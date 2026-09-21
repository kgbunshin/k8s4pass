#!/usr/bin/env python3
"""End-to-end test against an already running server (creates a real kind cluster).

    .venv/bin/python tests/e2e.py [http://127.0.0.1:8000]

Covers: origin defenses, session creation, terminal (user, kubectl, resize),
grading (deliberately solves ONE question), review (the environment stays alive, the terminal
keeps working and you can fix a question and regrade) and resource cleanup.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import qlib  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
WS = BASE.replace("http", "ws", 1)
PASSED = 0
DOMAIN = "application-environment-config-security"  # has exactly 3 questions: cfg-001, crd-001, crd-002


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASSED
    print(f"  {'✓' if cond else '✗'} {label}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)
    if not cond:
        sys.exit(f"FAILED: {label} {extra}")
    PASSED += 1


def http(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method, headers={"Content-Type": "application/json", **(headers or {})},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else {})


def sh(cmd: str, stdin: str | None = None, timeout: int = 120) -> tuple[int, str]:
    p = subprocess.run(["bash", "-c", cmd], input=stdin, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


async def read_until(ws, needles: list[bytes], timeout: float = 40) -> bytes:
    buf = b""
    end = time.monotonic() + timeout
    while time.monotonic() < end and not all(n in buf for n in needles):
        try:
            data = await asyncio.wait_for(ws.recv(), timeout=end - time.monotonic())
        except asyncio.TimeoutError:
            break
        buf += data if isinstance(data, bytes) else data.encode()
    return buf


async def terminal_checks(sid: str) -> None:
    print("terminal:")
    async with connect(f"{WS}/ws/terminal/{sid}?cols=100&rows=30", origin=BASE) as ws:
        await ws.send(b"echo RES-$((6*7)); id -un; kubectl get nodes; stty size; echo $KUBECONFIG\n")
        out = await read_until(ws, [b"RES-42", b"control-plane", b"30 100"])
        text = out.decode(errors="replace")
        check("RES-42" in text, "shell runs commands")
        check("ubuntu" in text, "runs as a non-root user (ubuntu)")
        check("control-plane" in text and "Ready" in text, "kubectl sees the session's cluster")
        check("30 100" in text, "initial PTY size came from the URL (30x100)")

        await ws.send(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
        await asyncio.sleep(0.5)
        await ws.send(b"stty size\n")
        out2 = (await read_until(ws, [b"40 120"], timeout=10)).decode(errors="replace")
        check("40 120" in out2, "live resize reaches the shell (SIGWINCH)")

        await ws.send(b"kubectl auth can-i --list >/dev/null 2>&1; docker ps 2>&1 | head -1; ls /var/run/docker.sock 2>&1 | head -1\n")
        out3 = (await read_until(ws, [b"No such file"], timeout=10)).decode(errors="replace")
        check("No such file" in out3, "shell has no access to the host docker socket")


async def review_terminal(sid: str) -> None:
    async with connect(f"{WS}/ws/terminal/{sid}?cols=100&rows=30", origin=BASE) as ws:
        await ws.send(b"echo REV-$((6*7)); kubectl get nodes\n")
        text = (await read_until(ws, [b"REV-42", b"control-plane"])).decode(errors="replace")
        check("REV-42" in text and "control-plane" in text, "terminal is still connected to the cluster after grading")


async def ws_rejections(sid: str) -> None:
    print("WebSocket defenses:")
    for label, kwargs in [("without Origin", {}), ("with Origin from another site", {"origin": "http://evil.example"})]:
        try:
            async with connect(f"{WS}/ws/terminal/{sid}", **kwargs):
                check(False, f"WebSocket {label} should be rejected")
        except InvalidStatus as e:
            check(e.response.status_code == 403, f"WebSocket {label} rejected (403)", str(e.response.status_code))
    try:
        async with connect(f"{WS}/ws/terminal/doesnotexist", origin=BASE):
            check(False, "a nonexistent session should be rejected")
    except InvalidStatus as e:
        check(e.response.status_code in (403, 404), "WebSocket of a nonexistent session rejected")


def main() -> None:
    print("API:")
    code, cat = http("GET", "/api/catalog")
    check(code == 200 and cat["domains"], "catalog loads", str(code))

    code, _ = http("POST", "/api/sessions", {"count": 1}, {"Origin": "http://evil.example"})
    check(code == 403, "POST with Origin from another site is rejected (403)", str(code))
    code, _ = http("POST", "/api/sessions", {"count": 0})
    check(code == 422, "invalid count is rejected (422)", str(code))
    code, _ = http("POST", "/api/sessions", {"domain": "does-not-exist", "count": 1})
    check(code == 400, "a domain without questions is rejected (400)", str(code))

    code, s = http("POST", "/api/sessions", {"domain": DOMAIN, "count": 3, "seed": 1})
    check(code == 201 and s["status"] == "provisioning", "session created and provisioning", f"{code} {s}")
    sid = s["id"]
    print(f"  session {sid}")
    created = time.monotonic()
    try:
        steps = []
        while time.monotonic() - created < 240:
            _, s = http("GET", f"/api/sessions/{sid}")
            if not steps or steps[-1] != s["step"]:
                steps.append(s["step"])
                print(f"    … {s['step']} ({time.monotonic() - created:.0f}s)", flush=True)
            if s["status"] in ("ready", "failed"):
                break
            time.sleep(2)
        check(s["status"] == "ready", "session became ready", s.get("error") or s["status"])
        check(len(s["questions"]) == 3 and all(q["statement"] for q in s["questions"]), "3 statements delivered")
        check("result" not in s and "solutions" not in json.dumps(s) and "checks" not in json.dumps(s),
              "before finishing, no checks or solutions leak")
        check(s["expires_at"] and s["expires_at"] > s["server_time"] + 60 * 60, "~2h timer set")

        # MAX_SESSIONS=2: the 2nd fits, the 3rd does not. The extra one is deleted right after.
        code2, extra = http("POST", "/api/sessions", {"count": 1})
        try:
            check(code2 == 201, "2nd simultaneous session is accepted (MAX_SESSIONS=2)", str(code2))
            code_r, _ = http("POST", f"/api/sessions/{extra['id']}/regrade", {})
            check(code_r == 409, "regrading a session that is not finished yet is rejected (409)", str(code_r))
            code3, body3 = http("POST", "/api/sessions", {"count": 1})
            check(code3 == 429, "3rd session over the limit is rejected (429)", f"{code3} {body3}")
        finally:
            if code2 == 201:
                http("DELETE", f"/api/sessions/{extra['id']}")

        asyncio.run(ws_rejections(sid))
        asyncio.run(terminal_checks(sid))

        # Solve ONE question (cfg-001, imperative solution) through the student's shell.
        record = json.loads((ROOT / "data" / "sessions" / f"{sid}.json").read_text())
        seeds = record["seeds"]
        print("grading:")
        target = "cfg-001"
        check(target in seeds, f"{target} is in the session", str(list(seeds)))
        q = qlib.load(ROOT / "questions/application-environment-config-security/cfg-001.md").render(seeds[target])
        rc, out = sh(f"docker exec -i ckad-sh-{sid} bash -eu", stdin=q.solutions["imperative"])
        check(rc == 0, "solution applied through the student's shell", out[-200:])
        rc, out = sh(f"docker exec ckad-sh-{sid} kubectl -n {q.meta['namespaces'][0]} rollout status deploy/web --timeout=90s")
        check(rc == 0, "deployment rollout finished", out[-200:])

        t0 = time.monotonic()
        code, done = http("POST", f"/api/sessions/{sid}/finish")
        took = time.monotonic() - t0
        check(code == 200 and done["status"] == "finished", f"grading finished in {took:.0f}s", f"{code} {done}")
        r = done["result"]
        by_id = {x["id"]: x for x in r["questions"]}
        check(by_id[target]["points"] == by_id[target]["total"], f"{target} (solved) got full marks",
              str(by_id[target]["points"]))
        others = [x for i, x in by_id.items() if i != target]
        check(all(x["points"] < x["total"] for x in others), "the unsolved ones did not get full marks",
              str([(x['id'], x['points'], x['total']) for x in others]))
        check(all(x["solutions"] for x in r["questions"]), "solutions revealed after finishing")
        check(r["score"] == sum(x["points"] for x in r["questions"]) and 0 < r["percent"] < 66 and not r["passed"],
              "total score, percentage and failure (<66%) are consistent", f"{r['score']}/{r['total']} {r['percent']}%")
        code, again = http("POST", f"/api/sessions/{sid}/finish")
        check(code == 200 and again["status"] == "finished", "finish is idempotent")

        print("review (go back and fix):")
        check(done["alive"] and done["review_expires_at"] > done["server_time"] + 10 * 60,
              "environment stays alive with a review window set")
        _, clusters = sh("kind get clusters 2>/dev/null")
        _, containers = sh(f"docker ps -q --filter name=ckad-sh-{sid}")
        check(f"ckad-s-{sid}" in clusters.split() and containers, "cluster and shell container are still up after grading")
        check(r["attempt"] == 1 and len(r["history"]) == 1, "1st grading recorded as attempt 1")
        asyncio.run(review_terminal(sid))

        code, _ = http("POST", f"/api/sessions/{sid}/regrade", {"question": "does-not-exist"})
        check(code == 400, "regrading a nonexistent question is rejected (400)", str(code))

        fix = "crd-001"
        check(fix in seeds, f"{fix} is in the session", str(list(seeds)))
        before = by_id[fix]["points"]
        q2 = qlib.load(ROOT / "questions/application-environment-config-security/crd-001.md").render(seeds[fix])
        rc, out = sh(f"docker exec -i ckad-sh-{sid} bash -eu", stdin=next(iter(q2.solutions.values())))
        check(rc == 0, f"solution of {fix} applied through the shell (fixing after grading)", out[-200:])

        t0 = time.monotonic()
        code, rg = http("POST", f"/api/sessions/{sid}/regrade", {"question": fix})
        check(code == 200 and rg["status"] == "finished", f"single-question regrade finished in {time.monotonic() - t0:.0f}s", f"{code}")
        rr = rg["result"]
        now = {x["id"]: x for x in rr["questions"]}
        check(now[fix]["points"] == now[fix]["total"] > before, f"{fix} now gets full marks ({before} -> {now[fix]['points']})")
        check(now[target]["points"] == by_id[target]["points"] and all(now[i]["points"] == by_id[i]["points"] for i in now if i != fix),
              "the other questions did not change")
        check(rr["score"] == sum(x["points"] for x in rr["questions"]) and rr["score"] > r["score"], "total score recomputed",
              f"{r['score']} -> {rr['score']}")
        check(rr["attempt"] == 2 and [h["scope"] for h in rr["history"]] == ["all", fix]
              and rr["history"][0]["percent"] == r["percent"], "history keeps the 1st grading and the regrade")

        code, rg2 = http("POST", f"/api/sessions/{sid}/regrade", {})
        check(code == 200 and rg2["result"]["attempt"] == 3 and rg2["result"]["score"] == rr["score"],
              "regrade all (empty body) keeps the score and counts the attempt")

    finally:
        http("DELETE", f"/api/sessions/{sid}")
        print("cleanup:")
        gone = False
        for _ in range(45):
            _, containers = sh(f"docker ps -aq --filter name=ckad-sh-{sid}")
            _, clusters = sh("kind get clusters 2>/dev/null")
            if not containers and f"ckad-s-{sid}" not in clusters.split() and not (ROOT / "data/run" / sid).exists():
                gone = True
                break
            time.sleep(2)
        check(gone, "cluster, shell container and working folder removed when the session is deleted")

    code, _ = http("GET", f"/api/sessions/{sid}")
    check(code == 404, "DELETE removes the session (GET returns 404)")
    print(f"\n{PASSED} checks ok")


if __name__ == "__main__":
    main()
