# Contributing

This repository is a personal Arduino HID macro keypad lab.

## Scope

Good contributions include:

- Documentation improvements.
- Safer HID behavior.
- Pin mapping clarification.
- Arduino build maintenance.

Out of scope:

- Generated Arduino build artifacts.
- Changes that assume a board without native USB HID keyboard support.

## Checklist

- If `arduino-cli` is available, run `arduino-cli compile --fqbn arduino:avr:leonardo .`.
- Confirm the sketch does not send unintended keyboard shortcuts during testing.

