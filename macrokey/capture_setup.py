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


# ------------------------------------------------- pointer acceleration ----
#
# The pad is a relative mouse: it sends "move this far", and the desktop decides
# how far that is on screen. With an adaptive acceleration curve that decision
# depends on how *fast* the deltas arrive, so a replayed gesture only lands
# where it was recorded if it is replayed at the speed it was made.
#
# The firmware does replay at that speed now, which is what makes the curve
# cancel rather than compound. A flat profile removes the variable entirely --
# the mapping is then a constant and replay is exact -- so it is worth offering,
# but it is a preference on someone's desktop and not ours to change quietly.

_ACCEL_SCHEMA = "org.gnome.desktop.peripherals.mouse"
_ACCEL_KEY = "accel-profile"


def _gsettings(*arguments: str) -> str | None:
    """Runs gsettings, or returns None where there is no such setting to read."""
    if sys.platform != "linux":
        return None
    try:
        done = subprocess.run(
            ["gsettings", *arguments],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def pointer_accel_profile() -> str | None:
    """The desktop's pointer acceleration profile, or None if unreadable.

    None on Windows, on a desktop that is not GNOME, and anywhere gsettings is
    absent -- all of which mean the same thing here: there is nothing to offer,
    so say nothing rather than guess.
    """
    value = _gsettings("get", _ACCEL_SCHEMA, _ACCEL_KEY)
    return value.strip("'\"") if value else None


def pointer_accel_is_flat() -> bool:
    """True only when the profile is known to be flat. Unknown is not flat."""
    return pointer_accel_profile() == "flat"


def pointer_accel_can_be_flattened() -> bool:
    """Whether there is a setting here worth offering to change."""
    profile = pointer_accel_profile()
    return profile is not None and profile != "flat"


def set_pointer_accel_flat() -> tuple[bool, str]:
    """Switches the desktop to flat pointer acceleration.

    The user's own setting, changed only on an explicit yes, and reversible from
    the same place -- so the message that offers it says how to put it back.
    """
    if _gsettings("set", _ACCEL_SCHEMA, _ACCEL_KEY, "flat") is None:
        return False, "could not change the pointer acceleration setting"
    if not pointer_accel_is_flat():
        return False, "the pointer acceleration setting did not take"
    return True, "pointer acceleration is now flat"
