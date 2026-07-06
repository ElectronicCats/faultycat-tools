"""faultycmd — host tool for FaultyCat v3 (BSD-3-Clause).

The CLI entry point lives in :mod:`faultycmd.core.cli`; the TUI in
:mod:`faultycmd.tui.app`. Wire-protocol primitives shared across all
clients live in :mod:`faultycmd.core.framing` and per-protocol modules
under :mod:`faultycmd.protocols`.
"""

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _read_version_file() -> str | None:
    # Checks the PyInstaller bundle root first, then the repo's VERSION
    # file (this package lives at <repo>/src/faultycmd/__init__.py).
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "VERSION",
        Path(__file__).resolve().parents[2] / "VERSION",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return None


try:
    __version__ = version("faultycmd")
except PackageNotFoundError:
    __version__ = _read_version_file() or "0.0.0"
