"""Where profiles and settings live, and how older files are brought forward."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..boards import board_by_id
from .model import SCHEMA_VERSION, Profile, default_profile

log = logging.getLogger(__name__)

APP_NAME = "MaduinosMacroKey"

# Profile saves can come from the recording worker while settings/profile edits
# are queued by the UI worker. Serialize write-then-rename inside one process;
# the temporary name also carries the PID so two app processes do not collide.
_WRITE_LOCK = threading.RLock()


def _temporary_path(path: Path) -> Path:
    """Same directory for atomic replace, unique across running app processes."""
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def config_dir() -> Path:
    override = os.environ.get("MACROKEY_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        return Path(base) / APP_NAME if base else Path.home() / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "macrokey"


def profile_path() -> Path:
    return config_dir() / "profile.json"


#: How many previous profiles to keep. One was not enough, and the way it
#: failed is the reason for the number: a pad that had lost its own profile was
#: adopted onto this computer, which correctly pushed the good profile into the
#: single backup slot -- and the next ordinary save, seventeen seconds later,
#: pushed it back out again. Anything that survives only until the next save is
#: not a recovery path for something nobody noticed happening.
PROFILE_BACKUP_GENERATIONS = 10


def profile_backup_path() -> Path:
    """The most recent backup. Older ones are this name plus `.1`, `.2`, ..."""
    return config_dir() / "profile.json.bak"


def profile_backup_paths() -> list[Path]:
    """Existing backups, newest first."""
    newest = profile_backup_path()
    candidates = [newest] + [
        newest.with_name(f"{newest.name}.{index}")
        for index in range(1, PROFILE_BACKUP_GENERATIONS)
    ]
    return [path for path in candidates if path.exists()]


def _rotate_backups() -> None:
    """Shifts every kept generation one step older, freeing the newest slot.

    Renames rather than copies, so this costs nothing and cannot half-write a
    generation. The oldest is dropped by being renamed over.
    """
    newest = profile_backup_path()
    for index in range(PROFILE_BACKUP_GENERATIONS - 1, 0, -1):
        target = newest.with_name(f"{newest.name}.{index}")
        source = newest if index == 1 else newest.with_name(f"{newest.name}.{index - 1}")
        if source.exists():
            try:
                source.replace(target)
            except OSError:
                log.warning("could not rotate profile backup %s", source, exc_info=True)


def settings_path() -> Path:
    return config_dir() / "settings.json"


def legacy_bindings_path() -> Path:
    """The old single-file app's config, kept for one-time migration."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME / "bindings.json"
    return Path.home() / APP_NAME / "bindings.json"


