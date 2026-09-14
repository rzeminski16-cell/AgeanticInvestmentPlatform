"""The web process, run beside the worker so the gate pages can be walked and screenshotted."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import IO, Final

import httpx

from audit.driver.recorder import Recorder

__all__ = ["WebServer"]

_START_SECONDS: Final = 90.0


class WebServer:
    def __init__(self, *, recorder: Recorder, log_path: Path, port: int = 8000) -> None:
        self._recorder = recorder
        self._log_path = log_path
        self.port = port
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: IO[bytes] | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self._log_path.open("ab")
        self._process = subprocess.Popen(
            ["uv", "run", "aer", "serve", "--port", str(self.port)],
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            cwd=Path.cwd(),
            start_new_session=True,
        )
        started = time.monotonic()
        while time.monotonic() - started < _START_SECONDS:
            if self._process.poll() is not None:
                message = f"The web process exited at startup; see {self._log_path}."
                raise RuntimeError(message)
            try:
                if httpx.get(f"{self.base_url}/healthz", timeout=2.0).status_code == 200:
                    self._recorder.event("web.started", pid=self._process.pid, url=self.base_url)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1.0)
        message = f"The web process never answered /healthz within {_START_SECONDS:.0f}s."
        raise RuntimeError(message)

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        self._recorder.event("web.stopped", pid=process.pid)
        self._process = None
        handle = self._log_handle
        if handle is not None:
            handle.close()
            self._log_handle = None
