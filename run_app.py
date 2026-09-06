"""PyInstaller entry point (it needs a script, not a package)."""

from photo_timestamp_editor.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
