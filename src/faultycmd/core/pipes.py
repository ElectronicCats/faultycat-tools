"""OS-specific lookup for external GUI tools faultycmd shells out to.

`shutil.which` alone only finds tools that are on PATH, which covers
Linux package installs but misses the default install locations on
Windows and macOS (Program Files, /Applications).
"""

from __future__ import annotations

import platform
import shutil
from pathlib import Path


def get_pulseview_path() -> Path | None:
    """Find the PulseView executable on Windows, Linux, or macOS.

    Checks each platform's default install location first, then falls
    back to PATH (covers Homebrew, Linux package managers, and other
    installs that already export it).
    """
    system = platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        candidates = [
            Path("C:\\Program Files\\sigrok\\PulseView\\pulseview.exe"),
            Path("C:\\Program Files (x86)\\sigrok\\PulseView\\pulseview.exe"),
            Path("C:\\Program Files\\PulseView\\pulseview.exe"),
            Path("C:\\Program Files (x86)\\PulseView\\pulseview.exe"),
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/PulseView.app/Contents/MacOS/pulseview"),
            Path("/opt/homebrew/bin/pulseview"),
            Path("/usr/local/bin/pulseview"),
        ]
    elif system == "Linux":
        candidates = [
            Path("/usr/bin/pulseview"),
            Path("/usr/local/bin/pulseview"),
        ]

    for path in candidates:
        if path.exists():
            return path

    on_path = shutil.which("pulseview")
    return Path(on_path) if on_path else None
