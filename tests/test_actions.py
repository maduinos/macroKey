"""The host action registry, including what the editor reads to build a form."""

from macrokey.actions import create, handler_class, registered_types
from macrokey.config import HostAction

EDITOR_KINDS = ("text", "multiline", "int", "bool", "json")


class TestRegistry:
    def test_the_documented_types_are_all_present(self):
        assert set(registered_types()) >= {
            "hotkey",
            "text",
            "clipboard_image",
            "shell",
            "sequence",
            "delay",
            "mouse_button",
            "mouse_wheel",
            "noop",
            "stop",
        }

    def test_unknown_type_names_the_known_ones(self):
        import pytest

        from macrokey.actions import ActionError

        with pytest.raises(ActionError, match="shell"):
            create(HostAction(type="nope"))


class TestParamSpec:
    """The editor draws a form straight off these, so they have to hold up."""

    def test_every_handler_is_resolvable_and_well_formed(self):
        for type_name in registered_types():
            handler = handler_class(type_name)
            assert handler is not None
            keys = [param.key for param in handler.param_spec]
            assert len(keys) == len(set(keys)), f"{type_name} repeats a param key"
            for param in handler.param_spec:
                assert param.kind in EDITOR_KINDS, f"{type_name}.{param.key}: {param.kind}"
                assert param.key and param.label

    def test_declared_params_match_what_the_handler_reads(self):
        """A field the editor cannot show is a field the user cannot set."""
        expected = {
            "hotkey": {"hotkey", "hold_ms"},
            "text": {"text"},
            "delay": {"ms"},
            "clipboard_image": {"path", "paste", "press_enter"},
            "shell": {"command", "cwd"},
            "sequence": {"steps"},
        }
        for type_name, params in expected.items():
            assert {p.key for p in handler_class(type_name).param_spec} == params

    def test_unknown_handler(self):
        assert handler_class("does_not_exist") is None
