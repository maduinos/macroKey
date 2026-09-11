"""Turns a raw capture into something worth replaying.

Replaying keystrokes exactly as typed is almost always wrong: it reproduces
human typing speed, splits a word into thirty events, and records the modifier
dance instead of the shortcut. This module folds all of that away.
"""

from __future__ import annotations

from typing import Any

from ..config import keycodes
from ..config.model import MACRO_MAX_RECORDS, TEXT_RUN_MAX, Action, ProfileError, macro_records
from .events import KEY_DOWN, KEY_UP, MOUSE_CLICK, MOUSE_MOVE, MOUSE_RELEASE, SCROLL, RawEvent

#: Gaps shorter than this are dropped rather than replayed.
DEFAULT_MIN_GAP_MS = 40
#: Gaps are rounded to this grid so a macro reads as intentional timing.
DELAY_QUANTUM_MS = 10
#: A pause longer than this is treated as the user thinking, not as timing.
MAX_DELAY_MS = 2000


def _produces_a_step(event: RawEvent) -> bool:
    """Whether this raw event becomes something replayable.

    Key releases and modifier presses do not: the first is bookkeeping and the
    second decorates the key that follows rather than standing on its own.
    """
    if event.kind == KEY_UP:
        return False
    if event.kind == KEY_DOWN:
        return event.token not in keycodes.MODIFIER_BITS
    return True


#: How far the pointer must travel between a press and its release before the
#: pair is a drag rather than a click, in whatever counts the mouse reports.
#:
#: Not zero, and this is the whole point. Nobody holds a mouse perfectly still
#: while clicking it, and a gaming mouse at 3200 dpi turns a hand tremor into
#: dozens of counts -- so "the pointer moved at all" classified essentially
#: every click as a drag, and the macro then held the button down and hauled
#: whatever was under it across the screen.
DRAG_MIN_TRAVEL = 48


def _mouse_modes(events: list[RawEvent]) -> tuple[dict[int, str], set[int]]:
    """Classifies each mouse button event, and finds the wobble to discard.

    A press and release with the pointer essentially still between them is a
    click, and is worth storing as one step. A press with real travel before its
    release is a drag: both halves have to survive, because the button must be
    down while the pointer moves. Collapsing every pair into a click made
    drag-and-drop impossible to record; treating any movement at all as a drag
    made every ordinary click into one.

    Returns the button modes and the indices of pointer moves that happened
    during a click -- the hand shaking on the button. Replaying those would
    shift everything after them by however far the hand drifted.
    """
    modes: dict[int, str] = {}
    wobble: set[int] = set()
    open_presses: dict[str, int] = {}

    def is_drag(start: int, end: int) -> bool:
        travel = 0
        for between in events[start + 1 : end]:
            if between.kind == MOUSE_MOVE:
                travel += abs(between.data[0] if between.data else 0)
                travel += abs(between.data[1] if len(between.data) > 1 else 0)
            elif _produces_a_step(between):
                # A keystroke, another button, the wheel. Deliberate, and it
                # only makes sense with the button still held.
                return True
        return travel >= DRAG_MIN_TRAVEL

    for index, event in enumerate(events):
        if event.kind == MOUSE_CLICK:
            open_presses[event.token] = index
            modes[index] = "press"
        elif event.kind == MOUSE_RELEASE:
            start = open_presses.pop(event.token, None)
            if start is None:
                modes[index] = "skip"  # a release for a press from before capture
            elif is_drag(start, index):
                modes[index] = "release"
            else:
                modes[start] = "click"
                modes[index] = "skip"
                wobble.update(
                    position
                    for position in range(start + 1, index)
                    if events[position].kind == MOUSE_MOVE
                )

    # A press whose release never arrived -- the recording ended mid-drag, or
    # the release was the click that stopped it. Replaying it as a press would
    # leave the button held down for good, with no macro step to let it go.
    for index in open_presses.values():
        modes[index] = "click"
    return modes, wobble


