"""The language layer, and the thing about it that fails silently.

`tr` returns its argument when the table has no entry, which is the right
behaviour at runtime -- a missing line shows English rather than a crash or a
bare key. It is the wrong behaviour to leave unchecked in a repository: a typo
in either the source string or the table produces a window that is silently
half-translated, and nothing anywhere reports it.

So the source is parsed and every `tr("...")` literal is compared against the
table.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from macrokey import i18n
from macrokey.translations import TRANSLATIONS

PACKAGE = pathlib.Path(i18n.__file__).parent


def _tr_literals() -> dict[str, list[str]]:
    """Every constant string passed to `tr`, mapped to where it was found.

    Implicit concatenation is already folded by the parser, so a call spread
    over several source lines arrives here as the one string it will be at
    runtime -- which is exactly what the table is keyed by.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "tr":
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    where = f"{path.relative_to(PACKAGE.parent)}:{node.lineno}"
                    found.setdefault(argument.value, []).append(where)
    return found


def test_the_source_actually_calls_tr() -> None:
    """Guards the guard: an empty scan would make every check below vacuous."""
    assert len(_tr_literals()) > 100


#: Keys reached as `tr(variable)` rather than `tr("literal")`, so the scan
#: above cannot see them. Listed rather than skipped: the point of the orphan
#: check is that nothing gets into the table by accident, and a name here is a
#: claim someone can go and verify.
DYNAMIC_KEYS = frozenset(
    {
        # ui/app.py and ui/slot_dialog.py: tr(gesture) / tr(g.title()).
        "tap",
        "double",
        "Tap",
        "Double",
        # ui/describe.py picks one of these two by count.
        "{others} key",
        "{others} keys",
    }
)


@pytest.mark.parametrize("language", sorted(TRANSLATIONS))
def test_every_translated_string_is_reachable_from_the_source(language: str) -> None:
    """A table entry no `tr` call asks for is a typo or a leftover."""
    literals = _tr_literals()
    orphans = sorted(
        key
        for key in TRANSLATIONS[language]
        if key not in literals and key not in DYNAMIC_KEYS
    )

    assert orphans == []


@pytest.mark.parametrize("language", sorted(TRANSLATIONS))
def test_every_source_string_has_a_translation(language: str) -> None:
    """The half-translated window this whole module exists to prevent."""
    literals = _tr_literals()
    missing = sorted(text for text in literals if text not in TRANSLATIONS[language])

    assert missing == []


@pytest.mark.parametrize("language", sorted(TRANSLATIONS))
def test_translations_keep_every_placeholder(language: str) -> None:
    """A dropped `{name}` is a KeyError at the moment the message is shown.

    `.format()` tolerates a translation that uses fewer placeholders than it is
    given, so the failure is not the missing word -- it is the *added* one, and
    a renamed placeholder raises where nobody is looking.
    """
    import string

    def names(text: str) -> set[str]:
        return {
            field
            for _literal, field, _spec, _conversion in string.Formatter().parse(text)
            if field
        }

    wrong = {
        source: (names(source), names(translated))
        for source, translated in TRANSLATIONS[language].items()
        if not names(translated) <= names(source)
    }

    assert wrong == {}


def test_a_missing_entry_falls_back_to_english() -> None:
    i18n.set_language("ko")
    try:
        assert i18n.tr("this string is not in any table") == (
            "this string is not in any table"
        )
    finally:
        i18n.set_language("en")


def test_english_is_the_source_and_needs_no_table() -> None:
    i18n.set_language("en")
    assert i18n.tr("Connect") == "Connect"


def test_korean_translates() -> None:
    i18n.set_language("ko")
    try:
        assert i18n.tr("Connect") == "연결"
    finally:
        i18n.set_language("en")


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("ko_KR.UTF-8", "ko"),
        ("ko-KR", "ko"),
        ("ko", "ko"),
        ("Korean_Korea.949", "ko"),
        ("en_US.UTF-8", "en"),
        ("fr_FR.UTF-8", "en"),
        ("", "en"),
    ],
)
def test_system_language_reads_the_environment(monkeypatch, tag: str, expected: str) -> None:
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", tag)
    # A tag we do not translate must fall back rather than pick the last match.
    monkeypatch.setattr(i18n.locale, "getlocale", lambda *_a: (None, None))
    assert i18n.system_language() == expected


def test_resolve_maps_system_to_a_concrete_language(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "system_language", lambda: "ko")
    assert i18n.resolve("system") == "ko"
    assert i18n.resolve(None) == "ko"
    assert i18n.resolve("en") == "en"
    # Never hands back a code with no table behind it.
    assert i18n.resolve("de") == "en"


def test_settings_round_trip_the_language(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACROKEY_CONFIG_DIR", str(tmp_path))
    from macrokey.config import Settings

    assert Settings().language == "system"
    settings = Settings()
    settings.language = "ko"
    settings.save()
    assert Settings.load().language == "ko"
