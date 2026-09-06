"""Launch the GUI: ``python -m photo_timestamp_editor``.

``--selftest`` builds the window without showing it and exits. The packaged
build runs it in CI to prove the trimmed Qt bundle actually works on Windows.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        from .gui import main as run_gui
        from .gui import selftest
    except ImportError:
        sys.stderr.write(
            "PySide6 is not installed.\n"
            "Install the dependencies first:\n\n"
            "    pip install -r requirements.txt\n"
        )
        return 1

    if "--selftest" in arguments:
        return selftest()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
