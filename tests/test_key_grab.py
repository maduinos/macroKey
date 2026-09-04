"""Reading one shortcut from the real keyboard.

The dangerous half is the exclusive grab: while it is held, the keyboard talks
to this process and to nothing else. So the tests here are mostly about the ways
out of it -- one key ends it, a key already held means it is never taken, and
stopping hands every device back.

Nothing here opens a real device. `_on_raw` is the whole state machine and takes
plain key codes, and `_open_devices` is pointed at fakes.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("evdev")

from evdev import ecodes  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from macrokey.config.keycodes import parse_hotkey  # noqa: E402
from macrokey.recorder import evdev_source  # noqa: E402
from macrokey.ui.key_grab import KeyGrab, canonical  # noqa: E402

PRESS, RELEASE, REPEAT = 1, 0, 2


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def grab(qt_app):
    widget = KeyGrab()
    # `start` is never called, so nothing was opened; the state machine below
    # only asks whether it is meant to be listening.
    widget.active = True
    return widget


@pytest.fixture
def captured(grab):
    seen: list[str] = []
    grab.captured.connect(seen.append)
    return seen


def key(grab, code: int, value: int) -> None:
    grab._on_raw(code, value)


# ------------------------------------------------------------- combinations --


def test_a_plain_key_is_a_combination_on_its_own(grab, captured) -> None:
    key(grab, ecodes.KEY_F13, PRESS)
    assert captured == ["f13"]


def test_the_modifiers_held_at_the_time_are_part_of_it(grab, captured) -> None:
    key(grab, ecodes.KEY_LEFTCTRL, PRESS)
    key(grab, ecodes.KEY_LEFTSHIFT, PRESS)
    key(grab, ecodes.KEY_LEFTALT, PRESS)
    key(grab, ecodes.KEY_1, PRESS)
    assert captured == ["ctrl+alt+shift+1"]


def test_a_released_modifier_is_no_longer_part_of_it(grab, captured) -> None:
    key(grab, ecodes.KEY_LEFTCTRL, PRESS)
    key(grab, ecodes.KEY_LEFTCTRL, RELEASE)
    key(grab, ecodes.KEY_A, PRESS)
    assert captured == ["a"]


def test_the_order_is_the_stored_one_not_the_order_pressed(grab, captured) -> None:
    key(grab, ecodes.KEY_LEFTSHIFT, PRESS)
    key(grab, ecodes.KEY_LEFTCTRL, PRESS)
    key(grab, ecodes.KEY_F5, PRESS)
    assert captured == ["ctrl+shift+f5"]


def test_shift_does_not_turn_the_key_into_its_glyph(grab, captured) -> None:
    """The kernel reports the key, not what the layout makes of it -- which is
    the whole reason this reads the kernel. shift+1 is a shortcut; "!" is not."""
    key(grab, ecodes.KEY_LEFTSHIFT, PRESS)
    key(grab, ecodes.KEY_1, PRESS)
    assert captured == ["shift+1"]


@pytest.mark.parametrize(
    "code",
    [ecodes.KEY_F13, ecodes.KEY_F24, ecodes.KEY_PRINT, ecodes.KEY_LEFTBRACE, ecodes.KEY_A],
)
def test_what_is_captured_is_something_the_pad_can_be_given(grab, captured, code) -> None:
    key(grab, ecodes.KEY_LEFTCTRL, PRESS)
    key(grab, code, PRESS)
    parse_hotkey(captured[0])


def test_a_held_key_repeating_is_not_a_second_press(grab, captured) -> None:
    key(grab, ecodes.KEY_A, PRESS)
    grab.active = True  # the real one has stopped by now; keep listening
    key(grab, ecodes.KEY_A, REPEAT)
    key(grab, ecodes.KEY_A, RELEASE)
    assert captured == ["a"]


def test_the_modifiers_show_the_shortcut_building_up(grab) -> None:
    seen: list[str] = []
    grab.progress.connect(seen.append)
    key(grab, ecodes.KEY_LEFTCTRL, PRESS)
    key(grab, ecodes.KEY_LEFTSHIFT, PRESS)
    key(grab, ecodes.KEY_LEFTSHIFT, RELEASE)
    key(grab, ecodes.KEY_LEFTCTRL, RELEASE)
    assert seen == ["ctrl+", "ctrl+shift+", "ctrl+", ""]


def test_one_key_ends_it(grab, captured) -> None:
    """The grab has to end itself: while it is exclusive, the keyboard reaches
    nothing else, so it must never wait for a second thing to happen."""
    key(grab, ecodes.KEY_A, PRESS)
    assert grab.active is False
    key(grab, ecodes.KEY_B, PRESS)
    assert captured == ["a"]


def test_a_key_with_no_keycode_to_send_still_arrives(grab, captured) -> None:
    """A laptop Fn, a media key, a hangeul key: the pad has no way to send
    these, and the dialog says so. Dropping them here would instead look like
    the key press was missed."""
    key(grab, ecodes.KEY_FN, PRESS)
    assert captured == ["fn"]


# ------------------------------------------------------------------ devices --


def fake_device(*, vendor=0x0001, product=0x0002, keys=(ecodes.KEY_A,), held=()):
    device = types.SimpleNamespace(
        path="/dev/input/eventX",
        info=types.SimpleNamespace(vendor=vendor, product=product),
        capabilities=lambda: {ecodes.EV_KEY: list(keys)},
        active_keys=lambda: list(held),
        grabbed=False,
        closed=False,
    )
    def grab_it():
        device.grabbed = True
    def ungrab_it():
        device.grabbed = False
    def close_it():
        device.closed = True
    device.grab = grab_it
    device.ungrab = ungrab_it
    device.close = close_it
    return device


@pytest.fixture
def devices(monkeypatch):
    """`evdev.list_devices()` / `InputDevice()` over a list of fakes."""
    opened: list = []

    def install(*fakes):
        paths = [f"/dev/input/event{index}" for index in range(len(fakes))]
        by_path = dict(zip(paths, fakes, strict=True))
        monkeypatch.setattr(
            evdev_source,
            "evdev",
            types.SimpleNamespace(
                list_devices=lambda: paths,
                InputDevice=lambda path: opened.append(by_path[path]) or by_path[path],
            ),
        )
        return opened

    return install


def test_the_keypads_own_node_is_never_read(devices, qt_app) -> None:
    """The pad is a USB keyboard like any other, so it turns up in the list --
    and reading it would let a pad key bind itself."""
    vendor, product = sorted(evdev_source.KEYPAD_USB_IDS)[0]
    pad = fake_device(vendor=vendor, product=product)
    keyboard = fake_device()
    devices(pad, keyboard)

    assert KeyGrab()._open_devices() == [keyboard]
    assert pad.closed is True


def test_things_that_are_not_keyboards_are_left_alone(devices, qt_app) -> None:
    mouse = fake_device(keys=(ecodes.BTN_LEFT,))
    keyboard = fake_device()
    devices(mouse, keyboard)

    assert KeyGrab()._open_devices() == [keyboard]
    assert mouse.closed is True


def test_the_keyboard_is_taken_exclusively(devices, qt_app) -> None:
    keyboard = fake_device()
    widget = KeyGrab()
    widget._devices = [keyboard]
    widget._take_exclusive()
    assert keyboard.grabbed is True
    assert widget.exclusive is True


def test_nothing_is_grabbed_while_a_key_is_already_down(devices, qt_app) -> None:
    """The desktop has seen that press. Taking its release away would leave the
    key held as far as the desktop is concerned -- a stuck ctrl."""
    keyboard = fake_device(held=(ecodes.KEY_LEFTCTRL,))
    widget = KeyGrab()
    widget._devices = [keyboard]
    widget._take_exclusive()
    assert keyboard.grabbed is False
    assert widget.exclusive is False


def test_stopping_hands_the_keyboard_back(devices, qt_app) -> None:
    keyboard = fake_device()
    widget = KeyGrab()
    widget._devices = [keyboard]
    widget._take_exclusive()
    widget.active = True

    finished: list[str] = []
    widget.finished.connect(finished.append)
    widget.stop()

    assert keyboard.grabbed is False
    assert keyboard.closed is True
    assert widget.exclusive is False
    assert finished == [""]


def test_canonical_spells_a_bare_set_of_modifiers(qt_app) -> None:
    assert canonical({"shift", "ctrl"}) == "ctrl+shift"
    assert canonical(set()) == ""