def normalize(
    events: list[RawEvent],
    *,
    min_gap_ms: int = DEFAULT_MIN_GAP_MS,
    keep_delays: bool = True,
    preserve_key_timing: bool = False,
) -> list[dict[str, Any]]:
    """Raw events -> a list of host action specs.

    ``preserve_key_timing`` keeps key holds as press / wait / release instead of
    collapsing every keystroke to a tap. That is what a game-style Shift+W hold
    needs; the default path still folds typing into text runs and treats
    modifiers as decorations on the next key, which is what shortcuts want.
    """
    steps: list[dict[str, Any]] = []
    held: list[str] = []
    text = ""
    last_at: float | None = None
    mouse_modes, wobble = _mouse_modes(events)
    # When preserving holds, gaps are the point -- a 10 s Shift+W must stay
    # 10 s. The ordinary cap treats long pauses as "thinking" and clips them.
    delay_cap_ms: int | None = None if preserve_key_timing else MAX_DELAY_MS

    def flush_text() -> None:
        nonlocal text
        if text:
            steps.append({"type": "text", "params": {"text": text}})
            text = ""

    def add_delay(at: float) -> None:
        nonlocal last_at
        if last_at is not None and keep_delays:
            gap_ms = int(round((at - last_at) * 1000))
            if gap_ms >= min_gap_ms:
                quantized = _quantize(gap_ms)
                if delay_cap_ms is not None:
                    quantized = min(delay_cap_ms, quantized)
                steps.append({"type": "delay", "params": {"ms": quantized}})
        last_at = at

    def emit_hotkey(token: str, *, mode: str = "click") -> None:
        params: dict[str, Any] = {"hotkey": token}
        if mode != "click":
            params["mode"] = mode
        steps.append({"type": "hotkey", "params": params})

    for position, event in enumerate(events):
        if event.kind == KEY_UP:
            if preserve_key_timing:
                flush_text()
                if event.token in keycodes.MODIFIER_BITS:
                    if event.token in held:
                        held.remove(event.token)
                        add_delay(event.at)
                        emit_hotkey(event.token, mode="release")
                    continue
                add_delay(event.at)
                emit_hotkey(event.token, mode="release")
                continue
            if event.token in held:
                held.remove(event.token)
            continue

        if event.kind == KEY_DOWN:
            if preserve_key_timing:
                # Each physical down is its own press, including modifiers.
                # Baking Shift into "shift+w" as a single click would press and
                # release both together and never hold them for the recorded
                # duration.
                flush_text()
                add_delay(event.at)
                if event.token in keycodes.MODIFIER_BITS:
                    if event.token not in held:
                        held.append(event.token)
                    emit_hotkey(event.token, mode="press")
                else:
                    emit_hotkey(event.token, mode="press")
                continue

            if event.token in keycodes.MODIFIER_BITS:
                # Modifiers are never steps of their own; they decorate the key
                # that follows, which is what the user actually meant.
                if event.token not in held:
                    held.append(event.token)
                continue

            if held:
                flush_text()
                add_delay(event.at)
                combo = "+".join([*held, event.token])
                steps.append({"type": "hotkey", "params": {"hotkey": combo}})
                continue

            if event.printable:
                if not text:
                    add_delay(event.at)
                else:
                    last_at = event.at
                text += event.char
                continue

            flush_text()
            add_delay(event.at)
            steps.append({"type": "hotkey", "params": {"hotkey": event.token}})
            continue

        if event.kind in (MOUSE_CLICK, MOUSE_RELEASE):
            # The button, not the position. Replaying the coordinates too would
            # mean a macro that clicks the right pixel only while nothing has
            # moved -- a different window position, a different resolution, a
            # second monitor, and it silently clicks something else. Buttons at
            # the current pointer are the part that stays true, and they are the
            # part the firmware can send by itself.
            mode = mouse_modes.get(position, "click")
            if mode == "skip":
                continue
            flush_text()
            add_delay(event.at)
            steps.append(
                {"type": "mouse_button", "params": {"button": event.token, "mode": mode}}
            )
            continue

        if event.kind == SCROLL:
            delta = event.data[1] if len(event.data) > 1 else 0
            if not delta:
                continue
            flush_text()
            add_delay(event.at)
            steps.append({"type": "mouse_wheel", "params": {"delta": int(delta)}})
            continue

        if event.kind == MOUSE_MOVE:
            if position in wobble:
                continue  # the hand shaking on a button, not a gesture
            dx = event.data[0] if event.data else 0
            dy = event.data[1] if len(event.data) > 1 else 0
            if not dx and not dy:
                continue
            # Relative, and that is the whole point: the pointer ends up the
            # same distance from where it started rather than at the same
            # screen coordinate, so the macro survives a window being moved.
            # It also means a drag has to begin from where you left the
            # pointer, which is the honest version of what was recorded.
            flush_text()
            add_delay(event.at)
            steps.append({"type": "mouse_move", "params": {"dx": int(dx), "dy": int(dy)}})
            continue

    flush_text()
    return steps


