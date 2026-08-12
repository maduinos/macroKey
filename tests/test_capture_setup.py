"""First-run capture setup helpers."""

from __future__ import annotations

from unittest.mock import patch

from macrokey.capture_setup import CaptureStatus, needs_linux_capture_fix, status


def test_needs_linux_capture_fix_is_false_when_ready() -> None:
    with (
        patch("macrokey.capture_setup.platform.system", return_value="Linux"),
        patch(
            "macrokey.capture_setup.status",
            return_value=CaptureStatus(ok=True, package_ok=True, devices_ok=True),
        ),
    ):
        assert needs_linux_capture_fix() is False


def test_needs_linux_capture_fix_is_false_off_linux() -> None:
    with (
        patch("macrokey.capture_setup.platform.system", return_value="Windows"),
        patch(
            "macrokey.capture_setup.status",
            return_value=CaptureStatus(
                ok=False, package_ok=False, devices_ok=False, reason="no"
            ),
        ),
    ):
        assert needs_linux_capture_fix() is False


def test_needs_linux_capture_fix_when_broken_on_linux() -> None:
    with (
        patch("macrokey.capture_setup.platform.system", return_value="Linux"),
        patch(
            "macrokey.capture_setup.status",
            return_value=CaptureStatus(
                ok=False,
                package_ok=False,
                devices_ok=False,
                reason="python-evdev is not installed",
            ),
        ),
    ):
        assert needs_linux_capture_fix() is True


def test_status_reports_package_gap() -> None:
    with patch("macrokey.recorder.recorder.Recorder.available", return_value=(False, "no")):
        with patch("macrokey.recorder.evdev_source.evdev", None):
            st = status()
    assert st.ok is False
    assert st.package_ok is False


def test_install_evdev_refuses_inside_frozen_build() -> None:
    with (
        patch("macrokey.runtime.frozen", return_value=True),
        patch("macrokey.recorder.evdev_source.evdev", None),
    ):
        from macrokey.capture_setup import install_evdev

        ok, message = install_evdev()
    assert ok is False
    assert "build_release" in message.lower() or "evdev" in message.lower()
