"""Connected keypad: framing, request/response and profile transfer.

One background thread reads lines. Responses go to the waiting caller through a
queue; unsolicited ``EV`` lines go straight to the event callback. Callers never
touch the serial port directly.
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from ..config import KEY_COUNT, LED_COUNT, binary
from . import discovery, protocol
from .protocol import Hello, KeyEvent, Message, ProtocolError, RecordRequest

try:
    import serial
except ModuleNotFoundError:  # pragma: no cover - exercised only without pyserial
    serial = None

log = logging.getLogger(__name__)

DeviceEvent = KeyEvent | RecordRequest
EventCallback = Callable[[DeviceEvent], None]
StatusCallback = Callable[[str], None]
DisconnectCallback = Callable[[str], None]

# The Leonardo re-enumerates when the port opens, so give it time before IDENT.
OPEN_SETTLE_SECONDS = 2.0
RESPONSE_TIMEOUT = 2.0
PROFILE_READ_TIMEOUT = 30.0
# Committing a profile and restoring defaults write most of the AVR EEPROM.
# EEPROM.update() is deliberately slow per changed byte, so a full profile can
# legitimately take longer than an ordinary request without the link being bad.
EEPROM_WRITE_TIMEOUT = 8.0

_LINK_LOST = object()


class DeviceError(RuntimeError):
    """Raised when the device is absent, incompatible or uncooperative."""


class DeviceClient:
    def __init__(
        self,
        on_event: EventCallback | None = None,
        on_status: StatusCallback | None = None,
        on_disconnect: DisconnectCallback | None = None,
    ) -> None:
        self._on_event = on_event
        self._on_status = on_status or (lambda message: None)
        self._on_disconnect = on_disconnect or (lambda reason: None)
        self._serial: Any = None
        self._reader: threading.Thread | None = None
        self._responses: queue.Queue[Message | object] = queue.Queue()
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()
        # A failed write reports disconnection synchronously. Its observer may
        # release recording LED state through another request on the same
        # thread, so this lock must permit that cleanup to re-enter and fail
        # promptly against the now-detached port rather than deadlocking.
        self._request_lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._connect_cancel = threading.Event()
        # A profile transfer is begin, twenty data chunks and commit. The request
        # lock only makes each line atomic, so two transfers could interleave and
        # each would abort the other's staging -- which surfaced as the device
        # rejecting a chunk with "range". Edits apply themselves, connecting
        # reconciles, and recordings save, so overlapping transfers are ordinary.
        self._profile_lock = threading.Lock()
        self._stop = threading.Event()
        self.hello: Hello | None = None
        self.port: str = ""

    # ------------------------------------------------------------ lifecycle --

    @property
    def connected(self) -> bool:
        with self._state_lock:
            port = self._serial
            hello = self.hello
        # Opening a tty is only the transport handshake. Until IDENT has
        # validated protocol, profile size, key count, and LED count, callers
        # must not mistake an unrelated serial device for a connected keypad.
        if port is None or hello is None:
            return False
        try:
            return bool(port.is_open)
        except Exception:
            return False

    def connect(self, port: str = "") -> Hello:
        """Opens `port`, or probes every candidate when it is empty."""
        with self._connect_lock:
            self._connect_cancel.clear()
            if self.connected:
                raise DeviceError(f"already connected to {self.port}")
            if serial is None:
                raise DeviceError("pyserial is not installed: pip install pyserial")

            if port:
                ports = [port]
            else:
                found = discovery.candidates()
                # Try boards with the expected USB identity first, then the rest.
                # Clones often use generic VID/PIDs, so excluding the second group
                # merely because one likely-looking (but unrelated) board exists
                # makes auto-discovery miss a working keypad.
                likely = [item.device for item in found if item.likely]
                ports = likely + [item.device for item in found if not item.likely]
            if not ports:
                raise DeviceError("no USB serial ports found. Is the keypad plugged in?")

            errors: list[str] = []
            for candidate in ports:
                if self._connect_cancel.is_set():
                    raise DeviceError("connection cancelled")
                try:
                    return self._connect_one(candidate)
                except DeviceError as exc:
                    errors.append(f"{candidate}: {exc}")
                    # Candidate cleanup is not a user cancellation; keep trying
                    # the remaining USB serial devices unless disconnect() was
                    # called from another thread.
                    self._drop_link(notify=False)
                    if self._connect_cancel.is_set():
                        raise DeviceError("connection cancelled") from exc
            raise DeviceError("no macroKey device answered.\n" + "\n".join(errors))

    def _connect_one(self, port: str) -> Hello:
        self._on_status(f"Opening {port}")
        try:
            opened = serial.Serial(port, 115200, timeout=0.2)
        except Exception as exc:  # pyserial raises several unrelated types
            raise DeviceError(str(exc)) from exc
        stop = threading.Event()
        with self._state_lock:
            self._stop = stop
            self._serial = opened

        if self._connect_cancel.wait(OPEN_SETTLE_SECONDS):
            raise DeviceError("connection cancelled")
        self._drain()
        reader = threading.Thread(
            target=self._read_loop,
            args=(opened, stop),
            name="macrokey-serial",
            daemon=True,
        )
        with self._state_lock:
            if self._serial is not opened or stop.is_set():
                raise DeviceError("connection cancelled")
            self._reader = reader
            reader.start()

        response = self.request(protocol.encode("IDENT"), expect={"HELLO"})
        hello = Hello.from_message(response)
        if not hello.compatible:
            raise DeviceError(
                f"device speaks protocol v{hello.protocol}, this app speaks "
                f"v{protocol.PROTOCOL_VERSION}. Update the side that is behind."
            )
        if hello.profile_bytes not in binary.SUPPORTED_PROFILE_SIZES:
            raise DeviceError(
                f"device profile is {hello.profile_bytes} bytes, this app supports "
                f"{sorted(binary.SUPPORTED_PROFILE_SIZES)}. Firmware and app are out of step."
            )
        if hello.keys != KEY_COUNT or hello.leds != LED_COUNT:
            raise DeviceError(
                f"device reports {hello.keys} keys and {hello.leds} LEDs, this app expects "
                f"{KEY_COUNT} keys and {LED_COUNT} LEDs. Firmware and app are out of step."
            )
        with self._state_lock:
            if self._serial is not opened:
                raise DeviceError("serial link lost during device identification")
            try:
                open_for_io = bool(opened.is_open)
            except Exception as exc:
                raise DeviceError("serial link lost during device identification") from exc
            if not open_for_io:
                raise DeviceError("serial link lost during device identification")
            self.hello = hello
            self.port = port
        self._on_status(f"Connected to {port} (firmware {hello.firmware})")
        return hello

    def disconnect(self) -> None:
        self._connect_cancel.set()
        self._drop_link(notify=False)

    def _drop_link(
        self,
        reason: str = "Serial link lost",
        *,
        notify: bool,
        expected_port: Any | None = None,
    ) -> None:
        """Atomically makes a dead serial link look dead to every caller.

        The reader used to report a failure and exit while leaving `_serial`
        open and attached. `connected` therefore stayed true, requests leaked
        raw OSErrors, and a global recording could remain active because its
        watchdog believed the keypad was still present.
        """
        with self._state_lock:
            # A driver read can outlive its one-second join. If another port has
            # since connected, that stale reader must not tear the new link down.
            if expected_port is not None and self._serial is not expected_port:
                return
            stop = self._stop
            stop.set()
            reader, self._reader = self._reader, None
            port, self._serial = self._serial, None
            established = self.hello is not None
            self.hello = None
        if port is not None:
            try:
                port.close()
            except Exception:  # closing a vanished port is not worth reporting
                log.debug("ignoring error while closing serial port", exc_info=True)
        if (
            reader is not None
            and reader is not threading.current_thread()
            and reader.is_alive()
        ):
            reader.join(timeout=1.0)
        # Wake a request that was already waiting for a reply; otherwise a
        # vanished cable still costs the entire request timeout.
        self._responses.put(_LINK_LOST)
        if notify and established:
            self._on_status(reason)
            try:
                self._on_disconnect(reason)
            except Exception:  # noqa: BLE001 - observers cannot revive the link
                log.exception("disconnect callback raised")

    def _drain(self) -> None:
        try:
            with self._state_lock:
                port = self._serial
            if port is not None:
                port.reset_input_buffer()
        except Exception:
            log.debug("could not flush input buffer", exc_info=True)

    # --------------------------------------------------------------- reader --

    def _read_loop(self, port: Any, stop: threading.Event) -> None:
        buffer = b""
        while not stop.is_set():
            try:
                # Block for one byte, then take whatever else has already
                # arrived. Asking for a fixed 128 instead makes pyserial wait for
                # either 128 bytes or the full read timeout, and replies here are
                # four bytes long -- so every single request paid the timeout in
                # full. That put a 200 ms floor under every exchange and turned a
                # profile write into 20 round trips of nothing happening.
                chunk = port.read(max(1, port.in_waiting))
            except Exception as exc:
                if not stop.is_set():
                    self._drop_link(
                        f"Serial link lost: {exc}",
                        notify=True,
                        expected_port=port,
                    )
                break
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                raw, _, buffer = buffer.partition(b"\n")
                self._dispatch(raw.decode("ascii", errors="replace").strip())

    def _dispatch(self, line: str) -> None:
        if not line:
            return
        try:
            message = protocol.parse(line)
        except ProtocolError:
            log.debug("unparseable line from device: %r", line)
            return

        event = protocol.parse_event(message)
        if event is not None:
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception:  # noqa: BLE001 - observers must not kill serial input
                    log.exception("device event callback raised")
            return
        if message.verb == "LOG":
            payload = message.get("msg", "") or ""
            try:
                text = base64.b64decode(payload).decode("utf-8", errors="replace")
            except Exception:
                text = payload
            log.info("device: %s", text)
            return
        self._responses.put(message)

    # -------------------------------------------------------------- requests --

    def send(self, line: str) -> None:
        failure: Exception | None = None
        with self._write_lock:
            with self._state_lock:
                port = self._serial
            try:
                open_for_io = port is not None and bool(port.is_open)
            except Exception:
                open_for_io = False
            # IDENT itself must be sent before the public `connected` state can
            # become true, so check the transport here rather than that state.
            if not open_for_io:
                raise DeviceError("not connected")
            try:
                port.write((line + "\n").encode("ascii"))
            except Exception as exc:
                failure = exc
        # A disconnect observer may stop a recording, which releases the LED
        # and therefore attempts another send. Notify only after the write lock
        # is free so that cleanup cannot deadlock inside the failed write.
        if failure is not None:
            self._drop_link(
                f"Serial link lost: {failure}",
                notify=True,
                expected_port=port,
            )
            raise DeviceError(f"serial link lost: {failure}") from failure

    def request(
        self,
        line: str,
        expect: set[str] | None = None,
        timeout: float = RESPONSE_TIMEOUT,
    ) -> Message:
        """Sends `line` and waits for the first response with a matching verb."""
        expected = expect or {"OK"}
        with self._request_lock:
            self._flush_responses()
            self.send(line)
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeviceError(f"timed out waiting for {sorted(expected)} after {line!r}")
                try:
                    message = self._responses.get(timeout=remaining)
                except queue.Empty:
                    continue
                if message is _LINK_LOST:
                    raise DeviceError("serial link lost")
                assert isinstance(message, Message)
                if message.verb == "ERR":
                    raise DeviceError(f"device rejected {line!r}: {message.get('code')}")
                if message.verb in expected:
                    return message
                # Anything else is a stale reply from a previous exchange.

    def _flush_responses(self) -> None:
        while True:
            try:
                self._responses.get_nowait()
            except queue.Empty:
                return

    # -------------------------------------------------------- profile transfer --

    def read_profile(self) -> bytes:
        """Pulls the device's stored profile blob."""
        with self._profile_lock:
            return self._read_profile_locked()

    def _read_profile_locked(self) -> bytes:
        expected_size = self.hello.profile_bytes if self.hello is not None else binary.PROFILE_SIZE
        with self._request_lock:
            self._flush_responses()
            self.send(protocol.encode("PROF", "read"))
            deadline = time.monotonic() + PROFILE_READ_TIMEOUT
            chunks: dict[int, bytes] = {}
            declared_crc: int | None = None
            declared_bytes: int | None = None
            expected_sequence = 0

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeviceError("timed out reading the device profile")
                try:
                    message = self._responses.get(timeout=remaining)
                except queue.Empty:
                    continue
                if message is _LINK_LOST:
                    raise DeviceError("serial link lost")
                assert isinstance(message, Message)
                if message.verb != "PROF":
                    continue
                if message.sub == "begin":
                    if declared_bytes is not None or chunks:
                        raise DeviceError("device restarted the profile transfer unexpectedly")
                    try:
                        declared_bytes = int(message.get("bytes", "") or "", 10)
                        declared_crc = int(message.get("crc", "") or "", 16)
                    except ValueError as exc:
                        raise DeviceError("device sent a malformed profile header") from exc
                    if declared_bytes != expected_size:
                        raise DeviceError(
                            f"device declared {declared_bytes} profile bytes, "
                            f"expected {expected_size}"
                        )
                elif message.sub == "data":
                    if declared_bytes is None:
                        raise DeviceError("device sent profile data before its header")
                    sequence = message.int("seq", -1)
                    payload = message.get("b64", "") or ""
                    if sequence != expected_sequence:
                        raise DeviceError(
                            f"device sent profile chunk {sequence}, expected {expected_sequence}"
                        )
                    try:
                        chunk = base64.b64decode(payload, validate=True)
                    except ValueError as exc:
                        raise DeviceError(
                            f"device sent invalid base64 in profile chunk {sequence}"
                        ) from exc
                    offset = expected_sequence * binary.CHUNK_BYTES
                    if offset >= expected_size:
                        raise DeviceError(f"device sent unexpected extra profile chunk {sequence}")
                    expected_length = min(
                        binary.CHUNK_BYTES,
                        expected_size - offset,
                    )
                    if len(chunk) != expected_length:
                        raise DeviceError(
                            f"device sent {len(chunk)} bytes in profile chunk {sequence}, "
                            f"expected {expected_length}"
                        )
                    chunks[sequence] = chunk
                    expected_sequence += 1
                elif message.sub == "end":
                    if declared_bytes is None or declared_crc is None:
                        raise DeviceError("device ended the profile transfer without a header")
                    break

        blob = b"".join(chunks[index] for index in sorted(chunks))
        if len(blob) != expected_size:
            raise DeviceError(f"device sent {len(blob)} bytes, expected {expected_size}")
        if binary.blob_crc(blob) != declared_crc:
            raise DeviceError("device profile failed its checksum")
        return blob

    def write_profile(self, blob: bytes) -> None:
        """Stages the blob and commits it only if the device agrees on the CRC."""
        expected_size = self.hello.profile_bytes if self.hello is not None else binary.PROFILE_SIZE
        if len(blob) != expected_size:
            raise DeviceError(f"profile must be {expected_size} bytes")
        with self._profile_lock:
            self._write_profile_locked(blob)

    def _write_profile_locked(self, blob: bytes) -> None:
        crc = binary.blob_crc(blob)
        self.request(protocol.encode("PROF", "begin", bytes=len(blob), crc=f"{crc:04X}"))
        for sequence, offset in enumerate(range(0, len(blob), binary.CHUNK_BYTES)):
            payload = base64.b64encode(blob[offset : offset + binary.CHUNK_BYTES]).decode("ascii")
            self.request(protocol.encode("PROF", "data", seq=sequence, b64=payload))
        self.request(protocol.encode("PROF", "commit"), timeout=EEPROM_WRITE_TIMEOUT)
        self._on_status("Profile written to device")

    # ------------------------------------------------------------------ leds --

    def set_led_mode(self, host: bool, timeout_ms: int | None = None) -> None:
        """Takes or releases the ambient layer.

        `timeout_ms` declares how long this host may be silent before the device
        should assume it is gone and fall back to its own scene. Callers that
        hold a colour still while a person looks at it should say so here rather
        than send keepalives; the device default is deliberately short.
        """
        if not host:
            self.request(protocol.encode("LED", mode="local"))
            return
        fields: dict[str, object] = {"mode": "host"}
        if timeout_ms is not None:
            fields["ms"] = max(0, min(60000, int(timeout_ms)))
        self.request(protocol.encode("LED", **fields))

    def set_brightness(self, value: int) -> None:
        self.request(protocol.encode("LED", bright=max(0, min(255, value))))

    def set_all(self, color: tuple[int, int, int], effect: str = "solid", period: int = 0) -> None:
        self.request(
            protocol.encode(
                "LED", "all", rgb=protocol.rgb_hex(color), fx=effect, ms=period or None
            )
        )

    def set_pixel(
        self, index: int, color: tuple[int, int, int], effect: str = "solid", period: int = 0
    ) -> None:
        self.request(
            protocol.encode(
                "LED", i=index, rgb=protocol.rgb_hex(color), fx=effect, ms=period or None
            )
        )

    def set_frame(self, colors: list[tuple[int, int, int]]) -> None:
        self.request(protocol.encode("LED", frame=protocol.frame_arg(colors)))

    def set_bar(self, percent: int, color: tuple[int, int, int]) -> None:
        self.request(
            protocol.encode(
                "LED", bar=max(0, min(100, percent)), rgb=protocol.rgb_hex(color)
            )
        )

    # ----------------------------------------------------------------- misc --

    def home_pointer(self) -> None:
        """Drives the pointer into the top-left corner, from the pad itself.

        The pad is a real USB mouse, so this works where synthesising a move
        from the host does not -- under Wayland nothing outside the compositor
        may place the cursor. Fixed-screen mode asks for this before capture so
        relative movement is measured from a known origin and can be replayed
        back to the same place on an unchanged desktop.
        """
        self.request(protocol.encode("MOUSE", "home"))

    def ping(self) -> None:
        self.request(protocol.encode("PING"))

    def save(self) -> None:
        self.request(protocol.encode("SAVE"))

    def reset_defaults(self) -> None:
        self.request(protocol.encode("RESET", defaults=1), timeout=EEPROM_WRITE_TIMEOUT)
