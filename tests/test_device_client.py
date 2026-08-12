"""Serial link behaviour, against a fake port rather than hardware.

The read loop had a bug that cost 200 ms on every single exchange and was
invisible in every functional test: everything still worked, just twenty times
slower than it should have. These pin the shape of the reads, not the outcome.
"""

from __future__ import annotations

import threading
import time

import pytest

from macrokey.config import binary
from macrokey.config.model import KEY_COUNT, LED_COUNT
from macrokey.device import protocol
from macrokey.device.client import DeviceClient, DeviceError


class FakeSerial:
    """Records how the reader asks for bytes, and answers each line with OK."""

    def __init__(self, reply_after: float = 0.0) -> None:
        self._pending = bytearray()
        self._lock = threading.Lock()
        self.read_sizes: list[tuple[int, int]] = []
        self.written: list[bytes] = []
        self.reply_after = reply_after
        self.is_open = True

    # -- the parts pyserial exposes that the client uses -----------------------

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._pending)

    def read(self, size: int = 1) -> bytes:
        # Both numbers matter: asking for more than is buffered is what makes
        # pyserial sit on the timeout.
        self.read_sizes.append((size, self.in_waiting))
        deadline = time.monotonic() + 0.2  # matches the client's read timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._pending:
                    taken = bytes(self._pending[:size])
                    del self._pending[:size]
                    return taken
            time.sleep(0.001)
        return b""

    def write(self, data: bytes) -> int:
        self.written.append(data)
        line = data.decode().strip()
        verb = line.split()[0] if line else ""
        # Built from the constants, so a topology change cannot leave the fake
        # claiming a profile size the app no longer speaks.
        reply = (
            f"HELLO proto=1 fw=0.3.0 board=promicro keys={KEY_COUNT} "
            f"leds={LED_COUNT} bytes={binary.PROFILE_SIZE}\r\n"
        ).encode()
        if verb != "IDENT":
            reply = b"OK\r\n"
        if self.reply_after:
            time.sleep(self.reply_after)
        with self._lock:
            self._pending.extend(reply)
        return len(data)

    def close(self) -> None:
        self.is_open = False

    def reset_input_buffer(self) -> None:
        with self._lock:
            self._pending.clear()


@pytest.fixture
def client(monkeypatch):
    fake = FakeSerial()
    monkeypatch.setattr("macrokey.device.client.serial.Serial", lambda *a, **k: fake)
    monkeypatch.setattr("macrokey.device.client.OPEN_SETTLE_SECONDS", 0.0)
    device = DeviceClient()
    device.connect("/dev/fake")
    yield device, fake
    device.disconnect()


def test_the_reader_asks_for_one_byte_when_nothing_is_buffered(client) -> None:
    """Asking for a fixed size made pyserial wait out the whole read timeout.

    `read(n)` returns early only once it has n bytes; with nothing waiting it
    blocks for the full timeout and then hands over whatever turned up. Replies
    here are four bytes, so every exchange paid 200 ms for nothing. An idle
    reader must therefore ask for exactly one byte.
    """
    device, fake = client
    device.ping()
    time.sleep(0.05)
    overreach = [
        (asked, waiting)
        for asked, waiting in fake.read_sizes
        if asked > max(1, waiting)
    ]
    assert not overreach, f"read asked for more than was buffered: {overreach}"


def test_a_request_returns_promptly(client) -> None:
    device, _ = client
    started = time.monotonic()
    for _ in range(10):
        device.ping()
    elapsed = time.monotonic() - started
    # Ten exchanges took over two seconds before the read fix.
    assert elapsed < 0.5, f"ten round trips took {elapsed:.2f}s"


def test_connect_rejects_a_protocol_it_does_not_speak(monkeypatch) -> None:
    fake = FakeSerial()

    def wrong_version(data: bytes) -> int:
        fake.written.append(data)
        line = data.decode().strip()
        reply = b"OK\r\n"
        if line.startswith("IDENT"):
            reply = (
                f"HELLO proto=99 fw=9.9.9 board=x keys={KEY_COUNT} leds={LED_COUNT} "
                f"bytes={binary.PROFILE_SIZE}\r\n"
            ).encode()
        fake._pending.extend(reply)
        return len(data)

    fake.write = wrong_version  # type: ignore[method-assign]
    monkeypatch.setattr("macrokey.device.client.serial.Serial", lambda *a, **k: fake)
    monkeypatch.setattr("macrokey.device.client.OPEN_SETTLE_SECONDS", 0.0)
    with pytest.raises(DeviceError, match="protocol"):
        DeviceClient().connect("/dev/fake")


def test_led_mode_can_declare_its_own_silence_window(client) -> None:
    """`ms=` lets a host holding a colour still say it is not dead."""
    device, fake = client
    device.set_led_mode(True, timeout_ms=45000)
    assert b"ms=45000" in fake.written[-1]


def test_releasing_led_mode_sends_no_window(client) -> None:
    device, fake = client
    device.set_led_mode(False)
    assert b"mode=local" in fake.written[-1]
    assert b"ms=" not in fake.written[-1]


def test_a_declared_window_is_clamped(client) -> None:
    device, fake = client
    device.set_led_mode(True, timeout_ms=10**9)
    assert b"ms=60000" in fake.written[-1]


def test_encode_round_trips_through_parse() -> None:
    line = protocol.encode("LED", mode="host", ms=45000)
    message = protocol.parse(line.strip())
    assert message.verb == "LED"
    assert message.get("mode") == "host"
    assert message.get("ms") == "45000"


# ------------------------------------------------------- key 0 is a real key --


def test_key_zero_parses_as_zero_and_not_as_minus_one() -> None:
    """`message.int("k", -1) or -1` reads as a default and is not one: 0 is
    falsy, so the first key on the pad parsed as -1. Nothing then failed --
    -1 is a valid Python index meaning "the last one" -- so holding key 1 to
    record silently stored the macro on key 8.
    """
    from macrokey.device import protocol

    for line, attribute in (
        ("EV t=key k=0 g=tap l=0 ms=100", "key"),
        ("EV t=record k=0 ms=100", "key"),
    ):
        event = protocol.parse_event(protocol.parse(line))
        assert event is not None, line
        assert getattr(event, attribute) == 0, line


def test_every_key_index_survives_parsing() -> None:
    from macrokey.device import protocol

    for key in range(8):
        event = protocol.parse_event(protocol.parse(f"EV t=record k={key} ms=1"))
        assert event.key == key


def test_a_missing_index_is_still_reported_as_absent() -> None:
    """The default has to keep working; -1 means "the device did not say"."""
    from macrokey.device import protocol

    event = protocol.parse_event(protocol.parse("EV t=record ms=1"))
    assert event.key == -1


def test_uptime_zero_is_not_swallowed() -> None:
    from macrokey.device import protocol

    event = protocol.parse_event(protocol.parse("EV t=key k=3 g=tap ms=0"))
    assert event.key == 3
    assert event.uptime_ms == 0


def test_retired_host_events_are_ignored() -> None:
    from macrokey.device import protocol

    assert protocol.parse_event(protocol.parse("EV t=host tok=0 k=0")) is None


def test_an_out_of_range_key_is_refused_rather_than_wrapped() -> None:
    """Second line of defence. A negative index is a perfectly good Python
    index, which is why the first bug was invisible."""
    import pytest

    from macrokey.config.model import Action, ProfileError, default_profile

    profile = default_profile()
    for bad in (-1, 8, 99):
        with pytest.raises(ProfileError):
            profile.set_action(bad, "tap", Action(kind="key", hotkey="a"))
        with pytest.raises(ProfileError):
            profile.action(bad, "tap")