@dataclass
class Settings:
    port: str = ""             # empty means auto-detect
    auto_connect: bool = True
    recorder_min_gap_ms: int = 40
    #: Clicks, wheel, and pointer movement. Off by default: most macros are
    #: keyboard, and a recording that quietly picked up every stray pointer
    #: twitch spent its slot on desk noise. The editor checkbox turns it on for
    #: the recordings that do want it.
    recorder_capture_mouse: bool = False
    #: Fixed-position mouse playback is inherently display-dependent: it homes
    #: the pointer before recording and replay. Relative/current-pointer replay
    #: is the safe default; this opt-in exists for unchanged single-screen rigs.
    recorder_anchor_mouse: bool = False
    #: Keep key holds as press / wait / release with the recorded durations,
    #: instead of collapsing every keystroke to a tap. Off by default: ordinary
    #: shortcut and typing macros do not want human dwell times, and a held key
    #: costs several records per second of hold (delay steps max out at 2550 ms).
    recorder_preserve_key_timing: bool = False
    theme: str = "system"
    #: UI language: "system" to follow the OS, or a code from `i18n.LANGUAGES`.
    #: Read once at startup -- widgets keep the language they were built with,
    #: so the editor asks for a restart rather than retranslating in place.
    language: str = "system"
    #: When True, the editor will not offer the one-click capture fix again.
    #: Cleared automatically is not done: the person said "not now".
    capture_setup_declined: bool = False
    #: Same, for the offer to flatten pointer acceleration. It is a preference
    #: on someone's desktop, so a no stays no -- Help > Mouse macro accuracy
    #: asks again for anyone who changes their mind.
    pointer_accel_declined: bool = False
    #: Whether the one evidence-backed re-offer has been spent. The startup
    #: question is asked before there is anything to point at; this is the
    #: single follow-up allowed once a recording exists that the setting will
    #: actually move. One, not a reminder: a no that keeps being re-asked is
    #: not being respected.
    pointer_accel_evidence_shown: bool = False
    #: Keep the app current: check the project's releases at startup and, when
    #: there is a newer one, download and stage it (a restart applies it).
    #: `MACROKEY_NO_UPDATE=1` overrules this for a whole machine.
    auto_update_app: bool = True
    #: Bring the keypad up to the firmware this app ships whenever it is behind.
    #: The two versions have to agree about the profile layout, so a pad left on
    #: old firmware is the thing that silently stops matching the editor.
    auto_update_firmware: bool = True
    #: Board id of the pad last connected to, from `macrokey.boards`.
    #:
    #: Storage is a board property, and the profile file records nothing about
    #: which board it belongs to. Without this the app fell back to the smallest
    #: registered board whenever the cable was out -- reporting an RP2040's 32
    #: records as 10% of 308 rather than 0% of 21801, and, worse, refusing to
    #: record a macro past 308 records onto a pad with room for 21801. Empty
    #: until a pad has been seen once.
    last_board: str = ""

    @classmethod
    def load(cls) -> Settings:
        path = settings_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(data, dict):
            return cls()

        # Drop retired keys (agentpet_*, led_enabled) and malformed values
        # quietly. JSON itself being valid does not make `port: []` or
        # `capture_mouse: "yes"` safe to feed into startup code.
        loaded = cls()
        if isinstance(data.get("port"), str):
            loaded.port = data["port"]
        if isinstance(data.get("recorder_min_gap_ms"), int) and not isinstance(
            data["recorder_min_gap_ms"], bool
        ):
            gap = data["recorder_min_gap_ms"]
            if 0 <= gap <= 10_000:
                loaded.recorder_min_gap_ms = gap
        if isinstance(data.get("theme"), str):
            loaded.theme = data["theme"]
        if isinstance(data.get("language"), str):
            loaded.language = data["language"]
        board = data.get("last_board")
        # Validated against the registry, not merely against `str`: a board that
        # was dropped, or a hand-edited name, must not decide a blob layout.
        if isinstance(board, str) and board_by_id(board) is not None:
            loaded.last_board = board
        for field in (
            "auto_connect",
            "recorder_capture_mouse",
            "recorder_anchor_mouse",
            "recorder_preserve_key_timing",
            "capture_setup_declined",
            "pointer_accel_declined",
            "pointer_accel_evidence_shown",
            "auto_update_app",
            "auto_update_firmware",
        ):
            value = data.get(field)
            if isinstance(value, bool):
                setattr(loaded, field, value)
        return loaded

    def save(self) -> None:
        path = settings_path()
        with _WRITE_LOCK:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = _temporary_path(path)
            temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            _restrict(temporary)
            temporary.replace(path)
            _restrict(path)
            _restrict(path.parent, directory=True)


def _quarantine(path: Path, exc: Exception) -> Profile:
    """Puts an unreadable profile out of harm's way and returns the defaults."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    spoiled = path.with_name(f"{path.name}.unreadable-{stamp}")
    try:
        path.rename(spoiled)
        log.error("could not read %s (%s); kept a copy at %s", path, exc, spoiled)
    except OSError:
        log.error("could not read %s (%s), and could not set it aside", path, exc)
    return default_profile()


def load_profile() -> Profile:
    """Loads the stored profile, migrating older formats on the way in."""
    path = profile_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profile = Profile.from_dict(migrate(data))
            # Repairs a profile that leaked storage before slots were reclaimed
            # on re-record. Done on the way in rather than in ``__post_init__``
            # so that decoding a device blob stays a faithful round trip.
            profile.reclaim_storage()
            return profile
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            # Falling straight back to defaults loses the file on the next save:
            # the app starts up looking factory fresh, writes that over the
            # profile it could not read, and the bindings are gone with nothing
            # having reported a problem. Move it aside first so it is
            # recoverable, and say where it went.
            return _quarantine(path, exc)

    legacy = legacy_bindings_path()
    if legacy.exists():
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default_profile()
        profile = migrate_legacy_bindings(data)
        save_profile(profile)  # so the conversion happens exactly once
        return profile

    return default_profile()


def save_profile(profile: Profile) -> None:
    _write_profile(profile_path(), profile, keep_backup=True)


def export_profile(profile: Profile, path: str | Path) -> None:
    """Writes a portable profile copy without changing the app's stored one."""
    _write_profile(Path(path), profile, keep_backup=False)


