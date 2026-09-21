"""WebSocket <-> `docker exec -it <shell> bash` bridge through a PTY."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import signal
import struct
import subprocess
import termios

from fastapi import WebSocket


def _resize(master: int, proc: subprocess.Popen, cols: int, rows: int) -> None:
    cols, rows = max(1, min(cols, 500)), max(1, min(rows, 200))
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    if proc.poll() is None:
        proc.send_signal(signal.SIGWINCH)  # the docker client re-reads the tty size


async def bridge(ws: WebSocket, container: str, cols: int = 80, rows: int = 24) -> None:
    loop = asyncio.get_running_loop()
    master, slave = pty.openpty()
    # Size set BEFORE starting docker, which reads the tty size on startup.
    cols, rows = max(1, min(cols, 500)), max(1, min(rows, 200))
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    # Popen instead of pty.fork(): the process has threads, and a bare fork with threads is fragile.
    proc = subprocess.Popen(
        ["docker", "exec", "-it", "-e", "TERM=xterm-256color", container, "bash"],
        stdin=slave, stdout=slave, stderr=slave, start_new_session=True, close_fds=True,
    )
    os.close(slave)

    output: asyncio.Queue[bytes] = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(master, 65536)
        except OSError:  # EIO: the shell exited
            data = b""
        output.put_nowait(data)
        if not data:
            loop.remove_reader(master)

    loop.add_reader(master, on_readable)

    async def pump_out() -> None:
        while True:
            data = await output.get()
            if not data:
                return
            await ws.send_bytes(data)

    async def pump_in() -> None:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                os.write(master, msg["bytes"])  # keystrokes
            elif msg.get("text"):
                ctl = json.loads(msg["text"])  # control message, e.g. {"type":"resize","cols":80,"rows":24}
                if ctl.get("type") == "resize":
                    _resize(master, proc, int(ctl["cols"]), int(ctl["rows"]))

    tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        try:
            loop.remove_reader(master)
        except Exception:  # noqa: BLE001 - already removed at EOF
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                await loop.run_in_executor(None, lambda: proc.wait(timeout=3))
            except subprocess.TimeoutExpired:
                proc.kill()
        os.close(master)
