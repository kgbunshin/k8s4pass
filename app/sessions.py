"""Exam sessions: one kind cluster per student, grading and cleanup.

Each session has a kind cluster (ckad-s-<id>), a shell container (ckad-sh-<id>)
on the kind network and a working directory at data/run/<id>. The student's shell runs
INSIDE the container; grading runs on the host, out of the student's reach.
"""
from __future__ import annotations

import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import qlib  # noqa: E402
import validate  # noqa: E402

QUESTIONS_DIR = ROOT / "questions"
DATA_DIR = ROOT / "data"
RUN_DIR = DATA_DIR / "run"
RECORDS_DIR = DATA_DIR / "sessions"

CLUSTER_PREFIX = "ckad-s-"
SHELL_PREFIX = "ckad-sh-"
SHELL_IMAGE = "ckad-lab-shell"

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "2"))
SESSION_MINUTES = float(os.environ.get("SESSION_MINUTES", "120"))
REVIEW_MINUTES = float(os.environ.get("REVIEW_MINUTES", "30"))  # the cluster stays alive after grading, so the student can go back and fix things
PASS_PERCENT = 66  # CKAD passing score
GRADE_RETRY_SECONDS = 20  # time for the state to converge (e.g. a rollout) before a check counts as failed
REAP_INTERVAL = 15
ACTIVE = {"provisioning", "ready", "grading"}


class CapacityError(Exception):
    pass


class StateError(Exception):
    pass


class _Cancelled(Exception):
    pass


def run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    return p.returncode, (p.stdout + p.stderr).strip()


def preflight() -> None:
    missing = [t for t in ("docker", "kind", "kubectl", "bash") if not shutil.which(t)]
    if missing:
        raise RuntimeError(f"tools missing from PATH: {', '.join(missing)}")
    rc, _ = run(["docker", "image", "inspect", SHELL_IMAGE], timeout=30)
    if rc != 0:
        raise RuntimeError(f"image '{SHELL_IMAGE}' is missing. Run: docker build -t {SHELL_IMAGE} app/shell")


@dataclass
class Session:
    id: str
    questions: list[qlib.Question]
    seeds: dict[str, int]
    created_at: float
    status: str = "provisioning"  # provisioning | ready | grading | finished | failed
    step: str = "Queued"
    error: str | None = None
    expires_at: float | None = None
    result: dict | None = None
    cancelled: bool = False
    alive: bool = True  # cluster + shell still exist (False after teardown)
    regrading: bool = False
    review_expires_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    teardown_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def holds_resources(self) -> bool:
        return self.status in ACTIVE or (self.status == "finished" and self.alive)

    @property
    def terminal_open(self) -> bool:
        return self.alive and self.status in ("ready", "grading", "finished")

    @property
    def cluster(self) -> str:
        return f"{CLUSTER_PREFIX}{self.id}"

    @property
    def shell(self) -> str:
        return f"{SHELL_PREFIX}{self.id}"

    @property
    def dir(self) -> Path:
        return RUN_DIR / self.id

    @property
    def external_kubeconfig(self) -> Path:
        return self.dir / "kubeconfig"

    @property
    def internal_kubeconfig(self) -> Path:
        return self.dir / "kubeconfig.internal"


