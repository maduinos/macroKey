# Contributing

```bash
python -m pip install -r requirements.txt
pytest
./build_release.sh --skip-tests
```

Bump version in `pyproject.toml`, `macrokey/__init__.py`, and `firmware/src/Config.h`
together before a release. Flash with `compile --upload`.

Do not commit `.build/`, `releases/`, or local `profile.json`.
