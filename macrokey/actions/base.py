"""Host action contract and registry.

A new host action is a class with a ``run`` method and a ``@register`` line.
Nothing else in the codebase changes: the runner looks handlers up by name and
the UI enumerates :func:`registered_types` to build its picker.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..backends import ClipboardBackend, KeyboardBackend
from ..config import HostAction, Profile


class ActionError(RuntimeError):
    """Raised when a host action cannot run or fails partway."""


@dataclass
class ActionContext:
    """Everything a handler is allowed to touch."""

    profile: Profile
    keyboard: KeyboardBackend
    clipboard: ClipboardBackend
    mouse: Any = None  # MouseBackend, resolved lazily so headless imports stay cheap
    status: Callable[[str], None] = lambda message: None
    device: Any = None  # DeviceClient, or None when running headless
    # Set when the user asks to abort. Long handlers must check it between steps.
    cancel: threading.Event = field(default_factory=threading.Event)
    depth: int = 0

    def child(self) -> ActionContext:
        """Nested context for a sequence step, one level deeper."""
        if self.depth >= 4:
            raise ActionError("host action nesting is too deep")
        return ActionContext(
            profile=self.profile,
            keyboard=self.keyboard,
            clipboard=self.clipboard,
            status=self.status,
            device=self.device,
            cancel=self.cancel,
            depth=self.depth + 1,
        )


class HostActionHandler(ABC):
    """One kind of host action. Instances are cheap and created per run."""

    #: Registry key, matching ``HostAction.type``.
    type_name = ""
    #: Shown in the UI picker.
    label = ""

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    @abstractmethod
    def run(self, context: ActionContext) -> None:
        ...

    def describe(self) -> str:
        return self.label or self.type_name

    # -- small typed accessors so handlers stop repeating validation ----------

    def string(self, key: str, default: str = "") -> str:
        return str(self.params.get(key, default))

    def integer(self, key: str, default: int = 0) -> int:
        try:
            return int(self.params.get(key, default))
        except (TypeError, ValueError):
            return default

    def flag(self, key: str, default: bool = False) -> bool:
        return bool(self.params.get(key, default))


_REGISTRY: dict[str, type[HostActionHandler]] = {}


def register(type_name: str, label: str = "") -> Callable[[type], type]:
    def decorator(handler: type) -> type:
        handler.type_name = type_name
        handler.label = label or type_name
        _REGISTRY[type_name] = handler
        return handler

    return decorator


def registered_types() -> dict[str, str]:
    """``{type_name: label}`` for every registered handler."""
    return {name: handler.label for name, handler in sorted(_REGISTRY.items())}


def create(spec: HostAction) -> HostActionHandler:
    handler = _REGISTRY.get(spec.type)
    if handler is None:
        raise ActionError(
            f"unknown host action type {spec.type!r}. "
            f"Known types: {', '.join(sorted(_REGISTRY))}"
        )
    return handler(dict(spec.params))
