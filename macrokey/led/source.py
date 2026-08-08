"""Local socket that accepts AgentPet event-protocol-v1 lines.

macroKey listens rather than subscribes. AgentPet's own socket is an inbox, not
a feed, so the workable direction is for anything with something to say --
AgentPet, a CI hook, a shell script -- to write one JSON object per line here.

That keeps macroKey useful even with AgentPet not installed: ``echo`` into the
socket and the strip reacts.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MAX_LINE_BYTES = 64 * 1024
MAX_CONNECTIONS = 8
SCHEMA_VERSION = 1

VALID_STATES = frozenset(
    {
        "idle",
        "sleeping",
        "thinking",
        "planning",
        "reading",
        "editing",
        "running",
        "researching",
        "waiting",
        "approval",
        "success",
        "warning",
        "error",
        "offline",
        "overloaded",
    }
)
VALID_SEVERITIES = frozenset({"debug", "info", "attention", "warning", "critical"})


@dataclass(frozen=True)
class ActivityEvent:
    state: str
    severity: str = "info"
    source: str = "external"
    title: str = ""
    progress: float | None = None

    @classmethod
    def from_json(cls, payload: dict) -> ActivityEvent:
        version = payload.get("schema_version", SCHEMA_VERSION)
        if int(version) != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {version}")

        state = str(payload.get("state", ""))
        # v0.1 senders used "working" before the vocabulary was split up.
        if state == "working":
            state = "running"
        if state not in VALID_STATES:
            raise ValueError(f"unknown state {state!r}")

        severity = str(payload.get("severity", "info"))
        if severity not in VALID_SEVERITIES:
            raise ValueError(f"unknown severity {severity!r}")

        progress = payload.get("progress")
        if progress is not None:
            progress = float(progress)
            if not 0.0 <= progress <= 1.0:
                raise ValueError("progress must be between 0 and 1")

        return cls(
            state=state,
            severity=severity,
            source=str(payload.get("source", "external")),
            title=str(payload.get("title", "")),
            progress=progress,
        )


def default_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        base = Path(runtime)
    else:
        # os.getuid is POSIX-only, and this used to be called unconditionally --
        # including from `macrokey state`, which checked for AF_UNIX support only
        # after computing the path. On Windows that raised AttributeError instead
        # of the message explaining what was unsupported.
        uid = getattr(os, "getuid", None)
        base = Path(f"/tmp/macrokey-{uid() if uid else 'user'}")  # noqa: S108
    return base / "macrokey" / "state.sock"


class ActivityEventServer:
    """Accepts one JSON object per line and hands each to a callback."""

    def __init__(
        self,
        on_event: Callable[[ActivityEvent], None],
        socket_path: Path | None = None,
        tcp_port: int | None = None,
    ) -> None:
        self._on_event = on_event
        self._socket_path = socket_path
        self._tcp_port = tcp_port
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.endpoint = ""

    def start(self) -> str:
        if self._thread is not None:
            return self.endpoint

        if self._tcp_port is not None or not hasattr(socket, "AF_UNIX"):
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Loopback only. This socket takes instructions; it must not be
            # reachable from the network.
            self._server.bind(("127.0.0.1", self._tcp_port or 0))
            self.endpoint = f"127.0.0.1:{self._server.getsockname()[1]}"
        else:
            path = self._socket_path or default_socket_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(stat.S_IRWXU)  # 0700: this user only
            if path.exists():
                path.unlink()
            self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server.bind(str(path))
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
            self.endpoint = str(path)

        self._server.listen(MAX_CONNECTIONS)
        self._server.settimeout(0.5)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._accept_loop, name="macrokey-state", daemon=True
        )
        self._thread.start()
        return self.endpoint

    def stop(self) -> None:
        self._stop.set()
        server, self._server = self._server, None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        if self._socket_path is not None and self._socket_path.exists():
            try:
                self._socket_path.unlink()
            except OSError:
                log.debug("could not remove %s", self._socket_path, exc_info=True)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            server = self._server
            if server is None:
                return
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(
                target=self._serve, args=(connection,), name="macrokey-state-conn", daemon=True
            ).start()

    def _serve(self, connection: socket.socket) -> None:
        connection.settimeout(5.0)
        buffer = b""
        try:
            while not self._stop.is_set():
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > MAX_LINE_BYTES:
                    return  # oversized frame: drop the connection, not memory
                while b"\n" in buffer:
                    raw, _, buffer = buffer.partition(b"\n")
                    self._handle(raw)
        except (TimeoutError, OSError):
            return
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def _handle(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            event = ActivityEvent.from_json(json.loads(text))
        except (ValueError, TypeError) as exc:
            log.debug("rejected event: %s", exc)
            return
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a bad consumer must not kill the server
            log.exception("state event handler raised")
