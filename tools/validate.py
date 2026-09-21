#!/usr/bin/env python3
"""Validate questions against a test cluster.

For each question (and each seed): reset the namespaces, run the setup and confirm that
the checker does NOT give full marks; then, for each solution, reset + setup + solution
and confirm that the checker gives full marks.

Safety: only runs against a test context (kind-*/k3d-*) pointing to localhost, and
uses a temporary kubeconfig containing only that context, so no question script
can reach another cluster.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from qlib import Question, QuestionError, discover, is_protected_namespace, lint, load

ROOT = Path(__file__).resolve().parent.parent
SAFE_CONTEXT_PREFIXES = ("kind-", "k3d-")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
DEFAULT_CONTEXT = "kind-ckad-val"
SCRIPT_TIMEOUT = 120
CHECK_TIMEOUT = 30
RETRY_INTERVAL = 3


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def sh(script: str, env: dict, timeout: int, strict: bool = True) -> tuple[int, str]:
    # Checks run without -e/-u: the result is the exit code of the final command.
    cmd = ["bash", "-eu", "-c", script] if strict else ["bash", "-c", script]
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    return p.returncode, (p.stdout + p.stderr).strip()


def tail(text: str, n: int = 300) -> str:
    return " ".join(text[-n:].split())


def cluster_env(context: str, kubeconfig: Path | None, unsafe: bool) -> tuple[dict, str]:
    if not unsafe and not context.startswith(SAFE_CONTEXT_PREFIXES):
        die(f"context '{context}' does not look like a test context (expected prefix kind-/k3d-). "
            "Use --unsafe-allow-context to force it.")
    cmd = ["kubectl"] + (["--kubeconfig", str(kubeconfig)] if kubeconfig else [])
    cmd += ["config", "view", "--minify", "--flatten", "--context", context]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        die(f"could not read context '{context}': {p.stderr.strip()}")
    server = yaml.safe_load(p.stdout)["clusters"][0]["cluster"]["server"]
    host = urlparse(server).hostname
    if not unsafe and host not in LOCAL_HOSTS:
        die(f"the server of context '{context}' is '{server}', not localhost. "
            "Use --unsafe-allow-context to force it.")
    fd, tmp = tempfile.mkstemp(prefix="ckad-val-", suffix=".kubeconfig")  # 0600
    with os.fdopen(fd, "w") as f:
        f.write(p.stdout)
    env = {**os.environ, "KUBECONFIG": tmp}
    rc, out = sh("kubectl get nodes", env, 30)
    if rc != 0:
        os.unlink(tmp)
        die(f"cluster '{context}' unreachable: {tail(out)}")
    return env, tmp


def reset(namespaces: list[str], env: dict) -> None:
    for ns in namespaces:
        if is_protected_namespace(ns):  # extra defense; the lint already blocks this
            raise QuestionError(f"refusing to reset protected namespace: {ns}")
        q = shlex.quote(ns)
        # Pods with 'sleep' as PID 1 ignore SIGTERM and hold the namespace for 30s;
        # this is a disposable cluster, so force them out before deleting.
        sh(f"kubectl -n {q} delete pods --all --grace-period=0 --force --ignore-not-found >/dev/null 2>&1 || true; "
           f"kubectl delete namespace {q} --ignore-not-found --wait=true --timeout=120s",
           env, SCRIPT_TIMEOUT + 30)


def score(q: Question, env: dict, retry_seconds: int) -> list[tuple[bool, str]]:
    """Run the checks; failing ones are retried for up to `retry_seconds` (eventual state)."""
    results: list[tuple[bool, str]] = [(False, "")] * len(q.checks)
    deadline = time.monotonic() + retry_seconds
    while True:
        for i, c in enumerate(q.checks):
            if not results[i][0]:
                rc, out = sh(c.command, env, CHECK_TIMEOUT, strict=False)
                results[i] = (rc == 0, out)
        if all(ok for ok, _ in results) or time.monotonic() >= deadline:
            return results
        time.sleep(RETRY_INTERVAL)


def points(q: Question, results: list[tuple[bool, str]]) -> int:
    return sum(c.points for c, (ok, _) in zip(q.checks, results) if ok)


def failed_checks(q: Question, results: list[tuple[bool, str]]) -> list[str]:
    return [c.description for c, (ok, _) in zip(q.checks, results) if not ok]


@dataclass
class Outcome:
    qid: str
    seed: int
    errors: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    seconds: float = 0.0


def validate_one(q: Question, seed: int, env: dict) -> Outcome:
    r = q.render(seed)
    out = Outcome(q.id, seed)
    total = r.total_points
    nss = r.meta["namespaces"]
    retry = int(r.meta.get("check_timeout", 60))
    started = time.monotonic()
    try:
        reset(nss, env)
        rc, text = sh(r.setup, env, SCRIPT_TIMEOUT)
        if rc != 0:
            out.errors.append(f"setup failed (rc={rc}): {tail(text)}")
            return out
        results = score(r, env, retry_seconds=0)
        got = points(r, results)
        if got >= total:
            out.errors.append("the checker gives full marks without solving anything (checks too weak)")
        out.log.append(f"initial: {got}/{total} pts (expected < {total})")

        for name, script in r.solutions.items():
            reset(nss, env)
            rc, text = sh(r.setup, env, SCRIPT_TIMEOUT)
            if rc != 0:
                out.errors.append(f"setup failed before solution '{name}' (rc={rc}): {tail(text)}")
                continue
            rc, text = sh(script, env, SCRIPT_TIMEOUT)
            if rc != 0:
                out.errors.append(f"solution '{name}' failed (rc={rc}): {tail(text)}")
                continue
            results = score(r, env, retry_seconds=retry)
            got = points(r, results)
            out.log.append(f"solution '{name}': {got}/{total} pts")
            if got < total:
                detail = "; ".join(
                    f"{c.description} -> {tail(o, 120) or 'no output'}"
                    for c, (ok, o) in zip(r.checks, results) if not ok
                )
                out.errors.append(f"solution '{name}' does not reach full marks: {detail}")
    finally:
        reset(nss, env)
        out.seconds = time.monotonic() - started
    return out


def write_report(path: Path, rows: list[tuple[Outcome, Question]]) -> None:
    lines = ["## Validation report", "",
             "| Question | Seed | Result | Time | Details |", "|---|---|---|---|---|"]
    for o, _ in rows:
        status = "✅" if not o.errors else "❌"
        details = "<br>".join(o.log + [f"**{e}**" for e in o.errors]).replace("|", "\\|")
        lines.append(f"| `{o.qid}` | {o.seed} | {status} | {o.seconds:.0f}s | {details} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path, default=[ROOT / "questions"],
                    help="question files or folders (default: questions/)")
    ap.add_argument("--lint-only", action="store_true", help="only validate the format, no cluster")
    ap.add_argument("--strict", action="store_true", help="lint warnings become errors")
    ap.add_argument("--context", default=DEFAULT_CONTEXT, help=f"test kubectl context (default: {DEFAULT_CONTEXT})")
    ap.add_argument("--kubeconfig", type=Path,
                    help="kubeconfig (default: ckad-lab/.kubeconfig if it exists, otherwise kubectl's)")
    ap.add_argument("--seeds", type=int, default=1, help="how many variations of vars to test per question")
    ap.add_argument("--report", type=Path, help="write a markdown report (PR body)")
    ap.add_argument("--unsafe-allow-context", action="store_true",
                    help="allow a context that is not kind-/k3d-/localhost (NOT recommended)")
    args = ap.parse_args()

    files = discover(args.paths)
    if not files:
        die("no questions found")

    questions: list[Question] = []
    bad = 0
    for f in files:
        try:
            q = load(f)
        except QuestionError as e:
            print(f"✗ {e}")
            bad += 1
            continue
        errors, warnings = lint(q)
        for w in warnings:
            print(f"! {f.name}: {w}")
            if args.strict:
                errors.append(w)
        for e in errors:
            print(f"✗ {f.name}: {e}")
        if errors:
            bad += 1
        else:
            questions.append(q)
    print(f"lint: {len(questions)} ok, {bad} with errors")
    if bad:
        return 1
    if args.lint_only:
        return 0

    kubeconfig = args.kubeconfig or (ROOT / ".kubeconfig" if (ROOT / ".kubeconfig").exists() else None)
    env, tmp = cluster_env(args.context, kubeconfig, args.unsafe_allow_context)
    rows: list[tuple[Outcome, Question]] = []
    try:
        for q in questions:
            for seed in range(args.seeds):
                r = q.render(seed)
                vars_txt = " ".join(f"{k}={v}" for k, v in r.values.items())
                print(f"▶ {q.id} [{q.meta['domain']}/{q.meta['topic']}] seed={seed} {vars_txt}", flush=True)
                o = validate_one(q, seed, env)
                for line in o.log:
                    print(f"    {line}")
                for e in o.errors:
                    print(f"    ✗ {e}")
                print(f"    {'✓ ok' if not o.errors else '✗ FAILED'} ({o.seconds:.0f}s)", flush=True)
                rows.append((o, q))
    finally:
        os.unlink(tmp)

    failed = [o for o, _ in rows if o.errors]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} runs ok")
    if args.report:
        write_report(args.report, rows)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
