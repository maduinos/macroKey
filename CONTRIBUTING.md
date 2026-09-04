> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-05-30<br>
> https://maduinos.blogspot.com/

# Contributing

```bash
python -m pip install -r requirements.txt
pytest
./build_release.sh --skip-tests
```

Bump version in `pyproject.toml`, `macrokey/__init__.py`, and `firmware/src/Config.h`
together before a release. Flash with `compile --upload`.

Do not commit `.build/`, `releases/`, or local `profile.json`.
