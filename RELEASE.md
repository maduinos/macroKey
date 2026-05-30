# Release Process

This is a personal macro keypad lab repository. Releases are optional.

## Before Release

1. Compile with Arduino IDE or `arduino-cli compile --fqbn arduino:avr:leonardo arduino/05_macrokey`.
2. Run `python3 -m py_compile python_app/macro_key_app.py`.
3. Update `CHANGELOG.md`.
4. Confirm README pin mapping, GUI behavior, and HID safety notes are accurate.
5. Do not commit generated Arduino build output or local app configuration.
