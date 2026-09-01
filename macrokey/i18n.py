"""UI language selection.

Deliberately toolkit-free, like every module here except `ui/`: status and error
strings are built in the session and device layers and only later shown in a
window, so those layers have to be able to translate too.

English is the source language rather than a set of abstract keys. A missing
translation then degrades to a correct English sentence instead of to a bare
identifier, which matters because the translation table is edited by hand and
will always trail the code slightly.

The active language is process-wide and read at import time by nothing -- the
app sets it once during startup, before any window is built. Changing it later
does not retranslate widgets that already exist, which is why the editor asks
for a restart rather than pretending to switch live.
"""

from __future__ import annotations

import locale
import logging
import os
import sys

from .translations import TRANSLATIONS

log = logging.getLogger(__name__)

#: The setting value meaning "ask the operating system".
SYSTEM = "system"
#: What the UI falls back to: the language the source strings are written in.
SOURCE = "en"

#: Languages the menu offers, in menu order. `SYSTEM` first because it is the
#: default and the answer for most people.
LANGUAGES: tuple[str, ...] = (SYSTEM, "en", "ko")

#: A language names itself in its own language -- someone who has the UI in the
#: wrong language has to be able to find their own entry in the menu.
LANGUAGE_NAMES: dict[str, str] = {
    SYSTEM: "System default",
    "en": "English",
    "ko": "한국어",
}

_active: str = SOURCE
_table: dict[str, str] = {}


def _code_from_tag(tag: str) -> str | None:
    """The language part of a locale tag, when it is one we have.

    Accepts everything the platforms actually hand out: `ko_KR.UTF-8`, `ko-KR`,
    `Korean_Korea.949`, and a bare `ko`.
    """
    if not tag:
        return None
    head = tag.replace("-", "_").split(".")[0].split("_")[0].strip().lower()
    if head in TRANSLATIONS or head == SOURCE:
        return head
    # Windows can report a language name rather than a code.
    named = {"korean": "ko", "english": "en"}.get(head)
    return named


def system_language() -> str:
    """The OS UI language, or `SOURCE` when it is not one we translate to.

    Checked in the order each platform actually answers: the POSIX environment
    first (it is also honoured on Windows when someone sets it deliberately),
    then the Windows UI language, then whatever `locale` was configured with.
    """
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name)
        if value:
            # LANGUAGE is a colon-separated preference list.
            for tag in value.split(":"):
                code = _code_from_tag(tag)
                if code:
                    return code

    if sys.platform.startswith("win"):
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            code = _code_from_tag(locale.windows_locale.get(lcid, ""))
            if code:
                return code
        except (OSError, AttributeError, ValueError) as exc:
            log.debug("could not read the Windows UI language: %s", exc)

    try:
        tag = locale.getlocale()[0] or ""
        code = _code_from_tag(tag)
        if code:
            return code
    except ValueError as exc:
        log.debug("could not read the configured locale: %s", exc)

    return SOURCE


def resolve(code: str | None) -> str:
    """The concrete language a setting value means. Never `SYSTEM`."""
    if not code or code == SYSTEM:
        return system_language()
    return code if code in TRANSLATIONS or code == SOURCE else SOURCE


def set_language(code: str | None) -> str:
    """Makes `code` the language `tr` translates into, and returns what it chose.

    Called once at startup. Safe to call again -- it only swaps the table -- but
    windows already built keep the text they were built with.
    """
    global _active, _table
    _active = resolve(code)
    _table = TRANSLATIONS.get(_active, {})
    return _active


def active_language() -> str:
    """The concrete language in force, for tests and for the menu's check mark."""
    return _active


def language_name(code: str) -> str:
    """The menu label for a setting value."""
    return LANGUAGE_NAMES.get(code, code)


def tr(text: str) -> str:
    """The active language's version of `text`, or `text` itself.

    Named `tr` rather than the gettext `_` because `_` is already a throwaway
    variable in this package and a shadowed one would fail far from the cause.
    """
    return _table.get(text, text)
