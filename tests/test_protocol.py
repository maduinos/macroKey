"""Wire format codec. Pure functions, so no device is needed to run these."""

from macrokey.device import protocol
from macrokey.device.protocol import Hello, KeyEvent, RecordRequest


class TestMessageInt:
    def test_zero_survives(self):
        """Regression: ``message.int(k, -1) or -1`` turned a valid 0 into -1."""
        message = protocol.parse("EV t=host tok=0 k=0")
        assert message.int("tok", -1) == 0
        assert message.int("k", -1) == 0

    def test_missing_and_malformed_fall_back(self):
        message = protocol.parse("EV t=host tok=zz")
        assert message.int("tok", -1) == -1
        assert message.int("absent", 7) == 7


class TestParseEvent:
    def test_host_token_is_ignored(self):
        """Retired EV t=host: pad is HID-only; old pads may still emit it."""
        assert protocol.parse_event(protocol.parse("EV t=host tok=0 k=4")) is None

    def test_key_zero(self):
        event = protocol.parse_event(protocol.parse("EV t=key k=0 g=tap ms=1200"))
        assert event == KeyEvent(key=0, gesture="tap", uptime_ms=1200)

    def test_hold_release_keeps_its_own_gesture_name(self):
        event = protocol.parse_event(protocol.parse("EV t=key k=7 g=holdend ms=9"))
        assert event.gesture == "holdend"

    def test_record_request(self):
        event = protocol.parse_event(protocol.parse("EV t=record k=2 g=tap ms=50"))
        assert event == RecordRequest(key=2, gesture="tap", uptime_ms=50)

    def test_non_event_lines_are_not_events(self):
        assert protocol.parse_event(protocol.parse("OK")) is None


class TestHello:
    def test_fields(self):
        line = "HELLO proto=1 fw=0.5.0 board=promicro keys=8 leds=1 bytes=1024"
        hello = Hello.from_message(protocol.parse(line))
        assert hello.protocol == 1
        assert hello.firmware == "0.5.0"
        assert hello.board == "promicro"
        assert hello.keys == 8
        assert hello.leds == 1
        assert hello.profile_bytes == 1024
