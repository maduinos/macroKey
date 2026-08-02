"""Putting an image on the system clipboard.

Every backend implements the same two-method surface, so the calling action does
not branch on the platform.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image
except ModuleNotFoundError:  # pragma: no cover - depends on the environment
    Image = None


class ClipboardError(RuntimeError):
    """Raised when the clipboard cannot be written on this system."""


class ClipboardBackend(ABC):
    name = "none"

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """Returns ``(usable, reason)``; the reason explains a False."""

    @abstractmethod
    def copy_image(self, path: Path) -> None:
        ...

    def require(self) -> None:
        usable, reason = self.available()
        if not usable:
            raise ClipboardError(reason)


class WindowsClipboard(ClipboardBackend):
    name = "windows"

    def available(self) -> tuple[bool, str]:
        if Image is None:
            return False, "Pillow is not installed: pip install Pillow"
        try:
            import win32clipboard  # noqa: F401
        except ModuleNotFoundError:
            return False, "pywin32 is not installed: pip install pywin32"
        return True, ""

    def copy_image(self, path: Path) -> None:
        self.require()
        import win32clipboard

        with Image.open(path) as image:
            buffer = BytesIO()
            image.convert("RGB").save(buffer, "BMP")
            # CF_DIB is a BMP without its 14-byte file header.
            data = buffer.getvalue()[14:]

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()


class _CommandClipboard(ClipboardBackend):
    """Shared implementation for backends that shell out to a helper tool."""

    tool = ""
    install_hint = ""

    def command(self, path: Path) -> list[str]:
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        if shutil.which(self.tool) is None:
            return False, f"{self.tool} not found. {self.install_hint}"
        return True, ""

    def copy_image(self, path: Path) -> None:
        self.require()
        data = path.read_bytes()
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            self.command(path), input=data, capture_output=True, check=False
        )
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise ClipboardError(f"{self.tool} failed: {message or result.returncode}")


class WaylandClipboard(_CommandClipboard):
    name = "wayland"
    tool = "wl-copy"
    install_hint = "Install wl-clipboard."

    def command(self, path: Path) -> list[str]:
        return [self.tool, "--type", _mime_type(path)]


class X11Clipboard(_CommandClipboard):
    name = "x11"
    tool = "xclip"
    install_hint = "Install xclip."

    def command(self, path: Path) -> list[str]:
        return [self.tool, "-selection", "clipboard", "-t", _mime_type(path), "-i"]


class MacClipboard(ClipboardBackend):
    name = "macos"

    def available(self) -> tuple[bool, str]:
        if shutil.which("osascript") is None:
            return False, "osascript not found"
        return True, ""

    def copy_image(self, path: Path) -> None:
        self.require()
        script = (
            f'set the clipboard to (read (POSIX file "{path}") as '
            f"«class {_osa_class(path)}»)"
        )
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["osascript", "-e", script], capture_output=True, check=False
        )
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise ClipboardError(f"osascript failed: {message}")


class UnsupportedClipboard(ClipboardBackend):
    name = "unsupported"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def available(self) -> tuple[bool, str]:
        return False, self.reason

    def copy_image(self, path: Path) -> None:
        raise ClipboardError(self.reason)


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
    }.get(suffix, "image/png")


def _osa_class(path: Path) -> str:
    return "JPEG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNGf"


def get_clipboard_backend() -> ClipboardBackend:
    if sys.platform.startswith("win"):
        return WindowsClipboard()
    if sys.platform == "darwin":
        return MacClipboard()
    if sys.platform.startswith("linux"):
        # Wayland first: a Wayland session often still advertises DISPLAY through
        # Xwayland, so checking WAYLAND_DISPLAY is the reliable order.
        if os.environ.get("WAYLAND_DISPLAY"):
            return WaylandClipboard()
        if os.environ.get("DISPLAY"):
            return X11Clipboard()
        return UnsupportedClipboard("no graphical session detected")
    return UnsupportedClipboard(f"no clipboard backend for {sys.platform}")
