#!/usr/bin/env python3
"""Draw the macroKey app icon: assets/app_icon.png and assets/app_icon.ico.

The icon is the product seen from above -- an eight-key pad with its single
WS2812B pixel below the keys. Key 1 and the pixel are lit the same red because
that is the pad's one non-obvious behaviour: hold a key and its pixel turns red
to say it is recording into that key.

Run it after changing anything here; the two files it writes are what
``build_release.sh`` picks up (``--icon`` on both platforms, and the PNG is
bundled so the running app can set its window icon).

    python3 -m pip install --user Pillow   # only to regenerate; not a build dep
    python3 tools/make_icon.py

Pillow deliberately stays out of ``requirements.txt`` -- ``build_release.sh``
excludes PIL from the binary, and the generated files are committed, so nobody
building a release needs it.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# Drawn at 4x the master size and downsampled, which is cheaper than hand-rolled
# antialiasing and keeps the rounded corners clean.
MASTER = 1024
SCALE = 4

BODY = (0x23, 0x28, 0x2F, 0xFF)
BODY_RIM = (0x3A, 0x42, 0x4D, 0xFF)
KEY = (0xDD, 0xE2, 0xEA, 0xFF)
KEY_SHADOW = (0x12, 0x15, 0x1A, 0x99)
ACCENT = (0xE0, 0x4B, 0x36, 0xFF)
ACCENT_GLOW = (0xE0, 0x4B, 0x36, 0x38)

# ICO sizes Windows actually picks from: 16/32 in lists and the taskbar, 48 in
# Explorer, 256 for the large views and the exe's own properties page.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size: int) -> Image.Image:
    """The icon at `size`, drawn oversampled and reduced back down."""
    s = size * SCALE
    u = s / 1024  # one master pixel, in this canvas's units
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)

    # Below this the real 4x2 grid stops being eight keys and becomes a smear:
    # four columns cannot survive in sixteen pixels. The small variant keeps the
    # things that identify the icon -- a grid, one lit key, the pixel -- and
    # spends every pixel it has on making them separable.
    compact = size < 32
    inset, radius = (48, 200) if compact else (64, 176)
    body = (inset * u, inset * u, (1024 - inset) * u, (1024 - inset) * u)
    pen.rounded_rectangle(body, radius=radius * u, fill=BODY)
    pen.rounded_rectangle(body, radius=radius * u, outline=BODY_RIM, width=max(1, round(8 * u)))

    cols, key_w, gap, left, top, cap_r = (
        (2, 240, 64, 240, 176, 56) if compact else (4, 140, 48, 160, 268, 34)
    )
    for row in range(2):
        for col in range(cols):
            x = (left + col * (key_w + gap)) * u
            y = (top + row * (key_w + gap)) * u
            span = key_w * u
            pen.rounded_rectangle(
                (x, y + 10 * u, x + span, y + span + 10 * u), radius=cap_r * u, fill=KEY_SHADOW
            )
            lit = row == 0 and col == 0
            pen.rounded_rectangle(
                (x, y, x + span, y + span), radius=cap_r * u, fill=ACCENT if lit else KEY
            )

    # The one pixel, centred under the keys. The glow is what keeps it reading as
    # a light rather than one more key.
    cx = 512 * u
    cy = (830 if compact else 760) * u
    dot = (52 if compact else 44) * u
    halo = (104 if compact else 96) * u
    pen.ellipse((cx - halo, cy - halo, cx + halo, cy + halo), fill=ACCENT_GLOW)
    pen.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=ACCENT)

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    master = draw(MASTER)
    png = ASSETS / "app_icon.png"
    master.save(png)

    ico = ASSETS / "app_icon.ico"
    # Each size is drawn at its own scale rather than resized from the master:
    # a 16px icon reduced 64:1 turns to mush, drawn at 64px and reduced 4:1 it
    # keeps its edges.
    frames = [draw(n) for n in ICO_SIZES]
    frames[-1].save(ico, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=frames[:-1])

    frames_shown = "/".join(str(n) for n in ICO_SIZES)
    print(f"{png.relative_to(ROOT)}  {MASTER}x{MASTER}  {png.stat().st_size:,} B")
    print(f"{ico.relative_to(ROOT)}  {frames_shown}  {ico.stat().st_size:,} B")


if __name__ == "__main__":
    main()
