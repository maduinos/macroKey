"""AgentPet state -> colour and effect.

The state vocabulary is AgentPet's event protocol v1, deliberately reused rather
than reinvented, so anything that already speaks to AgentPet can drive the strip
without a translation layer.
"""

from __future__ import annotations

from dataclasses import dataclass

Color = tuple[int, int, int]


@dataclass(frozen=True)
class LedScene:
    color: Color
    effect: str = "solid"
    period_ms: int = 0
    #: When set, the strip becomes a 0-100 progress bar instead of one colour.
    bar_percent: int | None = None

    def with_bar(self, percent: int) -> LedScene:
        return LedScene(self.color, self.effect, self.period_ms, max(0, min(100, percent)))


# Colours are pre-brightness: the device applies the global brightness and the
# current limiter afterwards, so these can stay saturated and readable.
STATE_SCENES: dict[str, LedScene] = {
    "idle": LedScene((0, 60, 70)),
    "sleeping": LedScene((0, 12, 45), "breathe", 4000),
    "thinking": LedScene((140, 60, 220), "breathe", 1600),
    "planning": LedScene((120, 70, 230), "breathe", 1600),
    "reading": LedScene((40, 110, 255)),
    "editing": LedScene((40, 220, 90)),
    "running": LedScene((0, 200, 200), "pulse", 900),
    "researching": LedScene((80, 80, 255), "breathe", 2000),
    "waiting": LedScene((255, 190, 0), "blink", 1200),
    "approval": LedScene((255, 120, 0), "blink", 400),
    "success": LedScene((40, 255, 90), "flash", 900),
    "warning": LedScene((255, 140, 0)),
    "error": LedScene((255, 30, 30), "blink", 500),
    "offline": LedScene((0, 0, 0)),
    "overloaded": LedScene((255, 0, 180), "pulse", 400),
}

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
        # Eight pixels make a genuinely readable progress bar, which is the best
        # use of a linear strip when a task reports how far along it is.
        scene = scene.with_bar(int(round(max(0.0, min(1.0, progress)) * 100)))
    return scene
