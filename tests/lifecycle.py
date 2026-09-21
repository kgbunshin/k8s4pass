#!/usr/bin/env python3
"""Tests the operational lifecycle with a server of its own (port 8001, 30s sessions).

    .venv/bin/python tests/lifecycle.py

Covers: (A) the reaper grades on its own when time runs out, keeps the environment alive during the
review window (REVIEW_MINUTES=0.5) and only then tears it down;
(B) orphan clusters, left behind by a server that died without shutting down properly
(SIGKILL), are removed on the next startup.

WARNING: orphan cleanup deletes ALL ckad-s-* clusters. Stop any other ckad-lab
server before running this test.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8001
BASE = f"http://127.0.0.1:{PORT}"
PASSED = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASSED
    print(f"  {'✓' if cond else '✗'} {label}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)
    if not cond:
        raise AssertionError(f"{label} {extra}")
    PASSED += 1


def http(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {}


def sh(cmd: str) -> str:
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=120).stdout.strip()


def leftovers() -> tuple[list[str], list[str]]:
    clusters = [c for c in sh("kind get clusters 2>/dev/null").split() if c.startswith("ckad-s-")]
    containers = sh("docker ps -aq --filter name=ckad-").split()
    return clusters, containers


def start_server() -> subprocess.Popen:
    (ROOT / "data").mkdir(exist_ok=True)
    log = open(ROOT / "data" / "lifecycle-server.log", "ab")
    p = subprocess.Popen(
        [str(ROOT / ".venv/bin/uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT, env={**os.environ, "SESSION_MINUTES": "0.5", "REVIEW_MINUTES": "0.5", "MAX_SESSIONS": "2"}, stdout=log, stderr=log)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/api/catalog", timeout=2)
            return p
        except OSError:
            time.sleep(1)
    p.kill()
    sys.exit("test server did not start (see data/lifecycle-server.log)")


def wait_status(sid: str, wanted: set[str], timeout: float) -> dict:
    end = time.monotonic() + timeout
    s: dict = {}
    while time.monotonic() < end:
        _, s = http("GET", f"/api/sessions/{sid}")
        if s.get("status") in wanted | {"failed"}:
            return s
        time.sleep(2)
    return s


def main() -> None:
    if sh(f"ss -ltn 'sport = :{PORT}' | tail -n +2"):
        sys.exit(f"port {PORT} is in use")
    pre_clusters, _ = leftovers()
    if pre_clusters:
        sys.exit(f"there are ckad-s-* clusters running ({pre_clusters}); stop the other server first")

    server = start_server()
    try:
        print("A) time runs out and the reaper grades on its own:")
        code, s = http("POST", "/api/sessions", {"count": 2, "seed": 3})
        check(code == 201, "session created")
        sid = s["id"]
        s = wait_status(sid, {"ready"}, 150)
        check(s["status"] == "ready", "session ready", s.get("error") or s["status"])
        check(0 < s["expires_at"] - s["server_time"] <= 31, "~30s deadline set (SESSION_MINUTES=0.5)",
              str(s["expires_at"] - s["server_time"]))
        t0 = time.monotonic()
        s = wait_status(sid, {"finished"}, 180)  # without calling /finish
        check(s["status"] == "finished", f"graded automatically on expiry ({time.monotonic() - t0:.0f}s after ready)",
              s["status"])
        check(s.get("result") and s["result"]["total"] > 0 and len(s["result"]["questions"]) == 2,
              "complete result produced by the reaper")
        c, k = leftovers()
        check(s["alive"] and c and k, "environment is still alive right after grading (review window)", f"{c} {k}")
        check(0 < s["review_expires_at"] - s["server_time"] <= 31, "~30s review window set")
        gone = False
        for _ in range(60):
            c, k = leftovers()
            if not c and not k:
                gone = True
                break
            time.sleep(2)
        check(gone, "environment torn down when the review window ends")
        _, s = http("GET", f"/api/sessions/{sid}")
        check(s["status"] == "finished" and s["alive"] is False and s["result"]["total"] > 0,
              "the session can still be queried, with its result, but is marked as having no environment")
        code, _ = http("POST", f"/api/sessions/{sid}/regrade", {})
        check(code == 409, "regrading after the environment is shut down is rejected (409)", str(code))
        http("DELETE", f"/api/sessions/{sid}")

        print("B) server dies without shutting down properly (SIGKILL):")
        code, s2 = http("POST", "/api/sessions", {"count": 1, "seed": 5})
        check(code == 201, "another session created")
        s2 = wait_status(s2["id"], {"ready"}, 150)
        check(s2["status"] == "ready", "session ready", s2.get("error") or s2["status"])
        server.send_signal(signal.SIGKILL)
        server.wait()
        clusters, containers = leftovers()
        check(len(clusters) == 1 and len(containers) >= 2, "orphans were left behind (cluster + containers), as expected after SIGKILL",
              f"{clusters} {containers}")

        server = start_server()
        clusters, containers = leftovers()
        check(not clusters and not containers, "the next startup removed the orphans", f"{clusters} {containers}")
        check(not any((ROOT / "data" / "run").glob("*")) if (ROOT / "data" / "run").exists() else True,
              "data/run folder cleaned")
        code, _ = http("GET", f"/api/sessions/{s2['id']}")
        check(code == 404, "the dead server's session no longer exists (state is in memory only)")
        print(f"\n{PASSED} checks ok")
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)  # desligamento limpo derruba o que restar
            try:
                server.wait(timeout=90)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()
