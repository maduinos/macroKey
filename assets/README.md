# assets

`app_icon.png` (1024px) and `app_icon.ico` (16~256px) are **generated** — edit
`tools/make_icon.py` and re-run it rather than editing the images:

```bash
python3 tools/make_icon.py
```

`build_release.sh` passes them to PyInstaller as the executable's icon, and
bundles the PNG so the running app can set its window icon.
