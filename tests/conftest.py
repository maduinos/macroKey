"""The UI tests build real widgets, so Qt needs a platform it can use headless."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
