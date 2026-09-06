"""Turn the PyInstaller output into the portable folder and ZIP.

Run after PyInstaller. Both build_windows.bat and the GitHub Actions workflow
call this, so the ZIP is named in exactly one place -- the published download
URL depends on that name, so it must not drift.

    python tools/package_portable.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
APP_NAME = "PhotoTimestampEditor"

PORTABLE_MARKER_TEXT = """This file makes the app portable: settings and undo logs are kept in the
"data" folder next to the program instead of in your Windows user profile.

Delete this file if you would rather it used AppData.
"""


def version() -> str:
    sys.path.insert(0, str(PROJECT))
    from photo_timestamp_editor import __version__

    return __version__


def build_zip(app_dir: Path, zip_path: Path) -> None:
    """Zip ``app_dir`` with the folder itself as the single top-level entry.

    Extracting then gives one folder rather than spraying files into whatever
    directory the user happened to be in.
    """
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(app_dir.rglob("*")):
            if item.is_file():
                archive.write(item, Path(APP_NAME) / item.relative_to(app_dir))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist",
        type=Path,
        default=PROJECT / "dist",
        help="the PyInstaller output directory (default: dist)",
    )
    parser.add_argument(
        "--platform-tag",
        default="windows-x64",
        help="appended to the ZIP name (default: windows-x64)",
    )
    args = parser.parse_args()

    app_dir = args.dist / APP_NAME
    if not app_dir.is_dir():
        sys.stderr.write(f"error: {app_dir} does not exist -- run PyInstaller first\n")
        return 1

    # The marker that puts settings and undo logs beside the executable.
    (app_dir / "portable.txt").write_text(PORTABLE_MARKER_TEXT, encoding="utf-8")
    shutil.copyfile(
        PROJECT / "packaging" / "READ-ME-FIRST.txt", app_dir / "READ-ME-FIRST.txt"
    )

    release = version()
    zip_path = args.dist / f"{APP_NAME}-{release}-{args.platform_tag}.zip"
    build_zip(app_dir, zip_path)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"version:  {release}")
    print(f"folder:   {app_dir}")
    print(f"zip:      {zip_path}  ({size_mb:.1f} MB)")

    # Hand the values to GitHub Actions when running there.
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"version={release}\n")
            handle.write(f"zip_path={zip_path}\n")
            handle.write(f"zip_name={zip_path.name}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
