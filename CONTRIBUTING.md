# Contributing

This repository is a personal Arduino HID macro keypad and Python macro app lab.

## Scope

Good contributions include:

- Documentation improvements.
- Safer HID behavior.
- Pin mapping clarification.
- Arduino build maintenance.
- Python GUI binding improvements.

Out of scope:

- Generated Arduino build artifacts.
- Changes that assume a board without native USB HID keyboard support.
- Changes that make the Python app write generated config into the repository tree.

## Checklist

- If `arduino-cli` is available, run `arduino-cli compile --fqbn arduino:avr:leonardo arduino/05_macrokey`.
- Run `python3 -m py_compile python_app/macro_key_app.py`.
- Confirm the sketch does not send unintended keyboard shortcuts during local verification.
