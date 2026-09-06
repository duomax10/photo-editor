"""Launch the GUI: ``python -m photo_timestamp_editor``."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from .gui import main as run_gui
    except ImportError:
        sys.stderr.write(
            "PySide6 is not installed.\n"
            "Install the dependencies first:\n\n"
            "    pip install -r requirements.txt\n"
        )
        return 1
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
