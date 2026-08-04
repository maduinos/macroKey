"""Dispatches ``EV t=host`` tokens to the registered handlers."""

from __future__ import annotations

import logging
import threading

from ..backends import get_clipboard_backend, get_keyboard_backend
from ..config import HostAction, Profile
from .base import ActionContext, ActionError, create

log = logging.getLogger(__name__)


class HostActionRunner:
    """Runs one host action at a time, off the caller's thread.

    Serial reads happen on the device reader thread, so a handler must never run
    there: a handler that types for two seconds would stall every other event.
    """

    def __init__(self, profile: Profile, status=None, device=None) -> None:
        self.profile = profile
        self.device = device
        self._status = status or (lambda message: None)
        self._cancel = threading.Event()
        self._current: threading.Thread | None = None
        self._lock = threading.Lock()

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile

    def context(self) -> ActionContext:
        return ActionContext(
            profile=self.profile,
            keyboard=get_keyboard_backend(),
            clipboard=get_clipboard_backend(),
            status=self._status,
            device=self.device,
            cancel=self._cancel,
        )

    def cancel(self) -> None:
        self._cancel.set()

    def trigger(self, token: int) -> None:
        """Looks a token up in the profile and runs it."""
        spec = self.profile.host_actions.get(token)
        if spec is None:
            self._status(f"No host action bound to token {token}")
            return
        self.run(spec)

    def run(self, spec: HostAction) -> None:
        with self._lock:
            busy = self._current is not None and self._current.is_alive()
        # `stop` must get through even while something is running; that is the
        # entire point of the panic chord.
        if busy and spec.type != "stop":
            self._status(f"Busy, ignored {spec.describe()}")
            return

        thread = threading.Thread(
            target=self._run_blocking, args=(spec,), name="macrokey-action", daemon=True
        )
        with self._lock:
            if spec.type != "stop":
                self._cancel.clear()
                self._current = thread
        thread.start()

    def _run_blocking(self, spec: HostAction) -> None:
        try:
            create(spec).run(self.context())
        except ActionError as exc:
            self._status(f"{spec.describe()} failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - a bad handler must not kill the app
            log.exception("host action %s raised", spec.type)
            self._status(f"{spec.describe()} error: {exc}")
