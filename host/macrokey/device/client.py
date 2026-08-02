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

from ..config import binary
from . import discovery, protocol
from .protocol import ChordEvent, Hello, HostEvent, KeyEvent, Message, ProtocolError

try:
    import serial
except ModuleNotFoundError:  # pragma: no cover - exercised only without pyserial
    serial = None

log = logging.getLogger(__name__)

DeviceEvent = KeyEvent | HostEvent | ChordEvent
EventCallback = Callable[[DeviceEvent], None]
StatusCallback = Callable[[str], None]

# The Leonardo re-enumerates when the port opens, so give it time before IDENT.
OPEN_SETTLE_SECONDS = 2.0
RESPONSE_TIMEOUT = 2.0
PROFILE_READ_TIMEOUT = 8.0


class DeviceError(RuntimeError):
    """Raised when the device is absent, incompatible or uncooperative."""


class DeviceClient:
    def __init__(
        self,
        on_event: EventCallback | None = None,
        on_status: StatusCallback | None = None,
    ) -> None:
        self._on_event = on_event
        self._on_status = on_status or (lambda message: None)
        self._serial: Any = None
        self._reader: threading.Thread | None = None
        self._responses: queue.Queue[Message] = queue.Queue()
        self._write_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._stop = threading.Event()
        self.hello: Hello | None = None
        self.port: str = ""

    # ------------------------------------------------------------ lifecycle --

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self, port: str = "") -> Hello:
        """Opens `port`, or probes every candidate when it is empty."""
        if serial is None:
            raise DeviceError("pyserial is not installed: pip install pyserial")

        ports = [port] if port else [item.device for item in discovery.candidates()]
        if not ports:
            raise DeviceError("no serial ports found")

        errors: list[str] = []
        for candidate in ports:
            try:
                return self._connect_one(candidate)
            except DeviceError as exc:
                errors.append(f"{candidate}: {exc}")
                self.disconnect()
        raise DeviceError("no macroKey device answered.\n" + "\n".join(errors))

    def _connect_one(self, port: str) -> Hello:
        self._stop.clear()
        self._on_status(f"Opening {port}")
        try:
            self._serial = serial.Serial(port, 115200, timeout=0.2)
        except Exception as exc:  # pyserial raises several unrelated types
            raise DeviceError(str(exc)) from exc

        time.sleep(OPEN_SETTLE_SECONDS)
        self._drain()
        self._reader = threading.Thread(target=self._read_loop, name="macrokey-serial", daemon=True)
        self._reader.start()

        response = self.request(protocol.encode("IDENT"), expect={"HELLO"})
        hello = Hello.from_message(response)
        if not hello.compatible:
            raise DeviceError(
                f"device speaks protocol v{hello.protocol}, this app speaks "
                f"v{protocol.PROTOCOL_VERSION}. Update the side that is behind."
            )
        if hello.profile_bytes != binary.PROFILE_SIZE:
            raise DeviceError(
                f"device profile is {hello.profile_bytes} bytes, this app builds "
                f"{binary.PROFILE_SIZE}. Firmware and app are out of step."
            )
        self.hello = hello
        self.port = port
        self._on_status(f"Connected to {port} (firmware {hello.firmware})")
        return hello

    def disconnect(self) -> None:
        self._stop.set()
        reader, self._reader = self._reader, None
        port, self._serial = self._serial, None
        if port is not None:
            try:
                port.close()
            except Exception:  # closing a vanished port is not worth reporting
                log.debug("ignoring error while closing serial port", exc_info=True)
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        self.hello = None

    def _drain(self) -> None:
        try:
            self._serial.reset_input_buffer()
        except Exception:
            log.debug("could not flush input buffer", exc_info=True)

    # --------------------------------------------------------------- reader --

    def _read_loop(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = self._serial.read(128)
            except Exception:
                if not self._stop.is_set():
                    self._on_status("Serial link lost")
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
                self._on_event(event)
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
        if not self.connected:
            raise DeviceError("not connected")
        with self._write_lock:
            self._serial.write((line + "\n").encode("ascii"))

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
        with self._request_lock:
            self._flush_responses()
            self.send(protocol.encode("PROF", "read"))
            deadline = time.monotonic() + PROFILE_READ_TIMEOUT
            chunks: dict[int, bytes] = {}
            declared_crc: int | None = None

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeviceError("timed out reading the device profile")
                try:
                    message = self._responses.get(timeout=remaining)
                except queue.Empty:
                    continue
                if message.verb != "PROF":
                    continue
                if message.sub == "begin":
                    declared_crc = int(message.get("crc", "0") or "0", 16)
                elif message.sub == "data":
                    sequence = message.int("seq", -1)
                    payload = message.get("b64", "") or ""
                    if sequence is None or sequence < 0:
                        continue
                    chunks[sequence] = base64.b64decode(payload)
                elif message.sub == "end":
                    break

        blob = b"".join(chunks[index] for index in sorted(chunks))
        if len(blob) != binary.PROFILE_SIZE:
            raise DeviceError(f"device sent {len(blob)} bytes, expected {binary.PROFILE_SIZE}")
        if declared_crc is not None and binary.blob_crc(blob) != declared_crc:
            raise DeviceError("device profile failed its checksum")
        return blob

    def write_profile(self, blob: bytes) -> None:
        """Stages the blob and commits it only if the device agrees on the CRC."""
        if len(blob) != binary.PROFILE_SIZE:
            raise DeviceError(f"profile must be {binary.PROFILE_SIZE} bytes")

        crc = binary.blob_crc(blob)
        self.request(protocol.encode("PROF", "begin", bytes=len(blob), crc=f"{crc:04X}"))
        for sequence, offset in enumerate(range(0, len(blob), binary.CHUNK_BYTES)):
            payload = base64.b64encode(blob[offset : offset + binary.CHUNK_BYTES]).decode("ascii")
            self.request(protocol.encode("PROF", "data", seq=sequence, b64=payload))
        self.request(protocol.encode("PROF", "commit"))
        self._on_status("Profile written to device")

    # ------------------------------------------------------------------ leds --

    def set_led_mode(self, host: bool) -> None:
        self.request(protocol.encode("LED", mode="host" if host else "local"))

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
        self.request(protocol.encode("LED", bar=max(0, min(100, percent)), rgb=protocol.rgb_hex(color)))

    # ----------------------------------------------------------------- misc --

    def set_layer(self, layer: int) -> None:
        self.request(protocol.encode("LAYER", set=layer))

    def ping(self) -> None:
        self.request(protocol.encode("PING"))

    def save(self) -> None:
        self.request(protocol.encode("SAVE"))

    def reset_defaults(self) -> None:
        self.request(protocol.encode("RESET", defaults=1))
