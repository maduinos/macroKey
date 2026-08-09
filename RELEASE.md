# Release Process

This is a personal macro keypad lab repository. Releases are optional.

## Before Release

1. `pytest` -- includes `test_firmware_agreement.py`, which compiles the real
   firmware sources for this machine and checks that the host encoder and the
   firmware's own reader agree about the same 1024 bytes. It skips rather than
   fails without a C++ compiler, so confirm it actually ran.
2. `ruff check .`
3. `arduino-cli compile --fqbn SparkFun:avr:promicro:cpu=16MHzatmega32U4 --warnings all firmware`,
   and the same for `arduino:avr:leonardo` and `arduino:avr:micro`. Note the
   flash and SRAM figures: the staging buffer leaves about 200 bytes of stack
   headroom, and `Profile.h` records how that was measured.
4. Flash a real pad with `compile --upload`, not `upload` on its own -- `upload`
   sends the last build output and will happily flash a stale binary. Check the
   version it reports back. Then record something and replay it: the harness
   proves the bytes, only the pad proves the recording.
5. Bump the version in `pyproject.toml`, `macrokey/__init__.py` and
   `firmware/src/Config.h` together, and `MK_PROFILE_SCHEMA` / `binary.SCHEMA`
   if the EEPROM layout moved -- the blob is the same size either way, so
   without the bump an out-of-date pad decodes as nonsense.
6. Update `CHANGELOG.md`, `README.md`, `manual.html` and `docs/`.
7. Do not commit generated Arduino build output or local app configuration.
