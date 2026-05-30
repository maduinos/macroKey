# macroKey

[![Arduino Compile](https://github.com/maduinos/macroKey/actions/workflows/arduino.yml/badge.svg)](https://github.com/maduinos/macroKey/actions/workflows/arduino.yml)

Personal Arduino HID macro keypad sketch by Maduinos.

This is a hobby/lab project and is not part of the Maduinos FPGA business portfolio.

## What It Does

The sketch reads eight active-low button inputs and sends `Tab + number` keyboard shortcuts through the Arduino `Keyboard` library.

| Pin | Shortcut |
| --- | --- |
| D3 | `Tab + 1` |
| D4 | `Tab + 2` |
| D5 | `Tab + 3` |
| D6 | `Tab + 4` |
| D7 | `Tab + 5` |
| D8 | `Tab + 6` |
| D9 | `Tab + 7` |
| D10 | `Tab + 8` |

## Requirements

- Arduino board that supports native USB HID keyboard mode, such as Leonardo, Micro, or Pro Micro
- Arduino IDE or `arduino-cli`

## Build

Open `05_macrokey.ino` in Arduino IDE and select a USB HID capable board.

If `arduino-cli` is installed:

```bash
arduino-cli compile --fqbn arduino:avr:leonardo .
```

## Safety

The sketch sends keyboard input to the host computer. Upload and test it in an environment where unexpected keyboard shortcuts are safe.

## License

MIT License. See `LICENSE`.

## Project Management

- Changes: `CHANGELOG.md`
- Support scope: `SUPPORT.md`
- Contribution guide: `CONTRIBUTING.md`
- Security reporting: `SECURITY.md`
