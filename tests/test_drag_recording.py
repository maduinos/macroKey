"""A drag is a press, movement, and a release -- not two clicks.

Mouse capture kept only the press and every button became a full click, so
recording a drag-and-drop produced "click, jump, click": the pointer arrived
where the drag ended and nothing was ever carried. The setting that turns mouse
capture on cites drag-and-drop as its reason, so this is the case it owes.
"""

from __future__ import annotations

from macrokey.config.model import MOUSE_MODES
from macrokey.recorder.events import KEY_DOWN, MOUSE_CLICK, MOUSE_MOVE, MOUSE_RELEASE, RawEvent
from macrokey.recorder.normalize import normalize, reduce_to_device_macro


def press(button: str, at: float) -> RawEvent:
    return RawEvent(kind=MOUSE_CLICK, token=button, at=at)


def release(button: str, at: float) -> RawEvent:
    return RawEvent(kind=MOUSE_RELEASE, token=button, at=at)


def move(dx: int, dy: int, at: float) -> RawEvent:
    return RawEvent(kind=MOUSE_MOVE, token="move", at=at, data=(dx, dy))


def modes(steps) -> list[str]:
    return [s["params"]["mode"] for s in steps if s["type"] == "mouse_button"]


# --------------------------------------------------------------- classifying --


def test_a_press_and_release_with_nothing_between_is_one_click() -> None:
    steps = normalize([press("left", 1.0), release("left", 1.05)])
    assert modes(steps) == ["click"]


def test_a_press_movement_release_is_a_drag() -> None:
    steps = normalize([press("left", 1.0), move(200, 60, 1.2), release("left", 1.5)])
    assert [step["type"] for step in steps] == [
        "mouse_button",
        "delay",
        "mouse_move",
        "delay",
        "mouse_button",
    ]
    assert modes(steps) == ["press", "release"]


def test_typing_during_a_hold_also_counts_as_a_drag() -> None:
    """Holding a button while pressing a key is a real gesture -- it is how a
    selection is extended in several editors -- and collapsing it to a click
    would replay the keystroke with nothing held."""
    steps = normalize(
        [
            press("left", 1.0),
            RawEvent(kind=KEY_DOWN, token="a", char="a", at=1.2),
            release("left", 1.4),
        ]
    )
    assert modes(steps) == ["press", "release"]


def test_a_release_with_no_press_is_dropped() -> None:
    """The button went down before capture started; replaying a bare release
    would let go of something the macro never took hold of."""
    assert normalize([release("left", 1.0)]) == []


def test_a_press_that_never_comes_back_up_is_stored_as_a_click() -> None:
    """Recording ended mid-drag. A stored press with no release would leave the
    button held down for good, with no step left that could let go."""
    steps = normalize([press("left", 1.0), move(90, 0, 1.2)])
    assert modes(steps) == ["click"]


def test_two_buttons_are_tracked_separately() -> None:
    steps = normalize(
        [
            press("left", 1.0),
            move(120, 0, 1.1),
            press("right", 1.2),
            release("right", 1.25),
            release("left", 1.4),
        ]
    )
    assert modes(steps) == ["press", "click", "release"]


# ------------------------------------------------------------- on the device --


def test_a_drag_compiles_to_device_records() -> None:
    steps = normalize([press("left", 1.0), move(300, -200, 1.2), release("left", 1.6)])
    macro = reduce_to_device_macro(steps)
    assert macro is not None
    kinds = [action.kind for action in macro]
    assert kinds[0] == "mouse_button" and kinds[-1] == "mouse_button"
    assert macro[0].mode == "press"
    assert macro[-1].mode == "release"
    assert "mouse_move" in kinds


def test_the_modes_survive_the_binary_round_trip() -> None:
    from macrokey.config import binary
    from macrokey.config.model import Action, default_profile

    profile = default_profile()
    profile.device_macros = [
        [
            Action(kind="mouse_button", button="left", mode="press"),
            Action(kind="mouse_move", dx=40, dy=-20),
            Action(kind="mouse_button", button="left", mode="release"),
        ]
    ]
    restored = binary.decode_profile(binary.encode_profile(profile))
    assert [step.mode for step in restored.device_macros[0] if step.kind == "mouse_button"] == [
        "press",
        "release",
    ]


def test_every_mode_encodes_to_a_distinct_byte() -> None:
    """Click used to be the only one, and `b` held a click count -- so the
    schema bump matters: an old profile's "1 click" is the new "press"."""
    assert sorted(MOUSE_MODES.values()) == [0, 1, 2]


# -------------------------------------------------------- the path stays true --


def test_a_split_move_really_does_travel_in_a_straight_line() -> None:
    """Clamping each axis on its own bends the path: 300,-200 came out as
    (127,-127), (127,-73), (46,0) -- the last leg horizontal. A drag selects
    whatever it is dragged across, so the path is the gesture."""
    macro = reduce_to_device_macro([{"type": "mouse_move", "params": {"dx": 300, "dy": -200}}])
    assert macro is not None
    assert sum(step.dx for step in macro) == 300
    assert sum(step.dy for step in macro) == -200
    for step in macro:
        assert -127 <= step.dx <= 127 and -127 <= step.dy <= 127
        # Every leg holds the overall direction: dy/dx stays near -200/300.
        assert abs(step.dy / step.dx - (-200 / 300)) < 0.05
