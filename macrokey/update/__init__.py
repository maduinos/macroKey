"""Staying current: the keypad's firmware, and the app itself.

Two different updates with one rule between them -- **the firmware follows the
app**. The app carries an image for every board, so a pad is brought level with
whatever this build ships without touching the network; the network is what
brings a *newer app*, and the firmware inside it comes along for the ride. That
keeps the two versions that have to agree (the profile layout the app encodes
and the one the firmware decodes) moving together instead of separately.

`MACROKEY_NO_UPDATE=1` switches off everything in here that reaches the network.
"""

from __future__ import annotations

from . import firmware, selfupdate
from .releases import Release, UpdateError, checksums, enabled, is_newer, latest_release

__all__ = [
    "Release",
    "UpdateError",
    "checksums",
    "enabled",
    "firmware",
    "is_newer",
    "latest_release",
    "selfupdate",
]
