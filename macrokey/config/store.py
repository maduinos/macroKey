"""Where profiles and settings live, and how older files are brought forward."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import SCHEMA_VERSION, Action, HostAction, Profile, default_profile

log = logging.getLogger(__name__)

APP_NAME = "MaduinosMacroKey"


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


def settings_path() -> Path:
    return config_dir() / "settings.json"


def legacy_bindings_path() -> Path:
    """The old single-file app's config, kept for one-time migration."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME / "bindings.json"
    return Path.home() / APP_NAME / "bindings.json"


def resolve_asset(path: str) -> Path:
    """Resolves a file path named by a host action.

    ``~`` expands and absolute paths pass through. A relative path is taken
    against the config directory, next to ``profile.json``, which is the only
    folder that is still there after an install; the repository no longer
    ships sample assets to point at.
    """
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else config_dir() / candidate


@dataclass
class Settings:
    port: str = ""             # empty means auto-detect
    auto_connect: bool = True
    led_enabled: bool = True
    agentpet_enabled: bool = True
    agentpet_socket: str = ""  # empty means the AgentPet default location
    recorder_min_gap_ms: int = 40
    theme: str = "system"

    @classmethod
    def load(cls) -> Settings:
        path = settings_path()
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


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
            return Profile.from_dict(migrate(data))
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
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.to_dict()
    payload["schema_version"] = SCHEMA_VERSION
    # Write-then-rename: a crash mid-save leaves the previous profile intact.
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Brings a stored profile up to the current schema.

    Each step is a separate ``if`` so upgrades chain: a v1 file passing through
    a future v3 codebase runs 1->2 and then 2->3.
    """
    version = int(data.get("schema_version", 0))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"profile schema v{version} is newer than this app supports "
            f"(v{SCHEMA_VERSION}). Update macroKey rather than downgrading the file."
        )
    if version == 0:
        # Pre-schema files were the flat binding list of the original app.
        return migrate_legacy_bindings(data.get("bindings", [])).to_dict()
    return data


def migrate_legacy_bindings(items: Any) -> Profile:
    """Converts the original app's ``bindings.json`` into a v1 profile.

    Each old binding pasted an image on a ``tab+N`` hotkey. That becomes a host
    action on the matching key's tap slot, and the device sends the token
    instead of the old Tab-based chord, which typed a stray tab into whatever
    window had focus.
    """
    profile = default_profile()
    if not isinstance(items, list):
        return profile

    for index, item in enumerate(items[: len(profile.layers[0].keys)]):
        if not isinstance(item, dict):
            continue
        image = str(item.get("image", "")).strip()
        if not image:
            continue
        token = profile.next_host_token()
        profile.host_actions[token] = HostAction(
            type="clipboard_image",
            name=str(item.get("name") or f"Macro {index + 1}"),
            params={
                "path": image,
                "paste": bool(item.get("paste", True)),
                "press_enter": bool(item.get("press_enter", True)),
            },
        )
        if item.get("enabled", True):
            profile.set_action(0, index, "tap", Action(kind="host", token=token))

    return profile
