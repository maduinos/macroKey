"""The host actions that ship with macroKey."""

from __future__ import annotations

import shlex
import subprocess
import time

from ..config.store import resolve_asset
from .base import ActionContext, ActionError, HostActionHandler, create, register

MAX_SEQUENCE_STEPS = 64
MAX_DELAY_MS = 10_000


@register("noop", "Do nothing")
class NoopAction(HostActionHandler):
    def run(self, context: ActionContext) -> None:
        context.status("noop")


@register("stop", "Stop the running host action")
class StopAction(HostActionHandler):
    """The panic button. Bound to a chord by default.

    Sets the shared cancel flag, which every long-running handler checks. It is
    dispatched on its own thread so it works while something else is mid-run.
    """

    def run(self, context: ActionContext) -> None:
        context.cancel.set()
        context.status("Stop requested")


@register("hotkey", "Send a keyboard shortcut")
class HotkeyAction(HostActionHandler):
    def run(self, context: ActionContext) -> None:
        hotkey = self.string("hotkey")
        if not hotkey:
            raise ActionError("hotkey action needs a 'hotkey' parameter")
        context.keyboard.tap_hotkey(hotkey, hold_ms=self.integer("hold_ms", 10))
        context.status(f"Sent {hotkey}")


@register("text", "Type text")
class TextAction(HostActionHandler):
    def run(self, context: ActionContext) -> None:
        text = self.string("text")
        if not text:
            raise ActionError("text action needs a 'text' parameter")
        context.keyboard.type_text(text)
        context.status(f"Typed {len(text)} characters")


@register("mouse_button", "Click a mouse button")
class MouseButtonAction(HostActionHandler):
    """Clicks where the pointer already is; see backends.mouse for why."""

    def run(self, context: ActionContext) -> None:
        button = self.string("button") or "left"
        clicks = self.integer("clicks", 1)
        _mouse(context).click(button, clicks)
        context.status(f"Clicked {button}")


@register("mouse_wheel", "Scroll")
class MouseWheelAction(HostActionHandler):
    def run(self, context: ActionContext) -> None:
        delta = self.integer("delta", 0)
        if not delta:
            return
        _mouse(context).scroll(delta)
        context.status(f"Scrolled {delta}")


@register("delay", "Wait")
class DelayAction(HostActionHandler):
    def run(self, context: ActionContext) -> None:
        milliseconds = max(0, min(MAX_DELAY_MS, self.integer("ms", 100)))
        # Waiting on the cancel event rather than sleeping keeps a long delay
        # interruptible by the stop action.
        context.cancel.wait(milliseconds / 1000)


@register("clipboard_image", "Copy an image and paste it")
class ClipboardImageAction(HostActionHandler):
    """The original app's feature, kept intact and made cross-platform."""

    def run(self, context: ActionContext) -> None:
        raw = self.string("path")
        if not raw:
            raise ActionError("clipboard_image action needs a 'path' parameter")
        path = resolve_asset(raw)
        if not path.exists():
            raise ActionError(f"image not found: {path}")

        context.clipboard.copy_image(path)
        context.status(f"Copied {path.name}")

        if self.flag("paste", True):
            time.sleep(0.05)  # let the clipboard owner change settle
            context.keyboard.tap_hotkey("ctrl+v")
            if self.flag("press_enter", True):
                time.sleep(0.05)
                context.keyboard.tap_hotkey("enter")


@register("shell", "Run a command")
class ShellAction(HostActionHandler):
    """Launches a command and does not wait for it.

    Fire-and-forget on purpose: a macro key that blocks until a program exits
    would freeze every other action behind it.
    """

    def run(self, context: ActionContext) -> None:
        command = self.params.get("command")
        if isinstance(command, str):
            command = shlex.split(command)
        if not command:
            raise ActionError("shell action needs a 'command' parameter")

        cwd = self.string("cwd") or None
        try:
            subprocess.Popen(  # noqa: S603 - the command is the user's own config
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            raise ActionError(f"could not run {command[0]!r}: {exc}") from exc
        context.status(f"Launched {command[0]}")


@register("layer", "Switch the device layer")
class LayerAction(HostActionHandler):
    def run(self, context: ActionContext) -> None:
        if context.device is None:
            raise ActionError("layer action needs a connected device")
        context.device.set_layer(self.integer("layer", 0))


@register("sequence", "Run several actions in order")
class SequenceAction(HostActionHandler):
    def run(self, context: ActionContext) -> None:
        from ..config import HostAction  # local import avoids a cycle

        steps = self.params.get("steps") or []
        if len(steps) > MAX_SEQUENCE_STEPS:
            raise ActionError(f"sequence is longer than {MAX_SEQUENCE_STEPS} steps")

        child = context.child()
        for index, step in enumerate(steps):
            if context.cancel.is_set():
                context.status(f"Sequence stopped at step {index + 1}")
                return
            create(HostAction.from_dict(step)).run(child)


def _mouse(context: ActionContext):
    """The mouse backend, created on first use rather than at import."""
    if context.mouse is None:
        from ..backends import get_mouse_backend

        context.mouse = get_mouse_backend()
    return context.mouse
