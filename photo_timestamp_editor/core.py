"""Scanning a folder, planning a shift, and applying it.

The GUI never touches files directly: it builds a :class:`ShiftPlan`, shows it
to the user, and only then calls :func:`apply_plan`. Applying always produces an
:class:`UndoRecord` so the change can be rolled back exactly.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from . import exif
from .exif import BytePatch, ExifDateInfo, ExifError
from .filetimes import FileTimes, read_file_times, write_file_times

ProgressCallback = Callable[[int, int, str], None]


@dataclass
class PhotoEntry:
    """One scanned file, with whatever timestamps we could read from it."""

    path: Path
    file_times: FileTimes
    exif_info: ExifDateInfo | None = None
    exif_error: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def exif_datetime(self) -> datetime | None:
        field_ = self.exif_info.primary if self.exif_info else None
        return field_.value if field_ else None

    @property
    def exif_status(self) -> str:
        if self.exif_datetime is not None:
            return "ok"
        if self.exif_error:
            return "unreadable"
        return "no date"


@dataclass
class PlannedChange:
    """What will happen to a single file, precomputed for preview and apply."""

    entry: PhotoEntry
    exif_patches: list[BytePatch] = field(default_factory=list)
    new_exif_datetime: datetime | None = None
    new_modified: datetime | None = None
    new_accessed: datetime | None = None
    new_created: datetime | None = None
    skipped_reason: str = ""

    @property
    def will_change(self) -> bool:
        return bool(self.exif_patches) or self.new_modified is not None


@dataclass
class ShiftPlan:
    delta: timedelta
    update_exif: bool
    update_file_dates: bool
    changes: list[PlannedChange] = field(default_factory=list)

    @property
    def actionable(self) -> list[PlannedChange]:
        return [c for c in self.changes if c.will_change]


@dataclass
class UndoRecord:
    """Everything needed to put a folder back the way it was."""

    folder: str
    created_at: str
    delta_seconds: float
    entries: list[dict] = field(default_factory=list)


@dataclass
class ApplyResult:
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    undo: UndoRecord | None = None


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def iter_photo_paths(folder: Path) -> list[Path]:
    """Return the supported image files sitting directly in ``folder``."""
    if not folder.is_dir():
        raise NotADirectoryError(f"{folder} is not a folder")
    paths = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in exif.SUPPORTED_EXTENSIONS
    ]
    return sorted(paths, key=lambda p: p.name.lower())


def scan_folder(folder: Path, progress: ProgressCallback | None = None) -> list[PhotoEntry]:
    paths = iter_photo_paths(folder)
    entries: list[PhotoEntry] = []
    for index, path in enumerate(paths, start=1):
        if progress:
            progress(index, len(paths), path.name)
        entries.append(scan_file(path))
    return entries


def scan_file(path: Path) -> PhotoEntry:
    entry = PhotoEntry(path=path, file_times=read_file_times(path))
    try:
        entry.exif_info = exif.read_exif_dates(path)
    except (ExifError, OSError, ValueError, struct.error) as error:
        entry.exif_error = str(error)
    return entry


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def build_plan(
    entries: Iterable[PhotoEntry],
    delta: timedelta,
    update_exif: bool = True,
    update_file_dates: bool = True,
) -> ShiftPlan:
    """Work out the new timestamps without writing anything."""
    plan = ShiftPlan(delta=delta, update_exif=update_exif, update_file_dates=update_file_dates)
    for entry in entries:
        change = PlannedChange(entry=entry)

        if update_exif and entry.exif_info is not None:
            try:
                change.exif_patches = exif.build_shift_patches(entry.exif_info, delta)
            except ExifError as error:
                change.skipped_reason = str(error)
            else:
                current = entry.exif_datetime
                if current is not None:
                    change.new_exif_datetime = current + delta
        elif update_exif and entry.exif_error:
            change.skipped_reason = "EXIF not readable"

        if update_file_dates and delta:
            times = entry.file_times
            change.new_modified = times.modified + delta
            change.new_accessed = times.accessed + delta
            if times.created_is_writable and times.created is not None:
                change.new_created = times.created + delta

        plan.changes.append(change)
    return plan


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


def apply_plan(plan: ShiftPlan, progress: ProgressCallback | None = None) -> ApplyResult:
    """Apply ``plan``, recording an undo entry for every file we touch.

    Files are only ever opened for in-place update. Nothing is created, moved,
    replaced, or deleted, and a failure on one file does not stop the rest.
    """
    result = ApplyResult()
    record = UndoRecord(
        folder=str(plan.changes[0].entry.path.parent) if plan.changes else "",
        created_at=datetime.now().isoformat(timespec="seconds"),
        delta_seconds=plan.delta.total_seconds(),
    )

    actionable = plan.actionable
    for index, change in enumerate(actionable, start=1):
        entry = change.entry
        if progress:
            progress(index, len(actionable), entry.name)

        undo_entry: dict = {"path": str(entry.path)}
        try:
            if change.exif_patches:
                png_chunk = entry.exif_info.png_chunk if entry.exif_info else None
                exif.apply_patches(entry.path, change.exif_patches, png_chunk)
                undo_entry["exif_patches"] = [
                    {
                        "offset": p.offset,
                        # Store both sides so undo can verify before it writes.
                        "expect": p.replacement.hex(),
                        "replacement": p.expect.hex(),
                    }
                    for p in change.exif_patches
                ]
                undo_entry["png_chunk"] = list(png_chunk) if png_chunk else None

            # Always record the original file dates: even an EXIF-only run has to
            # put them back, because writing to the file bumps its modified date.
            undo_entry["file_times"] = {
                "accessed": entry.file_times.accessed.timestamp(),
                "modified": entry.file_times.modified.timestamp(),
                "created": (
                    entry.file_times.created.timestamp()
                    if change.new_created is not None and entry.file_times.created
                    else None
                ),
            }
            if change.new_modified is not None:
                write_file_times(
                    entry.path,
                    accessed=change.new_accessed,
                    modified=change.new_modified,
                    created=change.new_created,
                )
            else:
                # File dates were not part of this run, so restore what the EXIF
                # write disturbed rather than leaving them set to "now".
                write_file_times(
                    entry.path,
                    accessed=entry.file_times.accessed,
                    modified=entry.file_times.modified,
                )
        except Exception as error:  # noqa: BLE001 - one bad file must not stop the batch
            result.failed += 1
            result.errors.append(f"{entry.name}: {error}")
            # Roll this file back so it is never left half-changed.
            if undo_entry.keys() - {"path"}:
                record.entries.append(undo_entry)
                _undo_entry(undo_entry, best_effort=True)
                record.entries.pop()
            continue

        record.entries.append(undo_entry)
        result.succeeded += 1

    result.skipped = len(plan.changes) - len(actionable)
    result.undo = record if record.entries else None
    return result


def undo(record: UndoRecord, progress: ProgressCallback | None = None) -> ApplyResult:
    """Restore the timestamps captured in ``record``."""
    result = ApplyResult()
    for index, undo_entry in enumerate(record.entries, start=1):
        if progress:
            progress(index, len(record.entries), Path(undo_entry["path"]).name)
        try:
            _undo_entry(undo_entry, best_effort=False)
        except Exception as error:  # noqa: BLE001
            result.failed += 1
            result.errors.append(f"{Path(undo_entry['path']).name}: {error}")
        else:
            result.succeeded += 1
    return result


def _undo_entry(undo_entry: dict, best_effort: bool) -> None:
    path = Path(undo_entry["path"])
    try:
        patches = [
            BytePatch(
                offset=p["offset"],
                expect=bytes.fromhex(p["expect"]),
                replacement=bytes.fromhex(p["replacement"]),
            )
            for p in undo_entry.get("exif_patches", [])
        ]
        if patches:
            png_chunk = undo_entry.get("png_chunk")
            exif.apply_patches(path, patches, tuple(png_chunk) if png_chunk else None)

        times = undo_entry.get("file_times")
        if times:
            write_file_times(
                path,
                accessed=datetime.fromtimestamp(times["accessed"]),
                modified=datetime.fromtimestamp(times["modified"]),
                created=(
                    datetime.fromtimestamp(times["created"])
                    if times.get("created") is not None
                    else None
                ),
            )
    except Exception:  # noqa: BLE001
        if not best_effort:
            raise
