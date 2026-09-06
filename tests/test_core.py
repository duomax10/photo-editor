"""Scanning, planning, applying and undoing a folder-wide shift."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from photo_timestamp_editor import core, exif, undo_store

from tests.conftest import build_heif, build_jpeg, build_png, build_tiff_block

SHIFT = timedelta(hours=-7)


@pytest.fixture
def folder(tmp_path):
    """A folder holding one of each supported type, plus files to leave alone."""
    tiff = build_tiff_block()
    (tmp_path / "a.jpg").write_bytes(build_jpeg(tiff))
    (tmp_path / "b.heic").write_bytes(build_heif(tiff))
    (tmp_path / "c.png").write_bytes(build_png(tiff))
    (tmp_path / "d.nef").write_bytes(tiff)
    (tmp_path / "broken.jpg").write_bytes(b"\xff\xd8\xff\xd9")  # no EXIF
    (tmp_path / "notes.txt").write_text("not a photo")
    (tmp_path / "subfolder").mkdir()
    (tmp_path / "subfolder" / "deep.jpg").write_bytes(build_jpeg(tiff))

    # Give every file a known modification time.
    stamp = datetime(2023, 7, 14, 9, 30, 0).timestamp()
    for path in tmp_path.iterdir():
        if path.is_file():
            os.utime(path, (stamp, stamp))
    return tmp_path


def test_scan_only_picks_up_supported_files_in_the_folder_itself(folder):
    entries = core.scan_folder(folder)
    assert [e.name for e in entries] == ["a.jpg", "b.heic", "broken.jpg", "c.png", "d.nef"]
    assert "notes.txt" not in [e.name for e in entries]
    assert "deep.jpg" not in [e.name for e in entries]


def test_scan_records_but_survives_unreadable_exif(folder):
    entries = {e.name: e for e in core.scan_folder(folder)}
    assert entries["a.jpg"].exif_datetime == datetime(2023, 7, 14, 9, 30)
    assert entries["broken.jpg"].exif_datetime is None
    assert entries["broken.jpg"].exif_error
    assert entries["broken.jpg"].exif_status == "unreadable"


def test_plan_previews_without_touching_anything(folder):
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    entries = core.scan_folder(folder)
    plan = core.build_plan(entries, SHIFT)

    changes = {c.entry.name: c for c in plan.changes}
    assert changes["a.jpg"].new_exif_datetime == datetime(2023, 7, 14, 2, 30)
    assert changes["a.jpg"].new_modified == datetime(2023, 7, 14, 2, 30)
    assert changes["broken.jpg"].new_exif_datetime is None
    # The unreadable file still gets its file dates shifted.
    assert changes["broken.jpg"].new_modified == datetime(2023, 7, 14, 2, 30)

    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before


def test_apply_shifts_exif_and_file_dates(folder):
    plan = core.build_plan(core.scan_folder(folder), SHIFT)
    result = core.apply_plan(plan)

    assert result.failed == 0
    assert result.succeeded == 5
    assert result.errors == []

    for name in ("a.jpg", "b.heic", "c.png", "d.nef"):
        info = exif.read_exif_dates(folder / name)
        assert info.primary.value == datetime(2023, 7, 14, 2, 30), name

    for name in ("a.jpg", "broken.jpg"):
        modified = datetime.fromtimestamp((folder / name).stat().st_mtime)
        assert modified == datetime(2023, 7, 14, 2, 30), name


def test_apply_never_adds_or_removes_files(folder):
    names_before = sorted(p.name for p in folder.iterdir())
    sizes_before = {p.name: p.stat().st_size for p in folder.iterdir() if p.is_file()}

    core.apply_plan(core.build_plan(core.scan_folder(folder), SHIFT))

    assert sorted(p.name for p in folder.iterdir()) == names_before
    assert {p.name: p.stat().st_size for p in folder.iterdir() if p.is_file()} == sizes_before


def test_undo_restores_bytes_and_file_dates_exactly(folder):
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    mtimes_before = {p.name: p.stat().st_mtime for p in folder.iterdir() if p.is_file()}

    result = core.apply_plan(core.build_plan(core.scan_folder(folder), SHIFT))
    assert result.undo is not None
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} != before

    undone = core.undo(result.undo)
    assert undone.failed == 0
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
    assert {
        p.name: p.stat().st_mtime for p in folder.iterdir() if p.is_file()
    } == pytest.approx(mtimes_before)


def test_exif_only_leaves_file_dates_alone(folder):
    mtimes = {p.name: p.stat().st_mtime for p in folder.iterdir() if p.is_file()}
    plan = core.build_plan(core.scan_folder(folder), SHIFT, update_file_dates=False)
    core.apply_plan(plan)

    assert {
        p.name: p.stat().st_mtime for p in folder.iterdir() if p.is_file()
    } == pytest.approx(mtimes)
    assert exif.read_exif_dates(folder / "a.jpg").primary.value == datetime(2023, 7, 14, 2, 30)


def test_file_dates_only_leaves_bytes_untouched(folder):
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    plan = core.build_plan(core.scan_folder(folder), SHIFT, update_exif=False)
    core.apply_plan(plan)

    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
    assert datetime.fromtimestamp((folder / "a.jpg").stat().st_mtime) == datetime(
        2023, 7, 14, 2, 30
    )


def test_a_zero_shift_changes_nothing(folder):
    plan = core.build_plan(core.scan_folder(folder), timedelta(0))
    assert plan.actionable == []
    result = core.apply_plan(plan)
    assert result.succeeded == 0
    assert result.undo is None


def test_one_bad_file_does_not_stop_the_batch(folder, monkeypatch):
    entries = core.scan_folder(folder)
    plan = core.build_plan(entries, SHIFT)

    real_apply = exif.apply_patches

    def explode(path, patches, png_chunk):
        if path.name == "c.png":
            raise OSError("disk gremlins")
        return real_apply(path, patches, png_chunk)

    monkeypatch.setattr(exif, "apply_patches", explode)
    result = core.apply_plan(plan)

    assert result.failed == 1
    assert "c.png" in result.errors[0]
    assert result.succeeded == 4
    assert exif.read_exif_dates(folder / "a.jpg").primary.value == datetime(2023, 7, 14, 2, 30)
    # The failed file is left exactly as it was, not half-shifted.
    assert exif.read_exif_dates(folder / "c.png").primary.value == datetime(2023, 7, 14, 9, 30)


def test_scanning_a_missing_folder_is_an_error(tmp_path):
    with pytest.raises(NotADirectoryError):
        core.scan_folder(tmp_path / "nope")


def test_undo_records_round_trip_through_disk(folder, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))

    result = core.apply_plan(core.build_plan(core.scan_folder(folder), SHIFT))
    saved = undo_store.save(result.undo)
    assert saved.exists()
    assert saved.parent not in folder.parents and saved.parent != folder

    newest = undo_store.latest()
    assert newest is not None
    _, reloaded = newest
    assert reloaded.delta_seconds == SHIFT.total_seconds()
    assert len(reloaded.entries) == len(result.undo.entries)

    assert core.undo(reloaded).failed == 0
    assert exif.read_exif_dates(folder / "a.jpg").primary.value == datetime(2023, 7, 14, 9, 30)


def test_survey_reports_files_it_ignored(folder):
    (folder / "clip.mov").write_bytes(b"movie")
    (folder / "readme").write_text("no extension")

    survey = core.survey_folder(folder)

    assert survey.ignored_count == 3  # notes.txt, clip.mov, readme
    assert ".mov" in survey.ignored_extensions
    assert ".txt" in survey.ignored_extensions
    assert "(no extension)" in survey.ignored_extensions


def test_survey_counts_photos_hidden_in_subfolders(folder):
    # A common layout: raw files kept in their own subfolder next to the JPEGs.
    raw_dir = folder / "RAW"
    raw_dir.mkdir()
    (raw_dir / "DSC_1.nef").write_bytes(build_tiff_block())
    (raw_dir / "DSC_2.orf").write_bytes(build_tiff_block())

    survey = core.survey_folder(folder)

    # subfolder/deep.jpg from the fixture, plus the two raw files.
    assert survey.photos_in_subfolders == 3
    assert [e.name for e in core.scan_folder(folder)] == [
        "a.jpg",
        "b.heic",
        "broken.jpg",
        "c.png",
        "d.nef",
    ], "subfolder photos must still not be scanned"


def test_raw_formats_with_their_own_magic_numbers_are_shifted(tmp_path):
    from tests.conftest import build_cr3, build_raf, build_tiff_variant

    (tmp_path / "olympus.orf").write_bytes(build_tiff_variant(0x4F52))
    (tmp_path / "panasonic.rw2").write_bytes(build_tiff_variant(85))
    (tmp_path / "fuji.raf").write_bytes(build_raf(build_tiff_block()))
    (tmp_path / "canon.cr3").write_bytes(build_cr3(build_tiff_block()))

    entries = core.scan_folder(tmp_path)
    assert len(entries) == 4
    assert all(e.exif_datetime == datetime(2023, 7, 14, 9, 30) for e in entries), {
        e.name: e.exif_error for e in entries
    }

    result = core.apply_plan(core.build_plan(entries, timedelta(hours=-7)))
    assert result.failed == 0
    for name in ("olympus.orf", "panasonic.rw2", "fuji.raf", "canon.cr3"):
        assert exif.read_exif_dates(tmp_path / name).primary.value == datetime(
            2023, 7, 14, 2, 30
        ), name
