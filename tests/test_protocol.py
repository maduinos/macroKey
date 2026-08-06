"""Wire format codec. Pure functions, so no device is needed to run these."""

import pytest

from macrokey.device import protocol
from macrokey.device.protocol import ChordEvent, Hello, HostEvent, KeyEvent


class TestMessageInt:
    def test_zero_survives(self):
        """Regression: ``message.int(k, -1) or -1`` turned a valid 0 into -1."""
        message = protocol.parse("EV t=host tok=0 k=0 l=0")
        assert message.int("tok", -1) == 0
        assert message.int("k", -1) == 0

    def test_missing_and_malformed_fall_back(self):
        message = protocol.parse("EV t=host tok=zz")
        assert message.int("tok", -1) == -1
        assert message.int("absent", 7) == 7


class TestParseEvent:
    def test_host_token_zero(self):
        """Key 5 of the default media layer carries token 0; it must dispatch."""
        event = protocol.parse_event(protocol.parse("EV t=host tok=0 k=4 l=1"))
        assert event == HostEvent(token=0, key=4, layer=1)

    def test_key_zero(self):
        event = protocol.parse_event(protocol.parse("EV t=key k=0 g=tap l=0 ms=1200"))
        assert event == KeyEvent(key=0, gesture="tap", layer=0, uptime_ms=1200)

    def test_unparseable_token_is_negative(self):
        event = protocol.parse_event(protocol.parse("EV t=host tok=zz k=1 l=0"))
        assert event.token == -1

    def test_hold_release_keeps_its_own_gesture_name(self):
        event = protocol.parse_event(protocol.parse("EV t=key k=7 g=holdend l=0 ms=9"))
        assert event.gesture == "holdend"

    def test_chord_mask_is_hex(self):
        event = protocol.parse_event(protocol.parse("EV t=chord m=03 l=0"))
        assert event == ChordEvent(keys=[0, 1], layer=0)

    def test_non_event_lines_are_not_events(self):
        assert protocol.parse_event(protocol.parse("OK")) is None


class TestHello:
    def test_fields(self):
        line = "HELLO proto=1 fw=0.3.0 board=promicro keys=8 leds=1 layers=4 bytes=932"
        hello = Hello.from_message(protocol.parse(line))
        assert hello == Hello(1, "0.3.0", "promicro", 8, 1, 4, 932)
        assert hello.compatible

    def test_future_protocol_is_incompatible(self):
        hello = Hello.from_message(protocol.parse("HELLO proto=2 fw=9 board=x"))
        assert not hello.compatible


class TestEncode:
    def test_drops_none_and_keeps_order(self):
        assert protocol.encode("LED", "all", rgb="FF0000", fx="pulse", ms=None) == (
            "LED all rgb=FF0000 fx=pulse"
        )

    def test_refuses_an_overlong_line(self):
        with pytest.raises(protocol.ProtocolError):
            protocol.encode("LED", frame="A" * 200)
