"""Command line entry point.

Every command works without the GUI, which is also how the app is tested
against real hardware on a machine with no desktop session.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import time

from . import __version__
from .app import MacroKeyApp
from .config import GESTURES, KEY_COUNT, LAYER_COUNT
from .device import DeviceError, candidates, pyserial_available
from .led import default_socket_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="macrokey", description="Maduinos macroKey host app")
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
    sub.add_parser("daemon", help="run headless: host actions and LEDs, no window")

    record = sub.add_parser("record", help="record input and bind it to a key")
    record.add_argument("--layer", type=int, default=0, choices=range(LAYER_COUNT))
    record.add_argument("--key", type=int, required=True, choices=range(1, KEY_COUNT + 1))
    record.add_argument("--gesture", default="tap", choices=GESTURES)
    record.add_argument("--name", default="")

    state = sub.add_parser("state", help="send a state event to the running app")
    state.add_argument("state", help="idle, running, error, ...")
    state.add_argument("--severity", default="info")
    state.add_argument("--progress", type=float, default=None)
    state.add_argument("--title", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    command = args.command or "gui"
    handler = {
        "gui": cmd_gui,
        "ports": cmd_ports,
        "info": cmd_info,
        "push": cmd_push,
        "pull": cmd_pull,
        "monitor": cmd_monitor,
        "daemon": cmd_daemon,
        "record": cmd_record,
        "state": cmd_state,
    }[command]

    try:
        return handler(args)
    except DeviceError as exc:
        print(f"device error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


# ------------------------------------------------------------------ commands --


def cmd_gui(args: argparse.Namespace) -> int:
    from .ui.app import run_gui

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
        print(f"topology   {hello.keys} keys, {hello.leds} leds, {hello.layers} layers")
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


def cmd_daemon(args: argparse.Namespace) -> int:
    app = MacroKeyApp(status=print)
    try:
        app.connect(args.port)
        print("running headless, Ctrl-C to stop")
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        app.close()


def cmd_record(args: argparse.Namespace) -> int:
    app = MacroKeyApp(status=print)
    key_index = args.key - 1
    try:
        app.start_recording()
        while app.recorder.recording:
            time.sleep(0.1)
        steps = app.stop_recording()
        if not steps:
            print("nothing recorded")
            return 1

        print("\nrecorded:")
        for line in app.recorder.summary(steps):
            print(f"  {line}")

        answer = input(f"\nbind to layer {args.layer} key {args.key} {args.gesture}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("discarded")
            return 1

        where = app.assign_recording(steps, args.layer, key_index, args.gesture, args.name)
        app.save()
        print(f"bound as {where}")
        print("run `macrokey push` to write it to the device")
    finally:
        app.close()
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    """Sends one event to a running app, for testing the LED mapping."""
    payload = {
        "schema_version": 1,
        "source": "external",
        "state": args.state,
        "severity": args.severity,
    }
    if args.progress is not None:
        payload["progress"] = args.progress
    if args.title:
        payload["title"] = args.title

    path = default_socket_path()
    if not hasattr(socket, "AF_UNIX"):
        print("state events need a Unix socket; not supported on this platform", file=sys.stderr)
        return 2
    if not path.exists():
        print(f"no listener at {path}. Start the app first.", file=sys.stderr)
        return 1

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2.0)
        client.connect(str(path))
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    print(f"sent {args.state} to {path}")
    return 0
