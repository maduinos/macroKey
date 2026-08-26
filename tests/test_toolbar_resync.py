"""The toolbar after the profile is replaced wholesale.

Brightness and typing speed are seeded once, when the toolbar is built. Pulling
the pad's profile swaps `app.profile` for a different object, so without a
resync the two widgets keep showing values from a profile that is no longer
loaded -- and typing speed reads "default" while the pad is on some other rate.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.config.model import Profile  # noqa: E402
from macrokey.ui.app import MainWindow  # noqa: E402


@pytest.fixture
def window():
    QApplication.instance() or QApplication([])
    return MainWindow()


def test_refresh_all_puts_the_loaded_profile_on_the_toolbar(window) -> None:
    window.app.profile = Profile(name="pad", brightness=200, text_speed_ms=40)

    window._refresh_all()

    assert window.text_speed.value() == 40
    assert window.brightness.value() == 200
    assert window.brightness_value.text() == "200"


def test_resync_is_not_read_back_as_an_edit(window) -> None:
    """Otherwise adopting the pad's profile would write it straight back."""
    pushed: list[str] = []
    window._apply = lambda what: pushed.append(what)
    window.app.profile = Profile(name="pad", brightness=200, text_speed_ms=40)

    window._refresh_all()

    assert pushed == []


def test_the_editor_shows_the_pads_own_rate_as_a_number(window) -> None:
    """A stored 0 is the firmware's 5 ms. It used to read "default", and
    stepping up from that gave 1 ms -- faster than what it had just left."""
    window.app.profile = Profile(name="pad", text_speed_ms=0)

    window._refresh_all()

    assert window.text_speed.value() == 5
    assert window.text_speed.minimum() == 1
    assert window.text_speed.specialValueText() == ""


def test_choosing_five_stores_the_zero_a_fresh_pad_holds(window) -> None:
    """Otherwise every new install would compare unequal to a flashed pad and
    prompt "Profile differs" over a difference that is not one."""
    applied: list[str] = []
    window._apply = lambda what: applied.append(what)
    window.app.profile = Profile(name="pad", text_speed_ms=40)
    window._refresh_all()  # the spinbox now reads 40, so 5 is a real change

    window.text_speed.setValue(5)

    assert window.app.profile.text_speed_ms == 0
    assert applied == ["Typing speed 5 ms per character"]


def test_one_millisecond_is_reachable_and_stored_as_itself(window) -> None:
    window._apply = lambda what: None
    window.app.profile = Profile(name="pad", text_speed_ms=0)
    window._refresh_all()

    window.text_speed.setValue(1)

    assert window.app.profile.text_speed_ms == 1
