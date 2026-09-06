"""The packaged self-test, including the no-console conditions of a frozen build."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

# Must be set before the first QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from photo_timestamp_editor import gui  # noqa: E402


def test_selftest_passes():
    assert gui.selftest() == gui.SELFTEST_OK


def test_selftest_survives_a_windowed_build_with_no_streams(monkeypatch):
    """A windowed PyInstaller build sets sys.stdout and sys.stderr to None.

    Writing to them there raises, and an exception escaping a windowed build is
    shown in a modal dialog that hangs whatever is waiting on the process, so
    the self-test has to succeed with nowhere to print.
    """
    monkeypatch.setattr(gui.sys, "stderr", None)
    monkeypatch.setattr(gui.sys, "stdout", None)

    assert gui.selftest() == gui.SELFTEST_OK


def test_report_never_raises_on_a_broken_stream(monkeypatch):
    class Broken:
        def write(self, _):
            raise OSError("no console attached")

        def flush(self):
            pass

    monkeypatch.setattr(gui.sys, "stderr", Broken())
    gui._report("this must not raise")


def test_a_crash_becomes_an_exit_code_not_an_exception(monkeypatch):
    def explode():
        raise RuntimeError("Qt platform plugin missing")

    monkeypatch.setattr(gui, "app_icon", explode)
    assert gui.selftest() == gui.SELFTEST_CRASHED


def test_a_missing_icon_is_reported(monkeypatch):
    from PySide6.QtGui import QIcon

    monkeypatch.setattr(gui, "app_icon", QIcon)
    assert gui.selftest() == gui.SELFTEST_NO_ICON