class Manager:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.lock = threading.RLock()
        self.create_cluster_lock = threading.Lock()  # kind races itself when creating the 'kind' network in parallel
        self.stop = threading.Event()

    # ---- catalog ---------------------------------------------------------------

    def _load_questions(self) -> list[qlib.Question]:
        questions = []
        for path in qlib.discover([QUESTIONS_DIR]):
            try:
                q = qlib.load(path)
            except qlib.QuestionError as e:
                print(f"[catalog] skipped: {e}", file=sys.stderr)
                continue
            errors, _ = qlib.lint(q)
            if errors:
                print(f"[catalog] skipped {path.name}: {'; '.join(errors)}", file=sys.stderr)
                continue
            questions.append(q)
        return questions

    def catalog(self) -> list[dict]:
        domains: dict[str, dict] = {}
        for q in self._load_questions():
            d = domains.setdefault(q.meta["domain"], {"domain": q.meta["domain"], "count": 0, "topics": set()})
            d["count"] += 1
            d["topics"].add(q.meta["topic"])
        return [{**d, "topics": sorted(d["topics"])} for _, d in sorted(domains.items())]

    # ---- lifecycle ----------------------------------------------------------

    def create(self, domain: str | None, count: int, seed: int | None) -> Session:
        pool = [q for q in self._load_questions() if domain in (None, "", "all") or q.meta["domain"] == domain]
        if not pool:
            raise ValueError("no questions available for this domain")
        rng = random.Random(seed if seed is not None else secrets.randbits(32))
        picked = rng.sample(pool, min(max(count, 1), len(pool)))

        chosen: list[qlib.Question] = []
        seeds: dict[str, int] = {}
        used_ns: set[str] = set()
        for base in picked:
            start = rng.randrange(1_000_000)
            for attempt in range(20):  # keep drawing until the namespaces do not collide
                rendered = base.render(start + attempt)
                namespaces = set(rendered.meta["namespaces"])
                if not namespaces & used_ns:
                    used_ns |= namespaces
                    chosen.append(rendered)
                    seeds[base.id] = start + attempt
                    break
        if not chosen:
            raise ValueError("could not build the questions without namespace conflicts")

        with self.lock:
            active = sum(1 for x in self.sessions.values() if x.holds_resources)
            if active >= MAX_SESSIONS:
                raise CapacityError(f"maximum capacity of {MAX_SESSIONS} simultaneous sessions reached; try again later")
            s = Session(id=secrets.token_hex(8), questions=chosen, seeds=seeds, created_at=time.time())
            self.sessions[s.id] = s

        RUN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        s.dir.mkdir(mode=0o700)
        self._record(s)
        threading.Thread(target=self._provision, args=(s,), daemon=True, name=f"provision-{s.id}").start()
        return s

    def get(self, sid: str) -> Session:
        with self.lock:
            return self.sessions[sid]  # KeyError if it does not exist

    def _check_cancel(self, s: Session) -> None:
        if s.cancelled:
            raise _Cancelled

    def _provision(self, s: Session) -> None:
        try:
            s.step = "Creating the Kubernetes cluster (about 30s)"
            with self.create_cluster_lock:
                self._check_cancel(s)
                rc, out = run(["kind", "create", "cluster", "--name", s.cluster,
                               "--kubeconfig", str(s.external_kubeconfig), "--wait", "120s"], timeout=300)
            if rc != 0:
                raise RuntimeError(f"kind create cluster failed: {out[-300:]}")
            self._check_cancel(s)

            s.step = "Preparing the terminal"
            rc, out = run(["kind", "get", "kubeconfig", "--name", s.cluster, "--internal"], timeout=30)
            if rc != 0:
                raise RuntimeError(f"kind get kubeconfig failed: {out[-300:]}")
            s.internal_kubeconfig.write_text(out + "\n")
            s.internal_kubeconfig.chmod(0o644)  # the container user (uid 1000) needs to read it
            rc, out = run(["docker", "run", "-d", "--init", "--name", s.shell, "--network", "kind",
                           "--hostname", "exam", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                           "--memory", "256m", "--cpus", "0.5", "--pids-limit", "256",
                           "-v", f"{s.internal_kubeconfig}:/home/ubuntu/.kube/config:ro",
                           SHELL_IMAGE, "sleep", "infinity"], timeout=60)
            if rc != 0:
                raise RuntimeError(f"docker run failed: {out[-300:]}")
            self._check_cancel(s)

            s.step = "Applying the questions"
            env = {**os.environ, "KUBECONFIG": str(s.external_kubeconfig)}
            for q in s.questions:
                rc, out = validate.sh(q.setup, env, 120)
                if rc != 0:
                    raise RuntimeError(f"setup of {q.id} failed: {validate.tail(out)}")
                self._check_cancel(s)

            with s.lock:
                self._check_cancel(s)
                s.expires_at = time.time() + SESSION_MINUTES * 60  # the clock starts when it becomes ready
                s.status = "ready"
                s.step = "Ready"
        except _Cancelled:
            pass
        except Exception as e:  # noqa: BLE001 - any failure becomes status 'failed' for the student to see
            s.error = str(e)[:500]
            s.status = "failed"
            self._teardown(s)
        finally:
            if s.cancelled:
                self._teardown(s)

    def finish(self, sid: str) -> Session:
        s = self.get(sid)
        with s.lock:
            if s.status == "finished":
                return s
            if s.status != "ready":
                raise StateError(f"the session is '{s.status}', it cannot be graded right now")
            s.status = "grading"
        try:
            result = self._grade(s)
        except Exception:
            with s.lock:
                s.status = "ready"  # allow trying again
            raise
        with s.lock:
            s.result = result
            s.review_expires_at = time.time() + REVIEW_MINUTES * 60
            s.status = "finished"  # the environment stays alive (review window); the reaper tears it down later
        self._record(s)
        return s

    def regrade(self, sid: str, question_id: str | None = None) -> Session:
        """Regrade (one question or all) in the still-living environment. The exam score is the one
        from the 1st grading; regrades only go into the history."""
        s = self.get(sid)
        with s.lock:
            if s.status != "finished" or not s.alive or not s.result:
                raise StateError("this session's environment has already been shut down; it can no longer be regraded")
            if s.regrading:
                raise StateError("a grading is already in progress")
            targets = [q for q in s.questions if question_id in (None, q.id)]
            if not targets:
                raise ValueError("question not found in this session")
            s.regrading = True
        try:
            env = {**os.environ, "KUBECONFIG": str(s.external_kubeconfig)}
            graded = {q.id: self._grade_question(q, env) for q in targets}
            with s.lock:
                if not s.alive:  # torn down mid-grading: the checks would fail because of that, not because of the student
                    raise StateError("the environment was shut down during grading")
                r = s.result
                r["questions"] = [graded.get(x["id"], x) for x in r["questions"]]
                self._summarize(r)
                r["attempt"] += 1
                r["history"].append({"percent": r["percent"], "score": r["score"], "total": r["total"],
                                     "scope": question_id or "all"})
                s.review_expires_at = time.time() + REVIEW_MINUTES * 60
        finally:
            s.regrading = False
        self._record(s)
        return s

    def _grade_question(self, q: qlib.Question, env: dict) -> dict:
        results = validate.score(q, env, retry_seconds=GRADE_RETRY_SECONDS)
        return {
            "id": q.id,
            "domain": q.meta["domain"],
            "topic": q.meta["topic"],
            "points": validate.points(q, results),
            "total": q.total_points,
            "checks": [{"description": c.description, "points": c.points, "ok": ok}
                       for c, (ok, _) in zip(q.checks, results)],
            "solutions": q.solutions,
        }

    @staticmethod
    def _summarize(result: dict) -> None:
        result["score"] = sum(x["points"] for x in result["questions"])
        result["total"] = sum(x["total"] for x in result["questions"])
        result["percent"] = round(100 * result["score"] / result["total"]) if result["total"] else 0
        result["passed"] = result["percent"] >= PASS_PERCENT

    def _grade(self, s: Session) -> dict:
        env = {**os.environ, "KUBECONFIG": str(s.external_kubeconfig)}
        result: dict = {"questions": [self._grade_question(q, env) for q in s.questions], "attempt": 1}
        self._summarize(result)
        result["history"] = [{"percent": result["percent"], "score": result["score"], "total": result["total"],
                              "scope": "all"}]
        return result

    def destroy(self, sid: str) -> None:
        with self.lock:
            s = self.sessions.pop(sid)  # KeyError if it does not exist
        s.cancelled = True
        threading.Thread(target=self._teardown, args=(s,), daemon=True).start()

    def _teardown(self, s: Session) -> None:
        s.alive = False  # first of all: nothing new (terminal, regrade) may start in an environment being torn down
        with s.teardown_lock:  # idempotent: tearing down twice is harmless
            run(["docker", "rm", "-f", s.shell], timeout=60)
            run(["kind", "delete", "cluster", "--name", s.cluster,
                 "--kubeconfig", str(s.external_kubeconfig)], timeout=120)
            shutil.rmtree(s.dir, ignore_errors=True)

    # ---- public view ----------------------------------------------------------

    def view(self, s: Session) -> dict:
        d = {
            "id": s.id,
            "status": s.status,
            "step": s.step,
            "error": s.error,
            "server_time": time.time(),
            "expires_at": s.expires_at,
            "alive": s.alive,
            "review_expires_at": s.review_expires_at,
            "questions": [
                {"id": q.id, "domain": q.meta["domain"], "topic": q.meta["topic"],
                 "difficulty": q.meta["difficulty"], "points": q.total_points, "statement": q.statement}
                for q in s.questions
            ],
        }
        if s.result:
            d["result"] = s.result  # checks and solutions only show up after finishing (from the 1st grading on)
        return d

    def _record(self, s: Session) -> None:
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        doc = {"id": s.id, "created_at": s.created_at, "seeds": s.seeds, "result": s.result}
        (RECORDS_DIR / f"{s.id}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2))

    # ---- maintenance -------------------------------------------------------------

    def cleanup_orphans(self) -> None:
        """On startup, delete what a previous run left behind (state is in memory only)."""
        rc, out = run(["kind", "get", "clusters"], timeout=30)
        for name in out.splitlines() if rc == 0 else []:
            if name.startswith(CLUSTER_PREFIX):
                print(f"[cleanup] removing orphan cluster {name}", file=sys.stderr)
                run(["kind", "delete", "cluster", "--name", name], timeout=120)
        rc, out = run(["docker", "ps", "-aq", "--filter", f"name={SHELL_PREFIX}"], timeout=30)
        for cid in out.split() if rc == 0 else []:
            run(["docker", "rm", "-f", cid], timeout=60)
        shutil.rmtree(RUN_DIR, ignore_errors=True)

    def start_reaper(self) -> None:
        threading.Thread(target=self._reap_loop, daemon=True, name="reaper").start()

    def _reap_loop(self) -> None:
        while not self.stop.wait(REAP_INTERVAL):
            now = time.time()
            with self.lock:
                snapshot = list(self.sessions.values())
            for s in snapshot:
                try:
                    if s.status == "ready" and s.expires_at and now > s.expires_at:
                        print(f"[reaper] time is up, grading {s.id}", file=sys.stderr)
                        self.finish(s.id)
                    elif (s.status == "finished" and s.alive and not s.regrading
                          and s.review_expires_at and now > s.review_expires_at):
                        print(f"[reaper] review ended, tearing down the environment of {s.id}", file=sys.stderr)
                        s.alive = False
                        threading.Thread(target=self._teardown, args=(s,), daemon=True).start()
                    elif s.status in ("finished", "failed") and now - s.created_at > 3600 * 3:
                        self.destroy(s.id)  # drop it from memory
                except Exception as e:  # noqa: BLE001 - the reaper must not die
                    print(f"[reaper] error on {s.id}: {e}", file=sys.stderr)

    def shutdown(self) -> None:
        self.stop.set()
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        threads = []
        for s in sessions:
            s.cancelled = True
            t = threading.Thread(target=self._teardown, args=(s,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=120)


manager = Manager()