def _quantize(milliseconds: int) -> int:
    return int(round(milliseconds / DELAY_QUANTUM_MS)) * DELAY_QUANTUM_MS


#: Commands that answer with a password prompt. What gets typed next is the
#: password, and it is typed into a terminal like anything else -- a recording
#: running at the time captures it verbatim and stores it in the profile.
PROMPTING_COMMANDS = ("sudo", "su", "ssh", "scp", "sftp", "passwd", "gpg", "mysql", "psql")


def find_likely_secrets(steps: list[dict[str, Any]]) -> list[int]:
    """Indices of text steps that are probably a password being answered.

    The shape is specific: a command that prompts, the Enter that submits it,
    then text. Requiring the Enter matters -- without it every word typed after
    the string "sudo" appeared anywhere would be suspected, and a macro that
    types `sudo apt update` and nothing else would lose its own command.

    This is a guess, and it is meant to be. Getting it wrong costs a step that
    has to be recorded again; getting it wrong the other way puts a password in
    a file, which is what already happened once.
    """
    secrets: list[int] = []
    # idle -> saw the command -> submitted it -> the next text is the answer.
    state = "idle"
    for index, step in enumerate(steps):
        kind = step.get("type")
        if kind == "delay":
            continue  # a pause is exactly what a prompt looks like

        if kind == "text":
            if state == "submitted":
                secrets.append(index)
                state = "idle"
                continue
            state = "saw-command" if _runs_prompting_command(step) else "idle"
            continue

        if kind == "hotkey" and step.get("params", {}).get("hotkey") == "enter":
            state = "submitted" if state == "saw-command" else "idle"
            continue

        state = "idle"
    return secrets


def _runs_prompting_command(step: dict[str, Any]) -> bool:
    """Whether this typed text runs something that will ask for a password.

    The command has to be what is being run, not merely mentioned: first word,
    or straight after a shell separator. Otherwise `echo "use sudo for this"`
    would arm the detector and eat the next thing typed.
    """
    words = step.get("params", {}).get("text", "").lower().split()
    separators = ("|", "&&", ";", "||", "&")
    return any(
        word in PROMPTING_COMMANDS and (position == 0 or words[position - 1] in separators)
        for position, word in enumerate(words)
    )


