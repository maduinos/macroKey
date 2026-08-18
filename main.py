#!/usr/bin/env python3
"""Launcher for the macroKey config app (GUI only)."""

from __future__ import annotations

from macrokey.ui import run_gui


def main() -> int:
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
