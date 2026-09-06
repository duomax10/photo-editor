"""Read and write file system timestamps, including the Windows creation date.

``os.utime`` covers accessed/modified everywhere. Creation time is writable only
on Windows, where it needs a ``SetFileTime`` call through ctypes.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# FILETIME counts 100-nanosecond intervals since 1601-01-01 UTC.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_HUNDREDS_OF_NS = 10_000_000


@dataclass
class FileTimes:
    """A file's timestamps, as local naive datetimes for display."""

    accessed: datetime
    modified: datetime
    created: datetime | None

    @property
    def created_is_writable(self) -> bool:
        return IS_WINDOWS and self.created is not None


def read_file_times(path: Path) -> FileTimes:
    stat = path.stat()
    created: datetime | None = None
    if IS_WINDOWS:
        created = datetime.fromtimestamp(stat.st_ctime)
    elif hasattr(stat, "st_birthtime"):
        created = datetime.fromtimestamp(stat.st_birthtime)
    return FileTimes(
        accessed=datetime.fromtimestamp(stat.st_atime),
        modified=datetime.fromtimestamp(stat.st_mtime),
        created=created,
    )


def write_file_times(
    path: Path,
    accessed: datetime | None = None,
    modified: datetime | None = None,
    created: datetime | None = None,
) -> None:
    """Set whichever timestamps are supplied, leaving the others untouched."""
    if accessed is not None or modified is not None:
        current = path.stat()
        os.utime(
            path,
            (
                accessed.timestamp() if accessed is not None else current.st_atime,
                modified.timestamp() if modified is not None else current.st_mtime,
            ),
        )
    if created is not None and IS_WINDOWS:
        _set_windows_creation_time(path, created)


def _set_windows_creation_time(path: Path, created: datetime) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.SetFileTime.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]

    aware = created.astimezone(timezone.utc) if created.tzinfo else created.astimezone()
    ticks = int((aware.astimezone(timezone.utc) - _FILETIME_EPOCH).total_seconds() * _HUNDREDS_OF_NS)
    filetime = FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)

    FILE_WRITE_ATTRIBUTES = 0x100
    FILE_SHARE_READ_WRITE_DELETE = 0x07
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    handle = kernel32.CreateFileW(
        str(path),
        FILE_WRITE_ATTRIBUTES,
        FILE_SHARE_READ_WRITE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), f"cannot open {path} to set its creation date")
    try:
        if not kernel32.SetFileTime(handle, ctypes.byref(filetime), None, None):
            raise OSError(ctypes.get_last_error(), f"cannot set the creation date of {path}")
    finally:
        kernel32.CloseHandle(handle)