def redact_secrets(steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drops probable passwords, returning the steps and how many went.

    Dropping rather than masking: a masked step would still be replayed, typing
    the wrong thing into a password prompt, and the point is that the secret
    never reaches storage at all.
    """
    secrets = set(find_likely_secrets(steps))
    if not secrets:
        return steps, 0
    kept = [step for index, step in enumerate(steps) if index not in secrets]
    return kept, len(secrets)


def summarize(steps: list[dict[str, Any]]) -> list[str]:
    """One readable line per step, shown before a recording is saved."""
    lines = []
    for step in steps:
        kind = step.get("type", "?")
        params = step.get("params", {})
        if kind == "hotkey":
            mode = params.get("mode", "click")
            verb = {"click": "press ", "press": "hold  ", "release": "let go"}.get(
                mode, "press "
            )
            lines.append(f"{verb} {params.get('hotkey')}")
        elif kind == "text":
            value = params.get("text", "")
            preview = value if len(value) <= 40 else value[:37] + "..."
            lines.append(f"type   {preview!r}")
        elif kind == "delay":
            lines.append(f"wait   {params.get('ms')} ms")
        elif kind == "mouse_button":
            mode = params.get("mode", "click")
            verb = {"click": "click ", "press": "hold  ", "release": "let go"}.get(mode, "click ")
            lines.append(f"{verb} {params.get('button')} button (where the pointer is)")
        elif kind == "mouse_wheel":
            delta = int(params.get("delta", 0))
            lines.append(f"scroll {'up' if delta > 0 else 'down'} {abs(delta)}")
        elif kind == "mouse_move":
            lines.append(
                f"move   pointer by {int(params.get('dx', 0)):+d}, "
                f"{int(params.get('dy', 0)):+d} (relative)"
            )
        elif "note" in step:
            lines.append(f"skip   {step['note']}")
        else:
            lines.append(kind)
    return lines


def reduce_to_device_action(steps: list[dict[str, Any]]) -> Action | None:
    """Returns a firmware-native action when the recording is simple enough.

    A single shortcut belongs in EEPROM: it then fires with no PC app running
    and no round trip. Anything longer stays a host action.
    """
    meaningful = [step for step in steps if step.get("type") != "delay"]
    if len(meaningful) != 1:
        return None
    step = meaningful[0]
    if step.get("type") != "hotkey":
        return None
    # A lone press or release is half a hold -- binding it to a pad key would
    # leave the key down (or try to release something that was never pressed).
    if step.get("params", {}).get("mode", "click") != "click":
        return None

    hotkey = step.get("params", {}).get("hotkey", "")
    try:
        keycodes.parse_hotkey(hotkey)
    except keycodes.KeyParseError:
        return None
    return Action(kind="key", hotkey=hotkey)


#: A device macro step holds its delay in units of 10 ms in a single byte.
MAX_DEVICE_DELAY_MS = 255 * 10


def text_to_runs(text: str) -> list[Action] | None:
    """Typed text as device text runs, or None when the pad cannot type it.

    The firmware types the characters out of the macro itself, so a run costs
    one header record plus one record per three characters. It used to be one
    3-byte key action per character: a single command line was 38 records and
    the whole pad held 273, so anything worth recording went to the host and
    stopped working the moment the app was closed.

    Longer than one run's 255 characters simply becomes several runs. Tabs,
    newlines and anything outside printable ASCII return None -- a Korean macro
    is a host macro, and pretending otherwise would type nothing at all.
    """
    if not text:
        return None
    try:
        return [
            Action(kind="text", text=text[offset : offset + TEXT_RUN_MAX])
            for offset in range(0, len(text), TEXT_RUN_MAX)
        ]
    except ProfileError:
        return None  # not printable ASCII; the host can still type it


class UnsupportedRecording(ProfileError):
    """A recording the pad cannot replay, and why.

    Separate from "it does not fit" because the two ask for different things
    from the person: one wants a shorter recording, the other wants a different
    one. Reported as a single sentence naming the step that stopped it.
    """


def compile_device_macro(
    steps: list[dict[str, Any]],
    *,
    max_records: int = MACRO_MAX_RECORDS,
    anchor_pointer: bool = False,
) -> list[Action]:
    """Compiles a whole recording into records the firmware can replay itself.

    The firmware has always been able to run a stored sequence, but nothing ever
    built one, so every recording longer than a single shortcut became a host
    action and stopped working the moment the desktop app was not running. A
    keyboard macro with pauses is exactly what the sequence format is for.

    Raises UnsupportedRecording when a step has no on-device equivalent, or when
    the result would be truncated: `max_records` mirrors MK_MACRO_MAX_RECORDS,
    past which the firmware stops replaying, and half a macro is worse than an
    honest refusal.

    The budget is counted in *records*, not steps -- a text run spans a header
    plus one record per three characters, and it is records the region runs out
    of.
    """
    if not steps:
        raise UnsupportedRecording("nothing was captured")

    compiled: list[Action] = []
    for step in steps:
        kind = step.get("type")
        params = step.get("params", {})

        if kind == "delay":
            milliseconds = int(params.get("ms", 0))
            if milliseconds <= 0:
                continue
            # Longer pauses become several steps rather than being clipped: the
            # timing is the point of recording it in the first place.
            while milliseconds > 0:
                slice_ms = min(milliseconds, MAX_DEVICE_DELAY_MS)
                compiled.append(Action(kind="delay", delay_ms=slice_ms))
                milliseconds -= slice_ms
            continue

        if kind == "hotkey":
            hotkey = params.get("hotkey", "")
            mode = params.get("mode", "click")
            try:
                keycodes.parse_hotkey(hotkey)
            except keycodes.KeyParseError as exc:
                raise UnsupportedRecording(
                    f"the keypad cannot send {hotkey!r}"
                ) from exc
            compiled.append(Action(kind="key", hotkey=hotkey, mode=mode))
            continue

        if kind == "consumer":
            usage = params.get("usage", "")
            if usage not in keycodes.CONSUMER_USAGES:
                raise UnsupportedRecording(f"unknown media key {usage!r}")
            compiled.append(Action(kind="consumer", usage=usage))
            continue

        if kind == "mouse_button":
            try:
                action = Action(
                    kind="mouse_button",
                    button=params.get("button", "left"),
                    mode=params.get("mode", "click"),
                )
                action.encode()
            except Exception as exc:  # noqa: BLE001 - unknown button
                raise UnsupportedRecording(
                    f"the keypad cannot press the {params.get('button', '?')!r} "
                    "mouse button"
                ) from exc
            compiled.append(action)
            continue

        if kind == "mouse_wheel":
            delta = int(params.get("delta", 0))
            # One signed byte on the wire; a bigger scroll becomes repeats.
            while delta:
                slice_delta = max(-127, min(127, delta))
                compiled.append(Action(kind="mouse_wheel", delta=slice_delta))
                delta -= slice_delta
            continue

        if kind == "mouse_move":
            compiled.extend(_split_move(int(params.get("dx", 0)), int(params.get("dy", 0))))
            continue

        if kind == "text":
            text = params.get("text", "")
            runs = text_to_runs(text)
            if runs is None:
                raise UnsupportedRecording(
                    "the keypad types printable ASCII only, and this recording "
                    "contains characters it cannot send"
                )
            compiled.extend(runs)
            continue

        raise UnsupportedRecording(f"a recorded {kind!r} step has no keypad equivalent")

    if not compiled:
        raise UnsupportedRecording("nothing in the recording reaches the keypad")

    # Fixed-position playback is explicit. By default a click happens at the
    # current pointer and movement is relative to it -- both are predictable on
    # different monitors and window layouts. Anchored recordings are made from
    # the top-left, so they must replay from there too. Even a button-only macro
    # needs the home in that mode because its recorded location was the corner;
    # wheel events are pointer-targeted too.
    if anchor_pointer and any(
        action.kind in ("mouse_move", "mouse_button", "mouse_wheel")
        for action in compiled
    ):
        compiled.insert(0, Action(kind="mouse_home"))

    used = macro_records(compiled)
    if used > max_records:
        raise UnsupportedRecording(
            f"the recording needs {used} records and one macro slot holds "
            f"{max_records}. Record it in shorter pieces."
        )
    return compiled


def reduce_to_device_macro(
    steps: list[dict[str, Any]],
    *,
    max_records: int = MACRO_MAX_RECORDS,
    anchor_pointer: bool = False,
) -> list[Action] | None:
    """`compile_device_macro`, for callers that only need to know whether."""
    try:
        return compile_device_macro(
            steps, max_records=max_records, anchor_pointer=anchor_pointer
        )
    except UnsupportedRecording:
        return None


def _split_move(dx: int, dy: int) -> list[Action]:
    """Pointer travel as steps of at most one signed byte per axis.

    Along the straight line between the ends, not one axis at a time and not
    each axis clamped on its own: both of those bend the path, and a drag
    selects whatever it is dragged across. The longer axis sets how many steps
    there are, and the shorter one is spread evenly over them, so every step
    keeps the gesture's direction.
    """
    span = max(abs(dx), abs(dy))
    if span == 0:
        return []
    count = -(-span // 127)  # ceiling division: steps needed for the long axis
    moves: list[Action] = []
    done_x = done_y = 0
    for index in range(1, count + 1):
        step_x = round(dx * index / count) - done_x
        step_y = round(dy * index / count) - done_y
        done_x += step_x
        done_y += step_y
        if step_x or step_y:
            moves.append(Action(kind="mouse_move", dx=step_x, dy=step_y))
    return moves
