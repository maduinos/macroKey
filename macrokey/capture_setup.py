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

from .i18n import tr

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


# Windows keeps the same idea behind a different switch: "Enhance pointer
# precision", which is the third member of the SPI_GETMOUSE triple. Zero is
# off, and off is what flat means here. The first two members are the speed
# thresholds the curve uses; they are read back and written unchanged, so
# turning acceleration off does not quietly reset a tuned pointer speed.
_SPI_GETMOUSE = 0x0003
_SPI_SETMOUSE = 0x0004
_SPIF_UPDATEINIFILE = 0x01
_SPIF_SENDCHANGE = 0x02


def _windows_mouse_params() -> list[int] | None:
    """The SPI_GETMOUSE triple, or None where it cannot be read."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        values = (ctypes.c_int * 3)()
        ok = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            _SPI_GETMOUSE, 0, ctypes.byref(values), 0
        )
        return [values[0], values[1], values[2]] if ok else None
    except (OSError, AttributeError, ValueError) as exc:
        log.debug("could not read the Windows mouse parameters: %s", exc)
        return None


def pointer_accel_profile() -> str | None:
    """The desktop's pointer acceleration setting, or None if unreadable.

    Two platforms, one question. GNOME names its curves, so its own name is
    returned; Windows has a checkbox, so it reports "flat" or "enhanced" -- the
    caller only ever compares against "flat".

    None on a desktop that is not GNOME, anywhere gsettings is absent, and on
    macOS -- all of which mean the same thing here: there is nothing to offer,
    so say nothing rather than guess.
    """
    if sys.platform.startswith("win"):
        values = _windows_mouse_params()
        if values is None:
            return None
        return "flat" if values[2] == 0 else "enhanced"
    value = _gsettings("get", _ACCEL_SCHEMA, _ACCEL_KEY)
    return value.strip("'\"") if value else None


def pointer_accel_is_flat() -> bool:
    """True only when the profile is known to be flat. Unknown is not flat."""
    return pointer_accel_profile() == "flat"


def pointer_accel_can_be_flattened() -> bool:
    """Whether there is a setting here worth offering to change."""
    profile = pointer_accel_profile()
    return profile is not None and profile != "flat"


def pointer_accel_undo_hint() -> str:
    """How to put the setting back, in the words of the platform it is on.

    Part of the offer rather than a footnote: this changes how the mouse feels
    everywhere, so the dialog that asks has to show the way back before the
    answer, not after.
    """
    if sys.platform.startswith("win"):
        return (
            "    Settings > Bluetooth & devices > Mouse >\n"
            "    Additional mouse settings > Pointer Options >\n"
            "    Enhance pointer precision"
        )
    return "    gsettings set org.gnome.desktop.peripherals.mouse accel-profile 'default'"


def set_pointer_accel_flat() -> tuple[bool, str]:
    """Switches the desktop to flat pointer acceleration.

    The user's own setting, changed only on an explicit yes, and reversible from
    the same place -- so the message that offers it says how to put it back.
    """
    if sys.platform.startswith("win"):
        values = _windows_mouse_params()
        if values is None:
            return False, tr("could not change the pointer acceleration setting")
        try:
            import ctypes

            # Thresholds preserved, acceleration off. UPDATEINIFILE so it
            # survives a reboot, SENDCHANGE so open programs see it now.
            wanted = (ctypes.c_int * 3)(values[0], values[1], 0)
            ok = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
                _SPI_SETMOUSE,
                0,
                ctypes.byref(wanted),
                _SPIF_UPDATEINIFILE | _SPIF_SENDCHANGE,
            )
        except (OSError, AttributeError, ValueError) as exc:
            log.debug("could not set the Windows mouse parameters: %s", exc)
            ok = False
        if not ok:
            return False, tr("could not change the pointer acceleration setting")
        if not pointer_accel_is_flat():
            return False, tr("the pointer acceleration setting did not take")
        return True, tr("pointer acceleration is now flat")

    if _gsettings("set", _ACCEL_SCHEMA, _ACCEL_KEY, "flat") is None:
        return False, tr("could not change the pointer acceleration setting")
    if not pointer_accel_is_flat():
        return False, tr("the pointer acceleration setting did not take")
    return True, tr("pointer acceleration is now flat")
