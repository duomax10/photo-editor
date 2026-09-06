"""Persist undo records so a shift can be reverted after the app is closed."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .core import UndoRecord

MAX_RECORDS = 50


def undo_directory() -> Path:
    """Where undo logs live, outside the photo folder so nothing is added to it."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    directory = base / "PhotoTimestampEditor" / "undo"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save(record: UndoRecord) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = undo_directory() / f"{stamp}.json"
    path.write_text(json.dumps(asdict(record), indent=1), encoding="utf-8")
    _prune()
    return path


def list_records() -> list[Path]:
    """Undo logs, newest first."""
    return sorted(undo_directory().glob("*.json"), reverse=True)


def load(path: Path) -> UndoRecord:
    data = json.loads(path.read_text(encoding="utf-8"))
    return UndoRecord(**data)


def latest() -> tuple[Path, UndoRecord] | None:
    records = list_records()
    if not records:
        return None
    return records[0], load(records[0])


def _prune() -> None:
    """Keep the log directory from growing without bound.

    Only ever removes our own JSON logs -- never anything in a photo folder.
    """
    for stale in list_records()[MAX_RECORDS:]:
        stale.unlink(missing_ok=True)
