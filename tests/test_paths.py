"""Where settings and undo logs are stored, in normal and portable builds."""

from __future__ import annotations

import pytest

from photo_timestamp_editor import paths, undo_store


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    """Pretend the app lives in a writable temp folder, with a clean AppData."""
    application = tmp_path / "PhotoTimestampEditor"
    application.mkdir()
    monkeypatch.setattr(paths, "app_directory", lambda: application)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    return application


def test_without_the_marker_data_goes_to_the_user_profile(app_dir, tmp_path):
    assert paths.is_portable() is False
    assert paths.data_directory() == tmp_path / "appdata" / "PhotoTimestampEditor"


def test_the_marker_moves_data_beside_the_executable(app_dir):
    (app_dir / "portable.txt").write_text("portable")

    assert paths.is_portable() is True
    assert paths.data_directory() == app_dir / "data"
    assert paths.settings_file() == app_dir / "data" / "settings.ini"


def test_portable_undo_logs_stay_inside_the_app_folder(app_dir):
    (app_dir / "portable.txt").write_text("portable")
    directory = undo_store.undo_directory()

    assert directory == app_dir / "data" / "undo"
    assert directory.is_dir()


def test_a_read_only_app_folder_falls_back_to_the_user_profile(app_dir, tmp_path, monkeypatch):
    (app_dir / "portable.txt").write_text("portable")
    # Simulate running from Program Files, a network share, or inside the ZIP.
    monkeypatch.setattr(paths, "_is_writable", lambda directory: False)

    assert paths.is_portable() is False
    assert paths.data_directory() == tmp_path / "appdata" / "PhotoTimestampEditor"


def test_data_directory_is_created_on_demand(app_dir):
    (app_dir / "portable.txt").write_text("portable")
    assert not (app_dir / "data").exists()
    assert paths.data_directory().is_dir()
