"""A small on-disk log.

A windowed build has no console, so a failure on someone else's machine leaves
nothing behind unless it is written down. The log lives beside the rest of the
app's data and is capped so it cannot grow without bound.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import paths

MAX_BYTES = 512 * 1024


def log_file() -> Path:
    directory = paths.data_directory() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "app.log"


def write(message: str) -> None:
    """Append a timestamped line. Never raises -- logging must not be the failure."""
    try:
        path = log_file()
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_suffix(".log.old"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat(timespec='seconds')}  {message}\n")
    except Exception:  # noqa: BLE001
        pass


def write_exception(prefix: str, error: BaseException) -> None:
    details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    write(f"{prefix}\n{details.rstrip()}")


def install_excepthook() -> None:
    """Record unhandled exceptions instead of letting them vanish."""
    previous = sys.excepthook

    def hook(kind, value, tb) -> None:
        write_exception("unhandled exception", value)
        try:
            previous(kind, value, tb)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = hook
