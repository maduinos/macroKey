"""Serial link behaviour, against a fake port rather than hardware.

The read loop had a bug that cost 200 ms on every single exchange and was
invisible in every functional test: everything still worked, just twenty times
slower than it should have. These pin the shape of the reads, not the outcome.
"""

from __future__ import annotations

import base64
import threading
import time

import pytest

from macrokey.boards import PROMICRO_RP2040
from macrokey.config import binary, default_profile
from macrokey.config.model import KEY_COUNT, LED_COUNT
from macrokey.device import protocol
from macrokey.device.client import DeviceClient, DeviceError


class FakeSerial:
    """Records how the reader asks for bytes, and answers each line with OK."""

    def __init__(
        self, reply_after: float = 0.0, profile_size: int = binary.PROFILE_SIZE
    ) -> None:
        self._pending = bytearray()
        self._lock = threading.Lock()
        self.read_sizes: list[tuple[int, int]] = []
        self.written: list[bytes] = []
        self.reply_after = reply_after
        self.profile_size = profile_size
        self.is_open = True
        self.fail_reads = False
        self.fail_writes = False

    # -- the parts pyserial exposes that the client uses -----------------------

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._pending)

    def read(self, size: int = 1) -> bytes:
        if self.fail_reads:
            raise OSError("USB cable removed")
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
        if self.fail_writes:
            raise OSError("USB cable removed")
        self.written.append(data)
        line = data.decode().strip()
        verb = line.split()[0] if line else ""
        # Built from the constants, so a topology change cannot leave the fake
        # claiming a profile size the app no longer speaks.
        reply = (
            f"HELLO proto=1 fw=0.3.0 board=promicro keys={KEY_COUNT} "
            f"leds={LED_COUNT} bytes={self.profile_size}\r\n"
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


def test_an_open_tty_is_not_connected_until_ident_has_validated_it() -> None:
    device = DeviceClient()
    device._serial = FakeSerial()

    assert device.connected is False

    device.hello = protocol.Hello(
        1, "test", "promicro", KEY_COUNT, LED_COUNT, binary.PROFILE_SIZE
    )
    assert device.connected is True
    device.disconnect()


def test_a_reader_failure_marks_the_link_disconnected_immediately(client) -> None:
    device, fake = client
    fake.fail_reads = True

    deadline = time.monotonic() + 1.0
    while device.connected and time.monotonic() < deadline:
        time.sleep(0.005)

    assert device.connected is False
    assert device.hello is None


def test_a_write_failure_is_wrapped_as_a_device_error(client) -> None:
    device, fake = client
    fake.fail_writes = True

    with pytest.raises(DeviceError, match="serial link lost"):
        device.ping()
    assert device.connected is False


def test_write_failure_observer_can_release_device_state_without_deadlock(client) -> None:
    device, fake = client
    cleaned = threading.Event()

    def cleanup(_reason: str) -> None:
        try:
            device.set_led_mode(False)
        except DeviceError:
            cleaned.set()

    device._on_disconnect = cleanup
    fake.fail_writes = True

    with pytest.raises(DeviceError, match="serial link lost"):
        device.ping()
    assert cleaned.wait(0.2)


def test_disconnect_cancels_a_connection_during_board_settle(monkeypatch) -> None:
    fake = FakeSerial()
    monkeypatch.setattr("macrokey.device.client.serial.Serial", lambda *a, **k: fake)
    monkeypatch.setattr("macrokey.device.client.OPEN_SETTLE_SECONDS", 5.0)
    device = DeviceClient()
    failures: list[Exception] = []

    def connect() -> None:
        try:
            device.connect("/dev/fake")
        except Exception as exc:  # noqa: BLE001 - asserted below
            failures.append(exc)

    thread = threading.Thread(target=connect)
    thread.start()
    deadline = time.monotonic() + 1.0
    while device._serial is None and time.monotonic() < deadline:
        time.sleep(0.005)
    device.disconnect()
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert failures and "cancelled" in str(failures[0])
    assert fake.is_open is False


def test_a_stale_reader_cannot_drop_a_new_connection() -> None:
    old = FakeSerial()
    current = FakeSerial()
    device = DeviceClient()
    device._serial = current
    device.hello = protocol.Hello(
        1, "test", "promicro", KEY_COUNT, LED_COUNT, binary.PROFILE_SIZE
    )

    device._drop_link(notify=True, expected_port=old)

    assert device.connected is True
    device.disconnect()


def test_link_loss_notifies_disconnect_observer(monkeypatch) -> None:
    fake = FakeSerial()
    lost: list[str] = []
    monkeypatch.setattr("macrokey.device.client.serial.Serial", lambda *a, **k: fake)
    monkeypatch.setattr("macrokey.device.client.OPEN_SETTLE_SECONDS", 0.0)
    device = DeviceClient(on_disconnect=lost.append)
    device.connect("/dev/fake")

    fake.fail_reads = True
    deadline = time.monotonic() + 1.0
    while not lost and time.monotonic() < deadline:
        time.sleep(0.005)

    assert lost and "link lost" in lost[0].lower()
    device.disconnect()


def test_a_broken_event_observer_does_not_kill_serial_dispatch() -> None:
    def broken(_event) -> None:
        raise RuntimeError("view disappeared")

    device = DeviceClient(on_event=broken)
    device._dispatch("EV t=key k=0 g=tap l=0 ms=10")

    device._responses.put(protocol.Message("OK"))
    assert device._responses.get_nowait().verb == "OK"


def test_auto_discovery_falls_through_from_likely_to_generic(monkeypatch) -> None:
    from macrokey.device.discovery import PortCandidate
    from macrokey.device.protocol import Hello

    candidates = [
        PortCandidate("/dev/likely", "Arduino", 0x2341, 0x8036),
        PortCandidate("/dev/generic", "clone", 0xCAFE, 0xBEEF),
    ]
    monkeypatch.setattr("macrokey.device.client.discovery.candidates", lambda: candidates)
    attempted: list[str] = []
    device = DeviceClient()

    def connect_one(port: str) -> Hello:
        attempted.append(port)
        if port == "/dev/likely":
            raise DeviceError("not macroKey")
        return Hello(1, "test", "promicro", KEY_COUNT, LED_COUNT, binary.PROFILE_SIZE)

    monkeypatch.setattr(device, "_connect_one", connect_one)
    device.connect("")
    assert attempted == ["/dev/likely", "/dev/generic"]


def test_usb_identity_requires_matching_vendor_and_product() -> None:
    from macrokey.device.discovery import PortCandidate

    assert PortCandidate("a", "", 0x2341, 0x8036).likely
    assert PortCandidate("promicro", "", 0x1B4F, 0x9206).likely
    assert PortCandidate("rp2040", "", 0x2E8A, 0xF009).likely
    assert not PortCandidate("b", "", 0x2341, 0xBEEF).likely
    assert not PortCandidate("c", "", 0xCAFE, 0x8036).likely


def test_eeprom_operations_get_a_longer_reply_window(monkeypatch) -> None:
    from macrokey.device.client import EEPROM_WRITE_TIMEOUT

    device = DeviceClient()
    calls: list[tuple[str, float]] = []

    def request(line: str, expect=None, timeout=2.0):
        calls.append((line, timeout))
        return protocol.Message("OK")

    monkeypatch.setattr(device, "request", request)
    device.write_profile(binary.encode_profile(default_profile()))
    device.reset_defaults()

    commit = next(timeout for line, timeout in calls if line == "PROF commit")
    reset = next(timeout for line, timeout in calls if line.startswith("RESET "))
    assert commit == EEPROM_WRITE_TIMEOUT
    assert reset == EEPROM_WRITE_TIMEOUT


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


def test_connect_accepts_the_rp2040_large_profile(monkeypatch) -> None:
    fake = FakeSerial(profile_size=PROMICRO_RP2040.profile_size)
    monkeypatch.setattr("macrokey.device.client.serial.Serial", lambda *a, **k: fake)
    monkeypatch.setattr("macrokey.device.client.OPEN_SETTLE_SECONDS", 0.0)
    device = DeviceClient()
    device.connect("/dev/fake")

    assert device.hello is not None
    assert device.hello.profile_bytes == PROMICRO_RP2040.profile_size
    device.disconnect()


@pytest.mark.parametrize(
    ("keys", "leds"),
    [(KEY_COUNT + 1, LED_COUNT), (KEY_COUNT, LED_COUNT + 1)],
)
def test_connect_rejects_a_different_device_topology(monkeypatch, keys, leds) -> None:
    fake = FakeSerial()

    def wrong_topology(data: bytes) -> int:
        fake.written.append(data)
        reply = (
            f"HELLO proto=1 fw=0.5.0 board=promicro keys={keys} "
            f"leds={leds} bytes={binary.PROFILE_SIZE}\r\n"
        ).encode()
        fake._pending.extend(reply)
        return len(data)

    fake.write = wrong_topology  # type: ignore[method-assign]
    monkeypatch.setattr("macrokey.device.client.serial.Serial", lambda *a, **k: fake)
    monkeypatch.setattr("macrokey.device.client.OPEN_SETTLE_SECONDS", 0.0)
    with pytest.raises(DeviceError, match="expects"):
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


# --------------------------------------------------- profile transfer framing --


def _profile_lines(blob: bytes) -> list[bytes]:
    lines = [
        f"PROF begin bytes={len(blob)} crc={binary.blob_crc(blob):04X}\r\n".encode()
    ]
    for sequence, offset in enumerate(range(0, len(blob), binary.CHUNK_BYTES)):
        payload = base64.b64encode(blob[offset : offset + binary.CHUNK_BYTES]).decode()
        lines.append(f"PROF data seq={sequence} b64={payload}\r\n".encode())
    lines.append(b"PROF end\r\n")
    return lines


def _answer_next_write(fake: FakeSerial, lines: list[bytes]) -> None:
    def answer(data: bytes) -> int:
        fake.written.append(data)
        with fake._lock:
            fake._pending.extend(b"".join(lines))
        return len(data)

    fake.write = answer  # type: ignore[method-assign]


def test_a_framed_profile_dump_round_trips(client) -> None:
    device, fake = client
    blob = binary.encode_profile(default_profile())
    _answer_next_write(fake, _profile_lines(blob))

    assert device.read_profile() == blob


def test_a_large_rp2040_profile_dump_round_trips(client) -> None:
    device, fake = client
    device.hello = protocol.Hello(
        1, "test", "promicro-rp2040", KEY_COUNT, LED_COUNT, PROMICRO_RP2040.profile_size
    )
    blob = binary.encode_profile(
        default_profile(), profile_size=PROMICRO_RP2040.profile_size
    )
    _answer_next_write(fake, _profile_lines(blob))

    assert device.read_profile() == blob


def test_profile_data_before_the_header_is_rejected(client) -> None:
    device, fake = client
    blob = binary.encode_profile(default_profile())
    _answer_next_write(fake, _profile_lines(blob)[1:])

    with pytest.raises(DeviceError, match="before its header"):
        device.read_profile()


def test_out_of_order_profile_chunks_are_rejected(client) -> None:
    device, fake = client
    blob = binary.encode_profile(default_profile())
    lines = _profile_lines(blob)
    lines[1], lines[2] = lines[2], lines[1]
    _answer_next_write(fake, lines)

    with pytest.raises(DeviceError, match="expected 0"):
        device.read_profile()


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
