"""Which backend captured a recording, written down.

Two backends produce measurably different recordings from the same gesture --
one reads the kernel, the other reads what the desktop forwards -- and the
fallback is chosen silently. A recording that came back wrong therefore could
not be traced to the thing most likely to explain it: 190 `w press` records for
one held key, which is what a desktop auto-repeat looks like on the backend
that cannot tell one from a keystroke.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from macrokey.recorder.recorder import Recorder


def test_the_evdev_backend_says_so(caplog) -> None:
    recorder = Recorder()
    with (
        patch("macrokey.recorder.recorder.evdev_source.available", return_value=(True, "")),
        patch("macrokey.recorder.recorder.evdev_source.EvdevRecorder") as fake,
        caplog.at_level(logging.INFO, logger="macrokey.recorder.recorder"),
    ):
        recorder.start()

    assert fake.return_value.start.called
    assert recorder.backend == "evdev"
    assert "evdev" in caplog.text


def test_falling_back_to_pynput_is_a_warning_that_carries_the_reason(caplog) -> None:
    """Not an info line. The fallback is a downgrade, and the reason evdev was
    refused -- usually the input group -- is the fix."""
    recorder = Recorder()
    with (
        patch(
            "macrokey.recorder.recorder.evdev_source.available",
            return_value=(False, "not in the input group"),
        ),
        patch.object(Recorder, "available", return_value=(True, "")),
        patch("macrokey.recorder.recorder.pynput_keyboard", MagicMock()),
        caplog.at_level(logging.WARNING, logger="macrokey.recorder.recorder"),
    ):
        recorder.start()

    assert recorder.backend == "pynput"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "the downgrade was not logged as a warning"
    assert "not in the input group" in caplog.text
    assert "pynput" in caplog.text


def test_evdev_refusing_to_start_is_logged_before_it_is_raised(caplog) -> None:
    """The window only says the recording could not start. Which backend was
    refused, and why, is the whole diagnosis and it used to be nowhere."""
    recorder = Recorder()
    failing = MagicMock()
    failing.return_value.start.side_effect = RuntimeError("no readable input devices")
    with (
        patch("macrokey.recorder.recorder.evdev_source.available", return_value=(True, "")),
        patch("macrokey.recorder.recorder.evdev_source.EvdevRecorder", failing),
        caplog.at_level(logging.WARNING, logger="macrokey.recorder.recorder"),
    ):
        try:
            recorder.start()
        except RuntimeError:
            pass

    assert recorder.recording is False
    assert "no readable input devices" in caplog.text
