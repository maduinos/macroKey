"""Command line entry point.

Every command works without the GUI, which is also how the app is tested
against real hardware on a machine with no desktop session.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from .app import MacroKeyApp
from .boards import BOARDS
from .config import EDITABLE_GESTURES, KEY_COUNT
from .device import DeviceError, candidates, pyserial_available
from .logging_setup import setup_logging
from .recorder.recorder import DEFAULT_STOP_KEY
from .ui import MissingToolkit


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

    flash = sub.add_parser("flash", help="put firmware on the keypad")
    flash.add_argument(
        "--board",
        default="",
        choices=[board.id for board in BOARDS],
        help="which board, when more than one is attached (default: whatever is found)",
    )
    flash.add_argument(
        "--image", default="", help="a firmware file to write instead of the bundled one"
    )
    flash.add_argument("--yes", "-y", action="store_true", help="do not ask before writing")

    sub.add_parser("boards", help="list the boards this build supports")

    update = sub.add_parser(
        "update", help="update the keypad firmware and the app from GitHub releases"
    )
    update.add_argument(
        "--check", action="store_true", help="report what is available and change nothing"
    )
    update.add_argument(
        "--firmware", action="store_true", help="the keypad only, not the app"
    )
    update.add_argument("--app", action="store_true", help="the app only, not the keypad")
    update.add_argument(
        "--offline",
        action="store_true",
        help="use the image inside this build; never reach the network",
    )
    update.add_argument("--yes", "-y", action="store_true", help="do not ask before writing")

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
    setup_logging(args.verbose)

    command = args.command or "gui"
    handler = {
        "gui": cmd_gui,
        "update": cmd_update,
        "ports": cmd_ports,
        "boards": cmd_boards,
        "flash": cmd_flash,
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


def cmd_boards(args: argparse.Namespace) -> int:
    """What this build knows how to talk to, and whether it can flash it."""
    from .config import binary
    from .flash import available

    images = available()
    for board in BOARDS:
        layout = binary.layout_for_board(board)
        image = images.get(board.id)
        print(f"{board.id}")
        print(f"    {board.display_name} ({board.mcu})")
        print(
            f"    profile {board.profile_size} B, schema {board.schema}, "
            f"{layout.record_capacity} records ({board.max_records_per_slot} per slot)"
        )
        print(f"    firmware {image if image else '(not bundled in this build)'}")
        print(f"    docs {board.docs_page}")
    return 0


def cmd_flash(args: argparse.Namespace) -> int:
    """Detect the attached board and write the matching firmware to it.

    The confirmation is the whole interaction in the ordinary case: someone
    plugs a freshly soldered keypad in and answers one question. The exception
    is a board that has never run macroKey firmware, which cannot be asked to
    reboot into its bootloader because nothing on it is listening -- that needs
    a button held or a jumper touched, and the hint below says which.
    """
    from pathlib import Path

    from .flash import FlashError, NeedsManualBootloader, find_board, find_image, flash

    board_id = args.board or None
    found = find_board(board_id)
    if found is None:
        print(
            "no board found. Plug the keypad in. If it has never been flashed, "
            "hold its bootloader button while plugging it in:",
            file=sys.stderr,
        )
        for board in BOARDS:
            print(f"  {board.display_name}: {board.first_flash_hint}", file=sys.stderr)
        return 1

    try:
        image = Path(args.image) if args.image else find_image(found.board)
    except FlashError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"found    {found}")
    print(f"firmware {image}")
    if not args.yes:
        try:
            answer = input("write it to the board? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("nothing was written")
            return 1

    try:
        port = flash(board_id=found.board.id, image=image, status=print)
    except NeedsManualBootloader as exc:
        print(f"\n{found.board.display_name}: {exc.hint}", file=sys.stderr)
        print("then run this again.", file=sys.stderr)
        return 2
    except FlashError as exc:
        print(f"flashing failed: {exc}", file=sys.stderr)
        return 2

    if port:
        print(f"done -- the keypad is on {port}")
    else:
        print(
            "done -- the firmware was written, but the keypad has not reappeared yet. "
            "Unplug and plug it back in, then run `macrokey ports`."
        )
    return 0



def _confirm(question: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _update_firmware(args: argparse.Namespace) -> int:
    """Brings the attached pad level with the newest firmware available.

    The keypad has to be let go of first. Flashing resets the board through the
    same serial port this app is holding open, so an update that ran while
    connected would be asking the port to reboot out from under itself.
    """
    from .boards import board_by_id
    from .flash import FlashError, flash
    from .update import UpdateError, firmware

    app = MacroKeyApp(status=print)
    try:
        hello = app.device.connect(args.port)
        running, board_id = hello.firmware, hello.board
    except DeviceError as exc:
        print(f"no keypad to update: {exc}", file=sys.stderr)
        return 2
    finally:
        app.close()

    board = board_by_id(board_id)
    if board is None:
        print(f"unknown board {board_id!r}", file=sys.stderr)
        return 2

    try:
        candidate = firmware.best(
            board, running, allow_network=not args.offline, status=print
        )
    except UpdateError as exc:
        print(f"could not look for firmware: {exc}", file=sys.stderr)
        return 2
    if candidate is None:
        print(f"firmware {running} on {board.display_name} is current")
        return 0

    where = "downloaded" if candidate.downloaded else "bundled"
    print(f"firmware {running} -> {candidate.version} ({where}: {candidate.image})")
    if args.check:
        return 0
    if not _confirm("write it to the keypad?", assume_yes=args.yes):
        print("nothing was written")
        return 1
    try:
        flash(board_id=board.id, image=candidate.image, status=print)
    except FlashError as exc:
        print(f"firmware update failed: {exc}", file=sys.stderr)
        return 2
    print(f"keypad is now on firmware {candidate.version}")
    return 0


def _update_app(args: argparse.Namespace) -> int:
    from .update import UpdateError, selfupdate

    usable, why = selfupdate.supported()
    if not usable:
        print(f"app update unavailable: {why}")
        return 0
    try:
        release = selfupdate.check()
    except UpdateError as exc:
        print(f"could not check for an app update: {exc}", file=sys.stderr)
        return 2
    if release is None:
        print(f"app v{__version__} is current")
        return 0

    print(f"app v{__version__} -> v{release.version} ({release.tag})")
    if args.check:
        return 0
    if not _confirm("download and install it?", assume_yes=args.yes):
        print("nothing was installed")
        return 1
    try:
        path = selfupdate.apply(release, status=print)
    except UpdateError as exc:
        print(f"app update failed: {exc}", file=sys.stderr)
        return 2
    print(f"installed v{release.version} at {path} -- restart to use it")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Firmware, app, or both. Both is the default, and both report separately.

    One failing does not skip the other: a pad that is not plugged in is no
    reason to leave the app a release behind, and an update server having a bad
    day is no reason to leave the keypad on firmware the editor no longer
    agrees with.
    """
    from .update import releases

    if not releases.enabled() and not args.offline:
        print("updates are switched off (MACROKEY_NO_UPDATE)", file=sys.stderr)
        return 2

    both = not args.firmware and not args.app
    codes = []
    if both or args.firmware:
        codes.append(_update_firmware(args))
    if both or args.app:
        if args.offline:
            # Only said when it was asked for by name. `--offline` on its own
            # means "the keypad, from what is already here", and announcing a
            # skip nobody asked for would read as a failure.
            if args.app:
                print("--offline cannot update the app: it has to be downloaded")
        else:
            codes.append(_update_app(args))
    return max(codes) if codes else 0


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
        if app.recorder.capture_mouse and app.recorder.anchor_mouse and app.device.connected:
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
