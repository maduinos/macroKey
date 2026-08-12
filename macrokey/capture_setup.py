"""One-shot prep so recording works under Wayland.

The pad itself needs nothing. Capture does: on Linux Wayland the only reliable
source is the kernel input nodes, which means the ``evdev`` package and read
access to ``/dev/input/event*``.

Package install can be done as the same user. Device access needs root once
(``pkexec`` / PolicyKit). After that, ``setfacl`` makes the current session
usable immediately; adding the account to ``input`` keeps it across logins.
"""

from __future__ import annotations

import getpass
import importlib
import logging
import os
import platform
import shlex
import subprocess
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureStatus:
    """What is missing for recording, if anything."""

    ok: bool
    package_ok: bool
    devices_ok: bool
    reason: str = ""


def status() -> CaptureStatus:
    """Live check against the recorder backends."""
    from .recorder.recorder import Recorder

    usable, reason = Recorder.available()
    if usable:
        return CaptureStatus(ok=True, package_ok=True, devices_ok=True)

    from .recorder import evdev_source

    package_ok = evdev_source.evdev is not None
    devices_ok = False
    if package_ok:
        try:
            devices_ok = bool(evdev_source.evdev.list_devices())
        except Exception:  # noqa: BLE001
            devices_ok = False
    return CaptureStatus(
        ok=False, package_ok=package_ok, devices_ok=devices_ok, reason=reason
    )


def needs_linux_capture_fix() -> bool:
    """True when this session will not capture without a setup step."""
    if platform.system() != "Linux":
        return False
    return not status().ok


def install_evdev() -> tuple[bool, str]:
    """Installs ``evdev`` into the running interpreter. No root required.

    Frozen builds ship ``evdev`` already; pip is not available inside the bundle.
    """
    from .recorder import evdev_source
    from .runtime import frozen

    if evdev_source.evdev is not None:
        return True, "already installed"

    if frozen():
        return False, (
            "this build is missing evdev — rebuild with ./build_release.sh"
        )

    cmd = [sys.executable, "-m", "pip", "install", "--user", "evdev"]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run pip: {exc}"

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return False, detail or f"pip exited {completed.returncode}"

    importlib.reload(evdev_source)
    if evdev_source.evdev is None:
        return False, "evdev installed, but this process still cannot import it — restart the app"
    return True, "installed"


def grant_input_access() -> tuple[bool, str]:
    """Adds the user to ``input`` and ACL-opens current event nodes.

    Needs a PolicyKit prompt (``pkexec``). Returns immediately-usable access when
    ``setfacl`` works; group membership still needs a new login to apply fully
    to brand-new device nodes after reboot, but the ACL covers this session.
    """
    if platform.system() != "Linux":
        return True, "not needed off Linux"

    user = getpass.getuser()
    # Quote once for the shell that pkexec runs as root.
    quoted = shlex.quote(user)
    script = (
        f"usermod -aG input {quoted} && "
        f"for e in /dev/input/event*; do "
        f"[ -e \"$e\" ] || continue; "
        f"setfacl -m u:{quoted}:rw \"$e\" 2>/dev/null || chmod g+rw \"$e\"; "
        f"done"
    )
    cmd = ["pkexec", "bash", "-c", script]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return False, (
            "pkexec is not available. Run once:\n"
            f"  sudo usermod -aG input {user}\n"
            "then log out and back in."
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if completed.returncode == 126 or "dismissed" in detail.lower():
            return False, "administrator prompt was cancelled"
        return False, detail or f"pkexec exited {completed.returncode}"

    return True, "input access granted for this session"


def fix_capture(*, grant_devices: bool = True) -> tuple[bool, str]:
    """Best-effort: install the package, then open device access if needed."""
    st = status()
    notes: list[str] = []

    if not st.package_ok:
        ok, message = install_evdev()
        notes.append(message)
        if not ok:
            return False, "; ".join(notes)

    st = status()
    if st.ok:
        return True, "; ".join(notes) or "recording is ready"

    if grant_devices and not st.devices_ok:
        ok, message = grant_input_access()
        notes.append(message)
        if not ok:
            return False, "; ".join(notes)
        st = status()

    if st.ok:
        return True, "; ".join(notes) or "recording is ready"
    return False, st.reason or "; ".join(notes)


def wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
