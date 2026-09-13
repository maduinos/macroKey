> 만든 사람: maduinos<br>
> 문서 만든 날짜: 2026-05-30<br>
> https://maduinos.blogspot.com/

# Contributing

## 먼저: CLA

PR을 보내려면 [`CLA.md`](CLA.md)에 한 번 동의해야 합니다. PR을 열면 봇이 안내하고,
그 PR에 아래 문장을 댓글로 남기면 서명됩니다. **한 번 서명하면 이후 PR에는 다시 묻지
않습니다.**

```
I have read the CLA Document and I hereby sign the CLA
```

기여물의 저작권은 기여자에게 그대로 남습니다(양도가 아닙니다). 다만 이 프로젝트가
나중에 라이선스를 바꾸거나 상업 라이선스를 따로 제공할 수 있으려면 그 권한을 미리
받아 둬야 하고, 그것은 **소급해서 받을 수 없습니다.** 자세한 것은 `CLA.md`에 있습니다.

## 빌드

빌드·실행·릴리스 절차는 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)에 있습니다.

```bash
python -m pip install -r requirements.txt
pytest
./build_release.sh --skip-tests
```

Bump version in `pyproject.toml`, `macrokey/__init__.py`, and `firmware/src/Config.h`
together before a release. Flash with `compile --upload`.

Do not commit `.build/`, `releases/`, or local `profile.json`.
