"""Host-side actions: the work the firmware is too small to do."""

from . import builtin  # noqa: F401 - importing registers the built-in handlers
from .base import (
    ActionContext,
    ActionError,
    HostActionHandler,
    Param,
    create,
    handler_class,
    register,
    registered_types,
)
from .runner import HostActionRunner

__all__ = [
    "ActionContext",
    "ActionError",
    "HostActionHandler",
    "HostActionRunner",
    "Param",
    "create",
    "handler_class",
    "register",
    "registered_types",
]
