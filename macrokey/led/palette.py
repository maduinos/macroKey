"""AgentPet state -> colour and effect.

The state vocabulary is AgentPet's event protocol v1, deliberately reused rather
than reinvented, so anything that already speaks to AgentPet can drive the strip
without a translation layer.
"""

from __future__ import annotations

from dataclasses import dataclass

Color = tuple[int, int, int]

#: A screen decodes an sRGB byte through roughly this exponent before any light
#: comes out. A WS2812B does not: its PWM is linear, so writing AgentPet's hex
#: straight to the pixel emits far more light than the same hex shows on screen,
#: and it does so most in the channels that were meant to stay low. The colour
#: washes out toward white and the hue stops reading -- `#8da2bf` is a muted
#: blue-grey on screen and a pale white dot on the pad. Undoing the transfer
#: curve puts the channel ratios back where the eye expects them.
GAMMA = 2.2


def srgb_to_linear(hex_rgb: str) -> Color:
    """Converts one of AgentPet's UI hex colours into WS2812B drive levels."""
    channels = (int(hex_rgb[i : i + 2], 16) for i in (0, 2, 4))
    return tuple(round(255 * (c / 255) ** GAMMA) for c in channels)  # type: ignore[return-value]


@dataclass(frozen=True)
class LedScene:
    color: Color
    effect: str = "solid"
    period_ms: int = 0
    #: When set, the strip becomes a 0-100 progress bar instead of one colour.
    bar_percent: int | None = None

    def with_bar(self, percent: int) -> LedScene:
        return LedScene(self.color, self.effect, self.period_ms, max(0, min(100, percent)))


# Colours come from AgentPet's Codex accent table (`agentpet/ui.py`,
# `_CODEX_STATE_ACCENTS`), which is the authority: the pet on screen and the pad
# on the desk describe the same run, so they must not disagree about what a
# colour means. They previously did -- macroKey had `running` cyan and `success`
# green, exactly the reverse of AgentPet, which is the one pair a person watches
# for most.
#
# Colours are pre-brightness; the device applies global brightness and the
# current limiter afterwards.
#
# Nothing here blinks. This sits in peripheral vision all day, and a hard on/off
# edge is what makes a status light something you end up covering with tape.
# AgentPet collapses several states onto one accent because its UI has a text
# label beside the colour; a single pixel does not, so the breathe period does
# that work instead -- calm states drift over 1.4-4 s, states that want an answer
# breathe at 0.6-0.9 s. RECORDING_SCENE below is the one thing that still
# pulses: it is a consent signal rather than ambience.
#
# The hex values below are copied verbatim from AgentPet so the two tables can
# be diffed by eye; `srgb_to_linear` is the only thing standing between them and
# the pixel. Edit the hex, never the converted value.
STATE_ACCENTS: dict[str, str] = {
    "idle": "8da2bf",
    "sleeping": "8da2bf",
    "thinking": "b58cff",
    "planning": "b58cff",
    "reading": "73b7ff",
    "researching": "73b7ff",
    "editing": "2dd4bf",
    "running": "52d273",
    "waiting": "ffbf5b",
    "approval": "ff9f43",
    "success": "22d3ee",
    "warning": "ffbf5b",
    "error": "ff667a",
    "overloaded": "ff667a",
}

#: state -> (effect, period_ms). Absent means a steady colour.
_MOTION: dict[str, tuple[str, int]] = {
    "sleeping": ("breathe", 4000),
    "thinking": ("breathe", 1600),
    "planning": ("breathe", 2400),
    "researching": ("breathe", 2000),
    "running": ("breathe", 900),
    "waiting": ("breathe", 1800),
    "approval": ("breathe", 700),
    "success": ("breathe", 1400),
    "warning": ("breathe", 1400),
    "error": ("breathe", 900),
    "overloaded": ("breathe", 600),
}

STATE_SCENES: dict[str, LedScene] = {
    state: LedScene(srgb_to_linear(accent), *_MOTION.get(state, ("solid", 0)))
    for state, accent in STATE_ACCENTS.items()
}

# AgentPet paints `offline` in the error accent. On screen that is fine -- the
# label beside it says which is which. Here it would leave a red glow on the desk
# that cannot be told apart from a real failure, so offline goes dark. It is the
# one place this table deliberately departs from AgentPet.
STATE_SCENES["offline"] = LedScene((0, 0, 0))

# Severity outranks state: an error during a `running` task must not look calm.
SEVERITY_SCENES: dict[str, LedScene] = {
    "critical": STATE_SCENES["error"],
    "warning": STATE_SCENES["warning"],
}

#: Shown while the recorder is capturing. Recording is never silent.
RECORDING_SCENE = LedScene((255, 0, 0), "pulse", 700)

#: Shown when nothing has reported in yet.
DEFAULT_SCENE = STATE_SCENES["idle"]


def scene_for(
    state: str | None,
    severity: str | None = None,
    progress: float | None = None,
) -> LedScene:
    """Picks the scene for one AgentPet event."""
    scene = SEVERITY_SCENES.get(severity or "")
    if scene is None:
        scene = STATE_SCENES.get(state or "", DEFAULT_SCENE)
    if progress is not None:
        # The device fills pixels in order and dims the leading one by the
        # remainder. On the one-pixel pad that degenerates to brightness: 30%
        # done is the state colour at 30% -- coarse, but it does read as motion
        # once a task is actually running. A strip would show a real bar.
        scene = scene.with_bar(int(round(max(0.0, min(1.0, progress)) * 100)))
    return scene
