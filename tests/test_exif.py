"""EXIF parsing and in-place patching, across every supported container."""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timedelta

import pytest

from photo_timestamp_editor import exif
from photo_timestamp_editor.exif import ExifError

from tests.conftest import build_jpeg, build_tiff_block

CONTAINERS = [
    "jpeg_file",
    "png_file",
    "heif_file",
    "tiff_file",
    "raw_file",
    "orf_file",
    "rw2_file",
    "raf_file",
    "cr3_file",
]
EXPECTED_CONTAINER = {
    "jpeg_file": "JPEG",
    "png_file": "PNG",
    "heif_file": "HEIF",
    "tiff_file": "TIFF",
    "raw_file": "RAW",
    "orf_file": "RAW",
    "rw2_file": "RAW",
    "raf_file": "RAF",
    "cr3_file": "CR3",
}


@pytest.mark.parametrize("fixture_name", CONTAINERS)
def test_reads_all_three_dates(request, fixture_name):
    path = request.getfixturevalue(fixture_name)
    info = exif.read_exif_dates(path)

    assert info.container == EXPECTED_CONTAINER[fixture_name]
    # CR3 carries two independent EXIF blocks, so names can repeat.
    assert {f.name for f in info.fields} == {
        "DateTime",
        "DateTimeOriginal",
        "DateTimeDigitized",
    }
    assert all(f.value == datetime(2023, 7, 14, 9, 30, 0) for f in info.fields)
    assert info.primary.name == "DateTimeOriginal"


@pytest.mark.parametrize("fixture_name", CONTAINERS)
def test_shift_rewrites_in_place_without_changing_size(request, fixture_name):
    path = request.getfixturevalue(fixture_name)
    before = path.read_bytes()

    info = exif.read_exif_dates(path)
    patches = exif.build_shift_patches(info, timedelta(hours=5))
    assert len(patches) == len([f for f in info.fields if f.value])
    exif.apply_patches(path, patches, info.png_chunk)

    after = path.read_bytes()
    assert len(after) == len(before)

    # Only the date bytes (and, for PNG, the chunk CRC) may differ.
    changed = {i for i in range(len(before)) if before[i] != after[i]}
    allowed = set()
    for f in info.fields:
        allowed |= set(range(f.file_offset, f.file_offset + f.byte_count))
    if info.png_chunk:
        chunk_start, data_length = info.png_chunk
        allowed |= set(range(chunk_start + 8 + data_length, chunk_start + 12 + data_length))
    assert changed <= allowed

    reread = exif.read_exif_dates(path)
    assert all(f.value == datetime(2023, 7, 14, 14, 30, 0) for f in reread.fields)


def test_negative_shift_rolls_back_across_midnight(jpeg_file):
    info = exif.read_exif_dates(jpeg_file)
    exif.apply_patches(
        jpeg_file, exif.build_shift_patches(info, timedelta(hours=-10)), info.png_chunk
    )
    assert exif.read_exif_dates(jpeg_file).primary.value == datetime(2023, 7, 13, 23, 30, 0)


def test_patches_are_exactly_reversible(jpeg_file):
    original = jpeg_file.read_bytes()
    info = exif.read_exif_dates(jpeg_file)

    patches = exif.build_shift_patches(info, timedelta(days=2, hours=-3, minutes=15))
    exif.apply_patches(jpeg_file, patches, info.png_chunk)
    assert jpeg_file.read_bytes() != original

    exif.apply_patches(jpeg_file, [p.inverted() for p in patches], info.png_chunk)
    assert jpeg_file.read_bytes() == original


def test_png_crc_stays_valid_after_patching(png_file):
    info = exif.read_exif_dates(png_file)
    exif.apply_patches(
        png_file, exif.build_shift_patches(info, timedelta(hours=3)), info.png_chunk
    )

    data = png_file.read_bytes()
    chunk_start, data_length = info.png_chunk
    body = data[chunk_start + 4 : chunk_start + 8 + data_length]
    (stored_crc,) = struct.unpack_from(">I", data, chunk_start + 8 + data_length)
    assert stored_crc == zlib.crc32(body) & 0xFFFFFFFF


def test_apply_refuses_when_the_file_moved_under_us(jpeg_file):
    info = exif.read_exif_dates(jpeg_file)
    patches = exif.build_shift_patches(info, timedelta(hours=1))

    # Someone else edits the file between the scan and the apply.
    jpeg_file.write_bytes(build_jpeg(build_tiff_block(date_time="2001:01:01 00:00:00")))
    guarded = jpeg_file.read_bytes()

    with pytest.raises(ExifError, match="changed on disk"):
        exif.apply_patches(jpeg_file, patches, info.png_chunk)
    assert jpeg_file.read_bytes() == guarded, "a rejected patch must write nothing"


def test_zero_shift_produces_no_patches(jpeg_file):
    info = exif.read_exif_dates(jpeg_file)
    assert exif.build_shift_patches(info, timedelta(0)) == []


def test_unset_dates_are_ignored():
    assert exif.parse_exif_datetime("0000:00:00 00:00:00") is None
    assert exif.parse_exif_datetime("   ") is None
    assert exif.parse_exif_datetime("not a date") is None
    assert exif.parse_exif_datetime("2023:07:14 09:30:00\x00") == datetime(2023, 7, 14, 9, 30)
    assert exif.parse_exif_datetime("2023-07-14 09:30:00") == datetime(2023, 7, 14, 9, 30)


def test_dates_without_a_trailing_nul_are_writable():
    value = datetime(2024, 2, 29, 23, 59, 58)
    assert exif.format_exif_datetime(value, 20) == b"2024:02:29 23:59:58\x00"
    assert exif.format_exif_datetime(value, 19) == b"2024:02:29 23:59:58"
    with pytest.raises(ExifError):
        exif.format_exif_datetime(value, 12)


def test_a_file_with_no_dates_yields_no_patches(tmp_path):
    empty = build_tiff_block()
    # Blank out every date string in the data area.
    blanked = bytearray(empty)
    for offset in (68, 88, 108):
        blanked[offset : offset + 20] = b"0000:00:00 00:00:00\x00"
    path = tmp_path / "blank.tif"
    path.write_bytes(bytes(blanked))

    info = exif.read_exif_dates(path)
    assert info.has_dates is False
    assert info.primary is None
    assert exif.build_shift_patches(info, timedelta(hours=1)) == []


def test_unrecognised_files_raise(tmp_path):
    path = tmp_path / "notes.jpg"
    path.write_bytes(b"this is not an image at all")
    with pytest.raises(ExifError):
        exif.read_exif_dates(path)


def test_jpeg_without_exif_raises(tmp_path):
    path = tmp_path / "plain.jpg"
    path.write_bytes(b"\xff\xd8" + b"\xff\xda" + struct.pack(">H", 2) + b"\xff\xd9")
    with pytest.raises(ExifError, match="no EXIF segment"):
        exif.read_exif_dates(path)
