"""Shared EEPROM macro-pool fill, as shown in the editor."""

from __future__ import annotations

from macrokey.config.model import MACRO_RECORD_CAPACITY, Action, macro_storage_usage


def test_an_empty_profile_is_fully_free() -> None:
    used, capacity, used_pct, free_pct = macro_storage_usage([])
    assert (used, capacity, used_pct, free_pct) == (0, MACRO_RECORD_CAPACITY, 0, 100)


def test_percentages_match_record_fill() -> None:
    macros = [[Action(kind="key", hotkey="a")] * 77]
    used, capacity, used_pct, free_pct = macro_storage_usage(macros)
    assert used == 77
    assert capacity == MACRO_RECORD_CAPACITY
    assert used_pct == round(100 * 77 / MACRO_RECORD_CAPACITY)
    assert free_pct == 100 - used_pct


def test_a_full_pool_reports_no_free_space() -> None:
    macros = [[Action(kind="key", hotkey="a")] * MACRO_RECORD_CAPACITY]
    used, capacity, used_pct, free_pct = macro_storage_usage(macros)
    assert (used, capacity, used_pct, free_pct) == (
        MACRO_RECORD_CAPACITY,
        MACRO_RECORD_CAPACITY,
        100,
        0,
    )


def test_text_runs_count_as_packed_records_not_actions() -> None:
    """Fifteen ASCII letters are a header plus five payload records, not 15."""
    macros = [[Action(kind="text", text="sudo apt update")]]
    used, _capacity, _used_pct, _free_pct = macro_storage_usage(macros)
    assert used == 6
