"""Where the app keeps its settings and undo logs.

Normally that is the per-user application data folder. The portable ZIP build
ships a ``portable.txt`` marker next to the executable, which moves everything
into a ``data`` folder inside the app directory instead, so extracting the ZIP
to a USB stick leaves nothing behind on the machine.

If the app directory turns out to be read-only -- run straight from Program
Files, a network share, or inside the ZIP itself -- we fall back to the normal
per-user folder rather than failing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_FOLDER_NAME = "PhotoTimestampEditor"
PORTABLE_MARKER = "portable.txt"


def app_directory() -> Path:
    """The folder holding the executable, or the project root when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_data_directory() -> Path:
    """The conventional per-user location for this app's data."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_FOLDER_NAME


def is_portable() -> bool:
    """True when this copy should keep its data beside the executable."""
    marker = app_directory() / PORTABLE_MARKER
    if not marker.is_file():
        return False
    return _is_writable(app_directory())


def _is_writable(directory: Path) -> bool:
    probe = directory / ".write-test"
    try:
        probe.touch()
        probe.unlink()
    except OSError:
        return False
    return True


def data_directory() -> Path:
    """The folder for undo logs and settings, created if it does not exist."""
    base = app_directory() / "data" if is_portable() else user_data_directory()
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_file() -> Path:
    """Where a portable build stores its window settings, as a plain INI file."""
    return data_directory() / "settings.ini"
