"""PySide6 window: pick a folder, preview the shift, apply it, undo it."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import core, log, paths, undo_store
from . import __version__
from .core import ApplyResult, PhotoEntry, ShiftPlan

APP_NAME = "Photo Timestamp Editor"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

COLUMNS = [
    "File",
    "Type",
    "Photo taken (EXIF)",
    "New photo taken",
    "File modified",
    "New file modified",
    "Status",
]


def resource_dir() -> Path:
    """Where bundled resources live, both from source and inside a frozen build."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "photo_timestamp_editor" / "resources"
    return Path(__file__).resolve().parent / "resources"


def app_icon() -> QIcon:
    icon_path = resource_dir() / "app.ico"
    return QIcon(str(icon_path)) if icon_path.exists() else QIcon()


def format_dt(value: datetime | None) -> str:
    return value.strftime(DATE_FORMAT) if value else "—"


def format_delta(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    sign = "-" if total < 0 else "+"
    total = abs(total)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    parts.append(f"{hours:02d}h")
    parts.append(f"{minutes:02d}m")
    if seconds:
        parts.append(f"{seconds:02d}s")
    return sign + " ".join(parts)


class Worker(QObject):
    """Runs a blocking callable on a QThread and reports progress."""

    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, job) -> None:
        super().__init__()
        self._job = job

    def run(self) -> None:
        try:
            outcome = self._job(lambda done, total, name: self.progress.emit(done, total, name))
        except Exception as error:  # noqa: BLE001 - surfaced in the UI
            self.failed.emit(str(error))
        else:
            self.finished.emit(outcome)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 680)

        # A portable copy keeps its settings in a file beside the executable
        # rather than in the registry, so it leaves nothing on the machine.
        if paths.is_portable():
            self.settings = QSettings(str(paths.settings_file()), QSettings.IniFormat)
        else:
            self.settings = QSettings("PhotoTimestampEditor", "PhotoTimestampEditor")
        self.folder: Path | None = None
        self.entries: list[PhotoEntry] = []
        self.plan: ShiftPlan | None = None
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._on_done = None

        self._build_ui()
        self._refresh_undo_button()
        self._update_preview()

    # ---------------------------------------------------------------- layout

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        layout.addWidget(self._build_folder_box())
        layout.addWidget(self._build_shift_box())
        layout.addWidget(self._build_table(), stretch=1)
        layout.addLayout(self._build_actions())

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Choose a folder to begin.")

    def _build_folder_box(self) -> QWidget:
        box = QGroupBox("1. Choose a folder")
        row = QHBoxLayout(box)

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        font = self.folder_label.font()
        font.setStyleHint(QFont.Monospace)
        self.folder_label.setFont(font)

        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_folder)
        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.clicked.connect(self.rescan)
        self.rescan_button.setEnabled(False)

        row.addWidget(self.folder_label, stretch=1)
        row.addWidget(browse)
        row.addWidget(self.rescan_button)
        return box

    def _build_shift_box(self) -> QWidget:
        box = QGroupBox("2. Shift the timestamps")
        outer = QVBoxLayout(box)
        row = QHBoxLayout()

        self.hours = QSpinBox()
        self.hours.setRange(-240, 240)
        self.hours.setSuffix(" h")
        self.hours.setToolTip(
            "Whole hours to add or subtract. Use this to correct a camera that was\n"
            "set to the wrong time zone, e.g. -5 h if it ran five hours ahead."
        )
        hours_font = self.hours.font()
        hours_font.setPointSize(hours_font.pointSize() + 2)
        self.hours.setFont(hours_font)

        self.minutes = QSpinBox()
        self.minutes.setRange(-1440, 1440)
        self.minutes.setSuffix(" min")

        self.seconds = QSpinBox()
        self.seconds.setRange(-86400, 86400)
        self.seconds.setSuffix(" s")

        self.days = QSpinBox()
        self.days.setRange(-3650, 3650)
        self.days.setSuffix(" d")

        for widget in (self.days, self.hours, self.minutes, self.seconds):
            widget.valueChanged.connect(self._update_preview)

        row.addWidget(QLabel("Hours:"))
        row.addWidget(self.hours)
        row.addSpacing(12)
        row.addWidget(QLabel("Minutes:"))
        row.addWidget(self.minutes)
        row.addSpacing(12)
        row.addWidget(QLabel("Seconds:"))
        row.addWidget(self.seconds)
        row.addSpacing(12)
        row.addWidget(QLabel("Days:"))
        row.addWidget(self.days)
        row.addStretch(1)

        for label, hours in (("-1 h", -1), ("+1 h", 1), ("Reset", 0)):
            button = QPushButton(label)
            button.setFixedWidth(64)
            button.clicked.connect(lambda _=False, h=hours: self._nudge(h))
            row.addWidget(button)

        outer.addLayout(row)

        options = QHBoxLayout()
        self.update_exif = QCheckBox("Update EXIF dates inside the photo")
        self.update_exif.setChecked(True)
        self.update_exif.setToolTip(
            "Rewrites only the date characters in place. The image itself is never\n"
            "re-encoded and the file size does not change."
        )
        self.update_files = QCheckBox("Update Windows file dates")
        self.update_files.setChecked(True)
        self.update_exif.toggled.connect(self._update_preview)
        self.update_files.toggled.connect(self._update_preview)
        options.addWidget(self.update_exif)
        options.addSpacing(16)
        options.addWidget(self.update_files)
        options.addStretch(1)
        outer.addLayout(options)

        self.summary_label = QLabel()
        self.summary_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        outer.addWidget(self.summary_label)
        return box

    def _build_table(self) -> QWidget:
        box = QGroupBox("3. Check the preview")
        layout = QVBoxLayout(box)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        return box

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.undo_button = QPushButton("Undo last change")
        self.undo_button.clicked.connect(self.undo_last)
        self.apply_button = QPushButton("Apply to all photos")
        self.apply_button.setDefault(True)
        self.apply_button.setMinimumWidth(190)
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setEnabled(False)
        row.addWidget(self.undo_button)
        row.addStretch(1)
        row.addWidget(self.apply_button)
        return row

    # ----------------------------------------------------------------- state

    def current_delta(self) -> timedelta:
        return timedelta(
            days=self.days.value(),
            hours=self.hours.value(),
            minutes=self.minutes.value(),
            seconds=self.seconds.value(),
        )

    def _nudge(self, hours: int) -> None:
        if hours == 0:
            for widget in (self.days, self.hours, self.minutes, self.seconds):
                widget.setValue(0)
        else:
            self.hours.setValue(self.hours.value() + hours)

    # --------------------------------------------------------------- actions

    def choose_folder(self) -> None:
        start = self.settings.value("last_folder", "", type=str)
        chosen = QFileDialog.getExistingDirectory(self, "Select a folder of photos", start)
        if not chosen:
            return
        self.folder = Path(chosen)
        self.settings.setValue("last_folder", chosen)
        self.folder_label.setText(chosen)
        self.rescan_button.setEnabled(True)
        self.rescan()

    def rescan(self) -> None:
        if not self.folder:
            return
        folder = self.folder
        self._run(
            lambda report: core.scan_folder(folder, report),
            on_done=self._scan_finished,
            busy_message=f"Reading {folder}…",
        )

    def _scan_finished(self, entries: list[PhotoEntry]) -> None:
        self.entries = entries
        if not entries:
            self.statusBar().showMessage("No supported photos found in that folder.")
        else:
            with_dates = sum(1 for e in entries if e.exif_datetime)
            self.statusBar().showMessage(
                f"Found {len(entries)} photo(s); {with_dates} have a readable EXIF date."
            )
        self._update_preview()

    def apply(self) -> None:
        if not self.plan or not self.plan.actionable:
            return
        count = len(self.plan.actionable)
        delta = format_delta(self.plan.delta)
        targets = []
        if self.plan.update_exif:
            targets.append("EXIF dates")
        if self.plan.update_file_dates:
            targets.append("file dates")

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Apply timestamp shift")
        confirm.setIcon(QMessageBox.Question)
        confirm.setText(f"Shift {' and '.join(targets)} by {delta} on {count} photo(s)?")
        confirm.setInformativeText(
            "Photos are edited in place -- no file is copied, replaced or deleted. "
            "You can undo this afterwards with the Undo button."
        )
        confirm.setStandardButtons(QMessageBox.Cancel | QMessageBox.Ok)
        confirm.setDefaultButton(QMessageBox.Ok)
        if confirm.exec() != QMessageBox.Ok:
            return

        plan = self.plan
        self._run(
            lambda report: core.apply_plan(plan, report),
            on_done=self._apply_finished,
            busy_message="Applying…",
        )

    def _apply_finished(self, result: ApplyResult) -> None:
        if result.undo:
            undo_store.save(result.undo)
        self._refresh_undo_button()
        self._report(result, "Updated")
        self.rescan()

    def undo_last(self) -> None:
        newest = undo_store.latest()
        if not newest:
            QMessageBox.information(self, APP_NAME, "There is nothing to undo.")
            return
        path, record = newest
        answer = QMessageBox.question(
            self,
            "Undo last change",
            f"Restore the original timestamps of {len(record.entries)} photo(s)?",
            QMessageBox.Cancel | QMessageBox.Ok,
            QMessageBox.Ok,
        )
        if answer != QMessageBox.Ok:
            return

        def job(report):
            outcome = core.undo(record, report)
            if not outcome.failed:
                path.unlink(missing_ok=True)
            return outcome

        self._run(job, on_done=self._undo_finished, busy_message="Undoing…")

    def _undo_finished(self, result: ApplyResult) -> None:
        self._refresh_undo_button()
        self._report(result, "Restored")
        self.rescan()

    def _report(self, result: ApplyResult, verb: str) -> None:
        message = f"{verb} {result.succeeded} photo(s)."
        if result.failed:
            message += f" {result.failed} failed."
        self.statusBar().showMessage(message)
        if result.errors:
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Warning)
            box.setText(message)
            box.setInformativeText("Every other photo was updated. Details below.")
            box.setDetailedText("\n".join(result.errors))
            box.exec()

    def _refresh_undo_button(self) -> None:
        newest = undo_store.latest()
        self.undo_button.setEnabled(newest is not None)
        if newest:
            _, record = newest
            self.undo_button.setToolTip(
                f"Undo the shift of {format_delta(timedelta(seconds=record.delta_seconds))} "
                f"applied to {len(record.entries)} photo(s) at {record.created_at}."
            )
        else:
            self.undo_button.setToolTip("No change has been applied yet.")

    # --------------------------------------------------------------- preview

    def _update_preview(self) -> None:
        delta = self.current_delta()
        self.plan = core.build_plan(
            self.entries,
            delta,
            update_exif=self.update_exif.isChecked(),
            update_file_dates=self.update_files.isChecked(),
        )
        changed = len(self.plan.actionable)
        self.apply_button.setEnabled(bool(changed) and bool(delta))

        if not self.entries:
            self.summary_label.setText("")
        elif not delta:
            self.summary_label.setText("Set a shift to see the new timestamps.")
        else:
            self.summary_label.setText(
                f"<b>{format_delta(delta)}</b> → {changed} of {len(self.entries)} "
                f"photo(s) will change"
            )

        self.table.setRowCount(len(self.plan.changes))
        for row, change in enumerate(self.plan.changes):
            entry = change.entry
            container = entry.exif_info.container if entry.exif_info else entry.path.suffix.upper()

            status = self._describe(change, delta)

            values = [
                entry.name,
                container,
                format_dt(entry.exif_datetime),
                format_dt(change.new_exif_datetime),
                format_dt(entry.file_times.modified),
                format_dt(change.new_modified),
                status,
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column in (3, 5) and text != "—":
                    item.setForeground(QColor("#1a7f37"))
                if column == 6 and not change.will_change and delta:
                    item.setForeground(QColor("#9a6700"))
                self.table.setItem(row, column, item)

    def _describe(self, change, delta: timedelta) -> str:
        """Say what will actually happen to this file, and why anything is left out."""
        if not delta:
            return "no shift set"

        done = []
        if change.exif_patches:
            done.append(f"EXIF ×{len(change.exif_patches)}")
        if change.new_modified is not None:
            done.append("file dates")

        caveat = ""
        if self.update_exif.isChecked() and not change.exif_patches:
            caveat = change.skipped_reason or change.entry.exif_error or "no EXIF date"

        if done:
            return f"{' + '.join(done)} — {caveat}" if caveat else " + ".join(done)
        return caveat or "nothing to change"

    # -------------------------------------------------------------- threading

    def _run(self, job, on_done, busy_message: str) -> None:
        if self._thread is not None:
            return  # a job is already running
        self._set_busy(True, busy_message)

        self._on_done = on_done
        self._thread = QThread(self)
        self._worker = Worker(job)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        # These must be bound methods of this window, connected explicitly as
        # queued, so they run on the GUI thread. A bare lambda has no receiver
        # object, which makes Qt run it directly on the worker thread instead --
        # and touching a widget from there aborts the process outright rather
        # than raising something we could report.
        self._worker.progress.connect(self._on_progress, Qt.QueuedConnection)
        self._worker.finished.connect(self._on_finished, Qt.QueuedConnection)
        self._worker.failed.connect(self._on_failed, Qt.QueuedConnection)
        self._thread.start()

    def _on_finished(self, outcome) -> None:
        on_done = self._on_done
        self._teardown_thread()
        self._set_busy(False, "")
        if on_done is not None:
            on_done(outcome)

    def _on_failed(self, message: str) -> None:
        self._teardown_thread()
        self._set_busy(False, "")
        log.write(f"background job failed: {message}")
        QMessageBox.critical(self, APP_NAME, message)
        self.statusBar().showMessage("Failed: " + message)

    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None
        self._on_done = None

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.statusBar().showMessage(f"{done}/{total}  {name}")

    def _set_busy(self, busy: bool, message: str) -> None:
        self.progress.setVisible(busy)
        self.progress.setValue(0)
        if busy:
            for widget in (self.apply_button, self.undo_button, self.rescan_button):
                widget.setEnabled(False)
            self.statusBar().showMessage(message)
        else:
            self.rescan_button.setEnabled(self.folder is not None)
            self._refresh_undo_button()
            self._sync_apply_button()

    def _sync_apply_button(self) -> None:
        self.apply_button.setEnabled(
            bool(self.plan and self.plan.actionable) and bool(self.current_delta())
        )


def main() -> int:
    log.install_excepthook()
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    log.write(f"starting {APP_NAME} {__version__}")
    window = MainWindow()
    window.show()
    return app.exec()


def _report(message: str) -> None:
    """Print a diagnostic without assuming there is anywhere to print to.

    A windowed PyInstaller build has no console, and sets ``sys.stderr`` and
    ``sys.stdout`` to ``None``. Writing to them there raises, so the exit code
    is the real channel and this is best-effort.
    """
    stream = sys.stderr or sys.stdout
    if stream is None:
        return
    try:
        stream.write(message + "\n")
        stream.flush()
    except Exception:  # noqa: BLE001 - diagnostics must never be the failure
        pass


# Exit codes, so a windowed build can report a cause with no console.
SELFTEST_OK = 0
SELFTEST_NO_ICON = 2
SELFTEST_BAD_TABLE = 3
SELFTEST_CRASHED = 4


def selftest() -> int:
    """Build the window without showing it, then exit.

    Creating the QApplication is the part that loads Qt's platform plugin, so
    this catches a packaged build whose Qt libraries were trimmed too hard.
    Nothing is shown and no event loop runs.

    Every failure has to come back as an exit code. An exception escaping a
    windowed build is shown in a modal dialog, which would hang whatever is
    waiting on the process rather than failing it.
    """
    try:
        app = QApplication.instance() or QApplication([])
        app.setApplicationName(APP_NAME)

        icon = app_icon()
        if icon.isNull():
            _report("selftest: the application icon is missing from the bundle")
            return SELFTEST_NO_ICON

        window = MainWindow()
        if window.table.columnCount() != len(COLUMNS):
            _report("selftest: the preview table did not build correctly")
            return SELFTEST_BAD_TABLE

        _report(f"selftest: ok ({APP_NAME}, {len(icon.availableSizes())} icon sizes)")
        return SELFTEST_OK
    except BaseException as error:  # noqa: BLE001 - must not reach the bootloader
        _report(f"selftest: failed: {error!r}")
        return SELFTEST_CRASHED
