"""Builders for tiny synthetic files carrying real EXIF blocks.

Everything here is assembled byte by byte so the tests exercise the parsers
against known-good structures without needing sample photos in the repo.
"""

from __future__ import annotations

import os
import struct
import zlib

import pytest

# Qt tests run headless; must be set before any QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Layout of the TIFF block produced by :func:`build_tiff_block`.
IFD0_OFFSET = 8
EXIF_IFD_OFFSET = 38
DATA_OFFSET = 68


def build_tiff_block(
    date_time: str = "2023:07:14 09:30:00",
    date_time_original: str = "2023:07:14 09:30:00",
    date_time_digitized: str = "2023:07:14 09:30:00",
) -> bytes:
    """A little-endian TIFF block with DateTime plus an Exif IFD holding two more."""

    def ascii20(value: str) -> bytes:
        encoded = value.encode("ascii")
        assert len(encoded) == 19, "EXIF dates are 19 characters"
        return encoded + b"\x00"

    def entry(tag: int, type_: int, count: int, value: int) -> bytes:
        return struct.pack("<HHII", tag, type_, count, value)

    header = b"II" + struct.pack("<HI", 42, IFD0_OFFSET)

    ifd0 = struct.pack("<H", 2)
    ifd0 += entry(0x0132, 2, 20, DATA_OFFSET)  # DateTime
    ifd0 += entry(0x8769, 4, 1, EXIF_IFD_OFFSET)  # Exif IFD pointer
    ifd0 += struct.pack("<I", 0)  # no IFD1

    exif_ifd = struct.pack("<H", 2)
    exif_ifd += entry(0x9003, 2, 20, DATA_OFFSET + 20)  # DateTimeOriginal
    exif_ifd += entry(0x9004, 2, 20, DATA_OFFSET + 40)  # DateTimeDigitized
    exif_ifd += struct.pack("<I", 0)

    data = ascii20(date_time) + ascii20(date_time_original) + ascii20(date_time_digitized)

    block = header + ifd0 + exif_ifd + data
    assert len(header + ifd0) == EXIF_IFD_OFFSET
    assert len(header + ifd0 + exif_ifd) == DATA_OFFSET
    return block


def build_jpeg(tiff: bytes) -> bytes:
    app1 = b"Exif\x00\x00" + tiff
    out = b"\xff\xd8"  # SOI
    out += b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    out += b"\xff\xdb" + struct.pack(">H", 4) + b"\x00\x00"  # a filler segment
    out += b"\xff\xda" + struct.pack(">H", 4) + b"\x00\x00"  # start of scan
    out += b"\x11\x22\x33\x44"  # stand-in for compressed image data
    out += b"\xff\xd9"  # EOI
    return out


def build_png(tiff: bytes) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"eXIf", tiff)
        + chunk(b"IEND", b"")
    )


def build_heif(tiff: bytes) -> bytes:
    """A minimal ISOBMFF file whose meta/iinf/iloc point at an Exif item."""

    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload

    ftyp = box(b"ftyp", b"heic" + struct.pack(">I", 0))

    infe = box(
        b"infe",
        struct.pack(">BBBB", 2, 0, 0, 0)  # version 2, flags
        + struct.pack(">HH", 1, 0)  # item_ID, protection_index
        + b"Exif"
        + b"\x00",  # empty item_name
    )
    iinf = box(b"iinf", struct.pack(">BBBBH", 0, 0, 0, 0, 1) + infe)

    payload = struct.pack(">I", 0) + tiff  # exif_tiff_header_offset = 0

    def make_iloc(extent_offset: int) -> bytes:
        body = struct.pack(">BBBB", 0, 0, 0, 0)  # version 0, flags
        body += bytes([0x44, 0x00])  # offset_size=4, length_size=4, base_offset_size=0
        body += struct.pack(">H", 1)  # item_count
        body += struct.pack(">HH", 1, 0)  # item_ID, data_reference_index
        body += struct.pack(">H", 1)  # extent_count
        body += struct.pack(">II", extent_offset, len(payload))
        return box(b"iloc", body)

    # The extent offset is absolute, so size the boxes first with a placeholder.
    meta_size = 8 + 4 + len(iinf) + len(make_iloc(0))
    mdat_payload_start = len(ftyp) + meta_size + 8

    meta = box(b"meta", struct.pack(">I", 0) + iinf + make_iloc(mdat_payload_start))
    assert len(meta) == meta_size
    return ftyp + meta + box(b"mdat", payload)


@pytest.fixture
def tiff_block() -> bytes:
    return build_tiff_block()


@pytest.fixture
def jpeg_file(tmp_path, tiff_block):
    path = tmp_path / "IMG_0001.jpg"
    path.write_bytes(build_jpeg(tiff_block))
    return path


@pytest.fixture
def png_file(tmp_path, tiff_block):
    path = tmp_path / "shot.png"
    path.write_bytes(build_png(tiff_block))
    return path


@pytest.fixture
def heif_file(tmp_path, tiff_block):
    path = tmp_path / "IMG_0002.heic"
    path.write_bytes(build_heif(tiff_block))
    return path


@pytest.fixture
def tiff_file(tmp_path, tiff_block):
    path = tmp_path / "scan.tif"
    path.write_bytes(tiff_block)
    return path


@pytest.fixture
def raw_file(tmp_path, tiff_block):
    path = tmp_path / "DSC_0003.nef"
    path.write_bytes(tiff_block)
    return path


def build_tiff_variant(magic: int) -> bytes:
    """A TIFF block stamped with a raw format's own magic number.

    Olympus and Panasonic reuse TIFF's IFD layout but put their own marker
    where baseline TIFF has 42.
    """
    block = bytearray(build_tiff_block())
    struct.pack_into("<H", block, 2, magic)
    return bytes(block)


def build_raf(tiff: bytes) -> bytes:
    """Fuji raw: a header whose offset table points at a complete JPEG."""
    jpeg = build_jpeg(tiff)
    header = bytearray(b"\x00" * 148)
    header[0:15] = b"FUJIFILMCCD-RAW"
    header[15:20] = b"0201\x00"
    struct.pack_into(">I", header, 84, len(header))  # JPEG offset
    struct.pack_into(">I", header, 88, len(jpeg))  # JPEG length
    return bytes(header) + jpeg


def build_cr3(tiff: bytes) -> bytes:
    """Canon CR3: EXIF in CMT1/CMT2 boxes inside Canon's uuid box under moov."""

    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload

    ftyp = box(b"ftyp", b"crx " + struct.pack(">I", 0))
    # CMT1 is IFD0 and CMT2 the Exif IFD; each is a standalone TIFF block.
    canon_uuid = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
    uuid_box = box(b"uuid", canon_uuid + box(b"CMT1", tiff) + box(b"CMT2", tiff))
    return ftyp + box(b"moov", uuid_box)


@pytest.fixture
def orf_file(tmp_path):
    path = tmp_path / "P1010001.orf"
    path.write_bytes(build_tiff_variant(0x4F52))
    return path


@pytest.fixture
def rw2_file(tmp_path):
    path = tmp_path / "P1010002.rw2"
    path.write_bytes(build_tiff_variant(85))
    return path


@pytest.fixture
def raf_file(tmp_path, tiff_block):
    path = tmp_path / "DSCF0001.raf"
    path.write_bytes(build_raf(tiff_block))
    return path


@pytest.fixture
def cr3_file(tmp_path, tiff_block):
    path = tmp_path / "IMG_5001.cr3"
    path.write_bytes(build_cr3(tiff_block))
    return path