def load_profile_file(path: str | Path) -> Profile:
    """Loads and validates an explicitly selected profile file."""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    profile = Profile.from_dict(migrate(data))
    profile.reclaim_storage()
    return profile


def _write_profile(path: Path, profile: Profile, *, keep_backup: bool) -> None:
    with _WRITE_LOCK:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = profile.to_dict()
        payload["schema_version"] = SCHEMA_VERSION
        # Write-then-rename: a crash mid-save leaves the previous profile intact.
        temporary = _temporary_path(path)
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        # A recording holds whatever was typed while it ran, verbatim, and it only
        # takes one macro started a moment too early to put a password in here. The
        # default 0644 made that readable by every account on the machine.
        _restrict(temporary)
        if keep_backup and path.exists():
            backup = profile_backup_path()
            backup_temporary = _temporary_path(backup)
            try:
                _rotate_backups()
                shutil.copy2(path, backup_temporary)
                _restrict(backup_temporary)
                backup_temporary.replace(backup)
                _restrict(backup)
            except OSError:
                # The atomic primary save is still more important than its recovery
                # copy. Keep going, but leave a useful trace in the always-on log.
                log.warning("could not update profile backup %s", backup, exc_info=True)
        temporary.replace(path)
        _restrict(path)
        if keep_backup:
            _restrict(path.parent, directory=True)


def _restrict(path: Path, *, directory: bool = False) -> None:
    """Owner-only. Best effort: a filesystem without modes must not stop a save."""
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        log.debug("could not restrict %s", path, exc_info=True)


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Brings a stored profile up to the current schema.

    Each step is a separate ``if`` so upgrades chain: a v1 file passing through
    a future v3 codebase runs 1->2 and then 2->3.
    """
    if not isinstance(data, dict):
        raise ValueError("profile must be a JSON object")
    version = int(data.get("schema_version", 0))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"profile schema v{version} is newer than this app supports "
            f"(v{SCHEMA_VERSION}). Update macroKey rather than downgrading the file."
        )
    if version == 0:
        # Pre-schema files were the flat binding list of the original app.
        return migrate_legacy_bindings(data.get("bindings", [])).to_dict()
    # Drop host_actions from the dict before from_dict; from_dict ignores them
    # too, but stripping here keeps migrate()'s return shape clean for tests.
    data = dict(data)
    data.pop("host_actions", None)
    return data


def migrate_legacy_bindings(items: Any) -> Profile:
    """Converts the original app's ``bindings.json`` into a pad-only profile.

    Old bindings pasted clipboard images via a desktop host action. That cannot
    run on the pad, so those keys are left empty (defaults stay for others).
    """
    profile = default_profile()
    if not isinstance(items, list):
        return profile

    for index, item in enumerate(items[: len(profile.keys)]):
        if not isinstance(item, dict):
            continue
        image = str(item.get("image", "")).strip()
        if not image:
            continue
        # Former clipboard_image bindings have no HID equivalent -- clear the
        # default hyper shortcut so the key does not surprise by doing something
        # unrelated to the image the user expected.
        if item.get("enabled", True):
            from .model import Action

            profile.set_action(index, "tap", Action())
            log.info(
                "legacy binding %d (image %s) cleared: pad-only mode has no clipboard actions",
                index + 1,
                image,
            )

    return profile
