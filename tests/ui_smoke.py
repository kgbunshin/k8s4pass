#!/usr/bin/env python3
"""UI smoke test in a headless Chrome of its own (temporary profile, via CDP).

    .venv/bin/python tests/ui_smoke.py [http://127.0.0.1:8000]

Walks the whole flow on the real front end: start a session, type into the xterm terminal
for real, switch questions, reload the page (resume the session), finish, review/regrade
and start a new session. Creates a real kind cluster. Screenshots in data/shots/.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parent.parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PORT = 9333
SHOTS = ROOT / "data" / "shots"
PASSED = 0


def check(cond: bool, label: str, extra: str = "") -> None:
    global PASSED
    print(f"  {'✓' if cond else '✗'} {label}" + (f"  [{extra}]" if extra and not cond else ""), flush=True)
    if not cond:
        raise AssertionError(f"{label} {extra}")
    PASSED += 1


class CDP:
    def __init__(self, ws) -> None:
        self.ws, self.n, self.pending, self.console = ws, 0, {}, []

    async def reader(self) -> None:
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m:
                fut = self.pending.pop(m["id"], None)
                if fut:
                    fut.set_result(m)
            elif m["method"] == "Runtime.consoleAPICalled":
                p = m["params"]
                self.console.append((p["type"], " ".join(str(a.get("value", a.get("description", ""))) for a in p["args"])))
            elif m["method"] == "Runtime.exceptionThrown":
                self.console.append(("exception", json.dumps(m["params"]["exceptionDetails"])[:500]))
            elif m["method"] == "Log.entryAdded":
                e = m["params"]["entry"]
                self.console.append((e["level"], f"{e['text']} {e.get('url', '')}"))

    async def call(self, method: str, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        m = await asyncio.wait_for(fut, 60)
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error']}")
        return m["result"]

    async def js(self, expr: str):
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError("js: " + json.dumps(r["exceptionDetails"])[:500])
        return r["result"].get("value")

    async def wait_for(self, expr: str, timeout: float = 60, label: str = "") -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if await self.js(expr):
                    return
            except RuntimeError:
                pass
            await asyncio.sleep(0.4)
        raise TimeoutError(f"waiting for: {label or expr}")

    async def shot(self, name: str) -> None:
        r = await self.call("Page.captureScreenshot", format="png")
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / name).write_bytes(base64.b64decode(r["data"]))

    async def type_line(self, text: str) -> None:
        await self.js("document.querySelector('.xterm-helper-textarea').focus()")
        for ch in text:
            await self.call("Input.dispatchKeyEvent", type="keyDown", key=ch, text=ch)
            await self.call("Input.dispatchKeyEvent", type="keyUp", key=ch)
        await self.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter",
                        windowsVirtualKeyCode=13, text="\r")
        await self.call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)

    async def terminal_text(self) -> str:
        return await self.js("document.querySelector('.xterm-rows').innerText") or ""


def visible(view: str) -> str:
    return f"!document.getElementById('view-{view}').hidden"


async def run(cdp: CDP) -> None:
    await cdp.call("Page.enable")
    await cdp.call("Runtime.enable")
    await cdp.call("Log.enable")
    await cdp.call("Emulation.setDeviceMetricsOverride", width=1280, height=800, deviceScaleFactor=1, mobile=False)
    await cdp.call("Page.navigate", url=BASE)

    print("start screen:")
    await cdp.wait_for(visible("start") + " && document.querySelectorAll('#domain option').length > 1", 15, "catalog")
    opts = await cdp.js("[...document.querySelectorAll('#domain option')].map(o => o.textContent)")
    check(len(opts) >= 2 and "All domains" in opts[0], "domain select loaded from the catalog", str(opts))
    total = int(re.search(r"\((\d+) questions\)", opts[0]).group(1))
    check(await cdp.js("document.getElementById('count').max") == str(total), "max questions = catalog total", str(total))
    await cdp.shot("1-inicio.png")

    print("session start:")
    await cdp.js("document.getElementById('start').click()")
    await cdp.wait_for(visible("wait"), 10, "wait screen")
    check(True, "wait screen appears")
    await cdp.shot("2-espera.png")
    await cdp.wait_for(visible("exam"), 150, "exam screen")
    check(True, "session became ready and the exam screen opened")

    tabs = await cdp.js("document.querySelectorAll('.qtab').length")
    body = await cdp.js("document.getElementById('qbody').innerText")
    check(tabs == 3, "3 question tabs", str(tabs))
    check("Question 1" in body and "points" in body, "statement of question 1 rendered")
    check(await cdp.js("document.querySelectorAll('#qbody code').length") > 0, "markdown: `code` became <code>")
    check(await cdp.js("document.querySelectorAll('#qbody li, #qbody p').length") > 1, "markdown: paragraphs/lists rendered")
    timer = await cdp.js("document.getElementById('timer').textContent")
    check(bool(re.fullmatch(r"0[12]:\d\d:\d\d", timer)) and timer >= "01:58:00", "~2h timer running", timer)

    print("terminal:")
    await cdp.wait_for("document.getElementById('term-overlay').hidden && !!document.querySelector('.xterm-rows')",
                       20, "terminal connected")
    check(True, "xterm mounted and WebSocket connected")
    await cdp.wait_for("document.querySelector('.xterm-rows').innerText.includes('exam:')", 20, "prompt")
    check(True, "simulator shell prompt ('exam:') appears")
    await cdp.type_line("k get nodes")
    await cdp.wait_for("document.querySelector('.xterm-rows').innerText.includes('control-plane')", 20, "kubectl output")
    check(True, "typing 'k get nodes' in xterm works (alias k + kubectl on the session's cluster)")
    await cdp.type_line("echo $do")
    await cdp.wait_for("document.querySelector('.xterm-rows').innerText.includes('--dry-run=client -o yaml')", 15, "$do")
    check(True, "the environment's $do variable is defined")

    await cdp.js("document.querySelector('.qtab[data-i=\"1\"]').click()")
    body2 = await cdp.js("document.getElementById('qbody').innerText")
    check("Question 2" in body2 and body2 != body, "switching tab shows question 2")
    check(await cdp.js("document.querySelector('.qtab.active').dataset.i") == "1", "active tab is highlighted")
    await cdp.shot("3-prova.png")

    print("reload the page (resume the session):")
    await cdp.call("Page.reload")
    await cdp.wait_for(visible("exam") + " && document.getElementById('term-overlay').hidden", 30, "exam after reload")
    check(True, "after F5 it goes straight back to the exam, with the terminal reconnected")
    await cdp.wait_for("document.querySelector('.xterm-rows').innerText.includes('exam:')", 20, "prompt after reload")
    await cdp.type_line("echo AFTER-RELOAD-$((40+2))")
    await cdp.wait_for("document.querySelector('.xterm-rows').innerText.includes('AFTER-RELOAD-42')", 15, "echo after reload")
    check(True, "terminal responds after the reload")

    print("finish and grade:")
    await cdp.js("document.getElementById('finish').click()")
    check(await cdp.js("document.getElementById('finish').textContent") == "Confirm grading?",
          "1st click only asks for confirmation (no window.confirm)")
    await cdp.js("document.getElementById('finish').click()")
    await cdp.wait_for("!document.getElementById('review').hidden", 150, "review panel")
    check(await cdp.js("!document.getElementById('view-exam').hidden"), "2nd click grades and stays on the exam screen (review)")
    big = await cdp.js("document.querySelector('#review .big').textContent")
    check(bool(re.fullmatch(r"\d+/\d+", big)), "score shown in points/total format", big)
    check(await cdp.js("document.querySelector('#review .badge').textContent") == "Failed", "solving nothing: Failed")
    check(await cdp.js("document.querySelectorAll('.qtab.todo').length") == 3, "the 3 tabs are marked as pending")
    check(await cdp.js("document.querySelectorAll('#qbody .checks li.no').length") > 0, "failed checks show up with ✗ on the open question")
    check(await cdp.js("document.querySelectorAll('#qbody details').length") >= 2, "solutions revealed in <details>")
    check(await cdp.js("document.getElementById('finish').hidden"), "finish button disappears in review")
    check((await cdp.js("document.getElementById('timer').textContent")).startswith("review 00:"), "timer becomes the review window")

    print("review beside the terminal:")
    side = await cdp.js("(() => { const a = document.getElementById('review').getBoundingClientRect();"
                        " const b = document.getElementById('terminal').getBoundingClientRect();"
                        " return a.right <= b.left + 1 && b.width > 300 && a.width > 200; })()")
    check(side, "review panel and terminal sit side by side")
    check(await cdp.js("document.getElementById('term-overlay').hidden"), "terminal stays connected after grading")
    await cdp.type_line("echo AFTER-GRADING-$((40+2))")
    await cdp.wait_for("document.querySelector('.xterm-rows').innerText.includes('AFTER-GRADING-42')", 15, "echo after grading")
    check(True, "you can type in the same terminal after grading")
    await cdp.shot("4-resultado.png")

    print("go back and fix:")
    await cdp.js("document.querySelector('.qtab[data-i=\"0\"]').click()")
    check("Question 1" in await cdp.js("document.getElementById('qbody').innerText"), "clicking the tab opens the question")
    check(await cdp.js("!!document.querySelector('#qbody [data-regrade]:not([data-regrade=\"\"])')"), "'Regrade this question' button present")
    await cdp.js("document.querySelector('#qbody [data-regrade]').click()")
    await cdp.wait_for("document.getElementById('review').innerText.includes('regrades')", 150, "regrade")
    check(await cdp.js("document.querySelector('.qtab.active').dataset.i") == "0", "regrading keeps the question open")
    check(await cdp.js("document.getElementById('term-overlay').hidden"), "regrading does not drop the terminal")
    check(await cdp.js("document.querySelectorAll('#qbody [data-regrade]:disabled').length") == 0, "buttons released after regrading")

    print("new session:")
    await cdp.js("document.querySelector('#review [data-action=again]').click()")
    await cdp.wait_for(visible("start"), 10, "back to the start")
    check(await cdp.js("localStorage.getItem('ckad-sid')") is None, "session forgotten by the browser")

    print("browser console:")
    bad = [(t, m) for t, m in cdp.console if t in ("error", "exception") and "favicon" not in m]
    check(not bad, "no errors/exceptions in the console during the whole flow", str(bad[:3]))


async def main() -> None:
    profile = Path(tempfile.mkdtemp(prefix="ckad-ui-"))
    chrome = subprocess.Popen(
        ["google-chrome", "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={profile}", f"--remote-debugging-port={PORT}", "--window-size=1280,800", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        target = None
        for _ in range(50):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2))
                target = next((p for p in pages if p["type"] == "page"), None)
                if target:
                    break
            except OSError:
                pass
            time.sleep(0.3)
        if not target:
            sys.exit("headless Chrome did not start")
        async with connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            cdp = CDP(ws)
            reader = asyncio.create_task(cdp.reader())
            try:
                await run(cdp)
            except Exception:
                try:
                    await cdp.shot("falha.png")
                    print("  console:", cdp.console[-8:])
                except Exception:  # noqa: BLE001
                    pass
                raise
            finally:
                reader.cancel()
        print(f"\n{PASSED} checks ok")
    finally:
        try:
            os.killpg(chrome.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        chrome.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
