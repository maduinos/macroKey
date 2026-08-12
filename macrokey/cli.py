"""Command line entry point.

Every command works without the GUI, which is also how the app is tested
against real hardware on a machine with no desktop session.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import time

from . import __version__
from .app import MacroKeyApp
from .config import EDITABLE_GESTURES, KEY_COUNT
from .device import DeviceError, candidates, pyserial_available
from .recorder.recorder import DEFAULT_STOP_KEY
from .ui import MissingToolkit


def _setup_logging(verbose: bool) -> None:
    """Everything to the file, only what is worth interrupting to the terminal.

    The two have different jobs. The file answers "what did it actually
    capture" afterwards, so it takes everything and is not behind --verbose: a
    recording is made with the window unwatched and the pad under a hand, and by
    the time something is obviously wrong the explanation has already happened.
    The terminal is where someone is reading, and status messages already reach
    them through the status callback -- logging those too printed every line
    twice.

    The levels belong on the handlers. Raising the root logger so the file could
    see everything sent all of it to the terminal as well, which is how the
    console came to be full of every step of every recording.
    """
    from .config.store import profile_path

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)

    try:
        path = profile_path().parent / "macrokey.log"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        to_file = logging.handlers.RotatingFileHandler(
            path, maxBytes=512_000, backupCount=1, encoding="utf-8"
        )
        to_file.setLevel(logging.DEBUG)
        to_file.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(to_file)
    except OSError:
        pass  # a log we cannot write is not a reason to refuse to run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="macrokey",
        description="Maduinos macroKey config app (pad is HID-only after setup)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--verbose", "-v", action="store_true", help="log at debug level")
    parser.add_argument("--port", default="", help="serial port (default: auto-detect)")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="open the editor window (default)")
    sub.add_parser("ports", help="list serial ports")
    sub.add_parser("info", help="connect and print what the device reports")
    sub.add_parser("push", help="write the stored profile to the device")

    pull = sub.add_parser("pull", help="read the device profile")
    pull.add_argument("--save", action="store_true", help="adopt it as the stored profile")

    sub.add_parser("monitor", help="print device events until interrupted")

    record = sub.add_parser("record", help="record input and bind it to a key")
    record.add_argument("--key", type=int, required=True, choices=range(1, KEY_COUNT + 1))
    # Not GESTURES: hold is how recording starts on the pad itself, so nothing
    # may be bound to it.
    record.add_argument("--gesture", default="tap", choices=EDITABLE_GESTURES)
    record.add_argument(
        "--no-push",
        action="store_true",
        help="save the profile only; do not write it to the keypad",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    command = args.command or "gui"
    handler = {
        "gui": cmd_gui,
        "ports": cmd_ports,
        "info": cmd_info,
        "push": cmd_push,
        "pull": cmd_pull,
        "monitor": cmd_monitor,
        "record": cmd_record,
    }[command]

    try:
        return handler(args)
    except MissingToolkit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except DeviceError as exc:
        print(f"device error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


# ------------------------------------------------------------------ commands --


def cmd_gui(args: argparse.Namespace) -> int:
    from .ui import run_gui

    return run_gui(port=args.port)


def cmd_ports(args: argparse.Namespace) -> int:
    if not pyserial_available():
        print("pyserial is not installed: pip install pyserial", file=sys.stderr)
        return 2
    found = candidates()
    if not found:
        print("no serial ports found")
        return 1
    for candidate in found:
        marker = "*" if candidate.likely else " "
        print(f"{marker} {candidate}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    app = MacroKeyApp(status=print)
    try:
        hello = app.device.connect(args.port)
        print(f"firmware   {hello.firmware} on {hello.board}")
        print(f"protocol   v{hello.protocol}")
        print(f"topology   {hello.keys} keys, {hello.leds} led(s)")
        print(f"profile    {hello.profile_bytes} bytes")
        same = app.device_matches_host()
        print(f"in sync    {'yes' if same else 'no -- run: macrokey push'}")
    finally:
        app.close()
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    app = MacroKeyApp(status=print)
    try:
        app.device.connect(args.port)
        app.push_profile()
    finally:
        app.close()
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    app = MacroKeyApp(status=print)
    try:
        app.device.connect(args.port)
        profile = app.pull_profile()
        print(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False))
        if args.save:
            app.profile = profile
            app.save()
    finally:
        app.close()
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    app = MacroKeyApp(status=print)
    app.on_event(lambda event: print(event))
    try:
        app.device.connect(args.port)
        print("watching device events, Ctrl-C to stop")
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        app.close()


def cmd_record(args: argparse.Namespace) -> int:
    from .config import ProfileError
    from .ui.describe import nothing_captured_hint

    app = MacroKeyApp(status=print)
    key_index = args.key - 1
    try:
        try:
            app.device.connect(args.port)
        except DeviceError as exc:
            print(
                f"warning: no keypad ({exc}); recording to the stored profile only",
                file=sys.stderr,
            )

        # No window to click Stop in, so the CLI opts into the key. It is
        # therefore the one path where a macro cannot contain Esc.
        app.recorder.stop_key = DEFAULT_STOP_KEY
        if app.recorder.capture_mouse and app.device.connected:
            try:
                app.device.home_pointer()
            except DeviceError as exc:
                print(f"warning: could not home pointer: {exc}", file=sys.stderr)

        print(f"recording -- press {DEFAULT_STOP_KEY} to stop")
        app.start_recording()
        while app.recorder.recording:
            time.sleep(0.1)
        steps = app.stop_recording()
        if not steps:
            print(nothing_captured_hint())
            return 1

        print("\nrecorded:")
        for line in app.recorder.summary(steps):
            print(f"  {line}")
        if app.last_redacted:
            print(
                f"! dropped {app.last_redacted} step(s) that looked like a password",
                file=sys.stderr,
            )

        answer = input(f"\nbind to key {args.key} {args.gesture}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("discarded")
            return 1

        try:
            where = app.assign_recording(steps, key_index, args.gesture)
        except ProfileError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        app.save()
        if args.no_push or not app.device.connected:
            print(f"bound as {where}")
            if not args.no_push:
                print("run `macrokey push` to write it to the device")
            return 0
        app.push_profile()
        print(f"bound as {where} and written to the keypad")
    finally:
        app.close()
    return 0
