"""Background work must hand its result back on the GUI thread.

Qt widgets may only be touched from the thread that owns them. Connecting a
worker signal to a bare lambda gives Qt no receiver object, so it runs the slot
directly on the worker thread -- and the resulting widget access aborts the
process rather than raising. That looks to a user like the window vanishing the
moment the progress bar reaches 100%.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from photo_timestamp_editor import gui  # noqa: E402

TIMEOUT_MS = 15_000


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def run_job(app, window, job):
    """Run ``job`` through the window's worker and return (result, thread, ok)."""
    seen: dict = {}

    def on_done(outcome):
        seen["outcome"] = outcome
        seen["thread"] = QThread.currentThread()
        app.quit()

    def bail():
        seen["timed_out"] = True
        app.quit()

    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(bail)
    guard.start(TIMEOUT_MS)

    window._run(job, on_done, "working")
    app.exec()
    guard.stop()
    return seen


def test_result_is_delivered_on_the_gui_thread(app):
    window = gui.MainWindow()
    gui_thread = QThread.currentThread()

    seen = run_job(app, window, lambda report: "finished")

    assert not seen.get("timed_out"), "the worker never reported back"
    assert seen["outcome"] == "finished"
    assert seen["thread"] is gui_thread, (
        "the callback ran on the worker thread; touching widgets from there "
        "aborts the process instead of raising"
    )


def test_progress_updates_reach_the_widgets(app):
    window = gui.MainWindow()

    def job(report):
        for index in range(1, 4):
            report(index, 3, f"file{index}")
        return "done"

    seen = run_job(app, window, job)

    assert not seen.get("timed_out")
    assert seen["outcome"] == "done"


def test_the_thread_is_cleaned_up_so_another_job_can_start(app):
    window = gui.MainWindow()

    first = run_job(app, window, lambda report: 1)
    assert first["outcome"] == 1
    assert window._thread is None, "a finished job must release its thread"

    second = run_job(app, window, lambda report: 2)
    assert second["outcome"] == 2, "a second job should be able to run"


def test_a_failing_job_does_not_deliver_a_result(app, monkeypatch):
    window = gui.MainWindow()
    shown: list = []

    def critical(*args, **kwargs):
        shown.append(args)
        app.quit()  # the failure path has no result callback to end the loop

    monkeypatch.setattr(gui.QMessageBox, "critical", critical)

    def explode(report):
        raise RuntimeError("no such folder")

    seen = run_job(app, window, explode)

    assert "outcome" not in seen, "a failed job must not be reported as a result"
    assert shown, "the failure should have been surfaced to the user"
    assert window._thread is None
