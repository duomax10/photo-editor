"""Locate and rewrite EXIF date/time tags without re-encoding the image.

EXIF date/time values are fixed-width ASCII ("YYYY:MM:DD HH:MM:SS"), so a
shifted value always occupies exactly as many bytes as the value it replaces.
That lets us patch the bytes where they sit instead of rebuilding the file:
pixel data is never touched, the file never changes size, and the original is
never deleted or replaced.

Supported containers (all parsed here with the standard library only):

* JPEG  -- EXIF lives in an APP1 segment.
* TIFF and TIFF-based raw (CR2, NEF, ARW, DNG, ...) -- EXIF is the file.
* HEIF/HEIC -- EXIF is an ISOBMFF item located through ``meta``/``iinf``/``iloc``.
* PNG -- EXIF lives in an ``eXIf`` chunk, whose CRC we recompute after patching.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# Tags holding a date/time string, keyed by the IFD they live in.
TAG_DATETIME = 0x0132
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_EXIF_IFD_POINTER = 0x8769

TAG_NAMES = {
    TAG_DATETIME: "DateTime",
    TAG_DATETIME_ORIGINAL: "DateTimeOriginal",
    TAG_DATETIME_DIGITIZED: "DateTimeDigitized",
}

# The tag we treat as "the" capture time when showing a single value.
PRIMARY_TAG_ORDER = (TAG_DATETIME_ORIGINAL, TAG_DATETIME_DIGITIZED, TAG_DATETIME)

EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"
_TYPE_ASCII = 2

# Baseline TIFF puts 42 in bytes 2-3. Several raw formats reuse the TIFF
# container and its IFD layout exactly, but stamp their own marker there, so a
# strict check for 42 rejects perfectly readable files.
TIFF_MAGIC = {
    42: "TIFF",
    85: "RW2",  # Panasonic
    0x4F52: "ORF",  # Olympus, "IIRO"
    0x5352: "ORF",  # Olympus, "IIRS"
}

# Fuji wraps a whole JPEG (EXIF and all) inside its own container.
RAF_SIGNATURE = b"FUJIFILMCCD-RAW"
RAF_JPEG_OFFSET_AT = 84

# Canon CR3 is ISOBMFF, with EXIF in Canon's private uuid box under moov.
CR3_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
CR3_EXIF_BOXES = (b"CMT1", b"CMT2")  # IFD0 and the Exif IFD, each its own TIFF

JPEG_EXTENSIONS = {".jpg", ".jpeg", ".jpe"}
TIFF_EXTENSIONS = {".tif", ".tiff"}
RAW_EXTENSIONS = {
    # TIFF-based, read and written directly.
    ".cr2",  # Canon, older
    ".nef",  # Nikon
    ".nrw",  # Nikon, compact
    ".arw",  # Sony
    ".sr2",  # Sony, older
    ".srf",  # Sony, older
    ".dng",  # Adobe / Leica / Pentax
    ".rwl",  # Leica
    ".orf",  # Olympus
    ".rw2",  # Panasonic
    ".pef",  # Pentax
    ".srw",  # Samsung
    ".erf",  # Epson
    ".3fr",  # Hasselblad
    ".iiq",  # Phase One
    ".mef",  # Mamiya
    ".mos",  # Leaf
    ".dcr",  # Kodak
    ".kdc",  # Kodak
    # Their own containers, handled separately.
    ".raf",  # Fuji: wraps a JPEG
    ".cr3",  # Canon, current: ISOBMFF
    # Recognised so the file dates still shift, though the EXIF may not parse.
    ".mrw",  # Minolta
    ".x3f",  # Sigma
}
HEIF_EXTENSIONS = {".heic", ".heif", ".hif"}
PNG_EXTENSIONS = {".png"}

SUPPORTED_EXTENSIONS = (
    JPEG_EXTENSIONS | TIFF_EXTENSIONS | RAW_EXTENSIONS | HEIF_EXTENSIONS | PNG_EXTENSIONS
)


class ExifError(Exception):
    """Raised when EXIF data is present but cannot be understood."""


@dataclass
class ExifDateField:
    """One date/time tag, pinned to the exact byte range it occupies on disk."""

    tag: int
    ifd: str
    file_offset: int
    byte_count: int
    raw: str
    value: datetime | None

    @property
    def name(self) -> str:
        return TAG_NAMES.get(self.tag, f"Tag{self.tag:#06x}")


@dataclass
class ExifDateInfo:
    """Everything needed to read and rewrite a file's EXIF timestamps."""

    container: str
    tiff_offset: int
    fields: list[ExifDateField] = field(default_factory=list)
    # (chunk_start, data_length) of the PNG eXIf chunk, so its CRC can be fixed.
    png_chunk: tuple[int, int] | None = None
    note: str = ""

    @property
    def has_dates(self) -> bool:
        return any(f.value is not None for f in self.fields)

    @property
    def primary(self) -> ExifDateField | None:
        for tag in PRIMARY_TAG_ORDER:
            for f in self.fields:
                if f.tag == tag and f.value is not None:
                    return f
        return None


@dataclass
class BytePatch:
    """A same-length byte replacement, verified against ``expect`` before writing."""

    offset: int
    expect: bytes
    replacement: bytes

    def __post_init__(self) -> None:
        if len(self.expect) != len(self.replacement):
            raise ExifError(
                f"refusing to change file length at offset {self.offset}: "
                f"{len(self.expect)} -> {len(self.replacement)} bytes"
            )

    def inverted(self) -> "BytePatch":
        return BytePatch(self.offset, self.replacement, self.expect)


def parse_exif_datetime(raw: str) -> datetime | None:
    """Parse an EXIF date string, tolerating the common "unset" spellings."""
    text = raw.strip().strip("\x00").strip()
    if not text or text.startswith("0000"):
        return None
    try:
        return datetime.strptime(text, EXIF_DATETIME_FORMAT)
    except ValueError:
        # Some cameras use '-' or '.' as the date separator.
        normalised = text.replace("-", ":").replace(".", ":")
        try:
            return datetime.strptime(normalised, EXIF_DATETIME_FORMAT)
        except ValueError:
            return None


def format_exif_datetime(value: datetime, byte_count: int) -> bytes:
    """Render ``value`` into exactly ``byte_count`` bytes.

    Cameras write either 20 bytes (19 characters plus a NUL) or, less often,
    19 bytes with no terminator. We match whatever the file already uses.
    """
    text = value.strftime(EXIF_DATETIME_FORMAT).encode("ascii")
    if byte_count == len(text):
        return text
    if byte_count == len(text) + 1:
        return text + b"\x00"
    raise ExifError(f"cannot fit a timestamp into {byte_count} bytes")


# --------------------------------------------------------------------------
# Container parsing: find where the TIFF header starts
# --------------------------------------------------------------------------


def _find_tiff_in_jpeg(data: bytes, start: int = 0) -> int:
    """Locate the EXIF TIFF header in a JPEG beginning at ``start``.

    ``start`` is non-zero when the JPEG is embedded in another container, as
    Fuji's raw format does.
    """
    pos = start + 2  # skip SOI
    end = len(data)
    while pos + 4 <= end:
        if data[pos] != 0xFF:
            raise ExifError("malformed JPEG segment structure")
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:  # start of scan; no metadata beyond here
            break
        (length,) = struct.unpack_from(">H", data, pos + 2)
        segment_start = pos + 4
        if marker == 0xE1 and data[segment_start : segment_start + 6] == b"Exif\x00\x00":
            return segment_start + 6
        pos += 2 + length
    raise ExifError("no EXIF segment in this JPEG")


def _find_tiff_in_png(data: bytes) -> tuple[int, tuple[int, int]]:
    pos = 8  # skip signature
    end = len(data)
    while pos + 8 <= end:
        (length,) = struct.unpack_from(">I", data, pos)
        chunk_type = data[pos + 4 : pos + 8]
        data_start = pos + 8
        if chunk_type == b"eXIf":
            return data_start, (pos, length)
        if chunk_type == b"IEND":
            break
        pos = data_start + length + 4  # payload + CRC
    raise ExifError("no eXIf chunk in this PNG")


def _iter_boxes(data: bytes, start: int, end: int):
    """Yield ``(box_type, payload_start, payload_end)`` for ISOBMFF boxes."""
    pos = start
    while pos + 8 <= end:
        (size,) = struct.unpack_from(">I", data, pos)
        box_type = data[pos + 4 : pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                break
            (size,) = struct.unpack_from(">Q", data, pos + 8)
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            break
        yield box_type, pos + header, pos + size
        pos += size


def _find_box(data: bytes, start: int, end: int, path: tuple[bytes, ...]):
    """Descend ``path`` through nested boxes, returning the innermost payload."""
    for box_type, payload_start, payload_end in _iter_boxes(data, start, end):
        if box_type != path[0]:
            continue
        if len(path) == 1:
            return payload_start, payload_end
        inner_start = payload_start
        if path[0] == b"meta":
            inner_start += 4  # 'meta' is a FullBox: version + flags
        found = _find_box(data, inner_start, payload_end, path[1:])
        if found:
            return found
    return None


def _heif_exif_item_id(data: bytes, start: int, end: int) -> int:
    """Return the item ID whose item_type is 'Exif', via the iinf box."""
    pos = start + 4  # FullBox version + flags
    version = data[start]
    if version == 0:
        (count,) = struct.unpack_from(">H", data, pos)
        pos += 2
    else:
        (count,) = struct.unpack_from(">I", data, pos)
        pos += 4
    for _ in range(count):
        if pos + 8 > end:
            break
        (size,) = struct.unpack_from(">I", data, pos)
        if data[pos + 4 : pos + 8] != b"infe" or size < 12:
            pos += max(size, 8)
            continue
        infe_version = data[pos + 8]
        body = pos + 12  # box header + FullBox version/flags
        if infe_version in (0, 1):
            (item_id,) = struct.unpack_from(">H", data, body)
            # Version 0/1 name the type in a trailing string, not a 4CC.
            if data[body + 4 : body + 8] == b"Exif":
                return item_id
        else:
            if infe_version == 2:
                (item_id,) = struct.unpack_from(">H", data, body)
                type_at = body + 4
            else:
                (item_id,) = struct.unpack_from(">I", data, body)
                type_at = body + 6
            if data[type_at : type_at + 4] == b"Exif":
                return item_id
        pos += size
    raise ExifError("no EXIF item listed in this HEIF file")


def _heif_item_extent(data: bytes, start: int, end: int, item_id: int) -> tuple[int, int]:
    """Return ``(offset, length)`` of an item's payload, via the iloc box."""
    version = data[start]
    pos = start + 4
    sizes = data[pos]
    offset_size, length_size = sizes >> 4, sizes & 0xF
    sizes = data[pos + 1]
    base_offset_size, index_size = sizes >> 4, sizes & 0xF
    pos += 2
    if version < 2:
        (item_count,) = struct.unpack_from(">H", data, pos)
        pos += 2
    else:
        (item_count,) = struct.unpack_from(">I", data, pos)
        pos += 4

    def read_int(at: int, width: int) -> tuple[int, int]:
        if width == 0:
            return 0, at
        return int.from_bytes(data[at : at + width], "big"), at + width

    for _ in range(item_count):
        if pos >= end:
            break
        if version < 2:
            (this_id,) = struct.unpack_from(">H", data, pos)
            pos += 2
        else:
            (this_id,) = struct.unpack_from(">I", data, pos)
            pos += 4
        if version in (1, 2):
            pos += 2  # reserved + construction_method
        pos += 2  # data_reference_index
        base_offset, pos = read_int(pos, base_offset_size)
        (extent_count,) = struct.unpack_from(">H", data, pos)
        pos += 2
        for _ in range(extent_count):
            if version in (1, 2) and index_size:
                _, pos = read_int(pos, index_size)
            extent_offset, pos = read_int(pos, offset_size)
            extent_length, pos = read_int(pos, length_size)
            if this_id == item_id:
                return base_offset + extent_offset, extent_length
    raise ExifError(f"HEIF item {item_id} has no locatable payload")


def _find_tiff_in_heif(data: bytes) -> int:
    meta = _find_box(data, 0, len(data), (b"meta",))
    if not meta:
        raise ExifError("no meta box in this HEIF file")
    meta_start = meta[0] + 4  # FullBox version + flags
    meta_end = meta[1]
    iinf = _find_box(data, meta_start, meta_end, (b"iinf",))
    iloc = _find_box(data, meta_start, meta_end, (b"iloc",))
    if not iinf or not iloc:
        raise ExifError("HEIF file has no iinf/iloc box")
    item_id = _heif_exif_item_id(data, *iinf)
    payload_offset, payload_length = _heif_item_extent(data, iloc[0], iloc[1], item_id)
    if payload_length < 4 or payload_offset + 4 > len(data):
        raise ExifError("HEIF EXIF payload is truncated")
    # ExifDataBlock: a 4-byte offset, then optionally "Exif\0\0", then TIFF.
    (header_offset,) = struct.unpack_from(">I", data, payload_offset)
    return payload_offset + 4 + header_offset


def _find_tiff_in_raf(data: bytes) -> int:
    """Fuji raw: a container wrapping a full JPEG, which carries the EXIF."""
    if len(data) < RAF_JPEG_OFFSET_AT + 8:
        raise ExifError("this Fuji raw file is truncated")
    (jpeg_offset,) = struct.unpack_from(">I", data, RAF_JPEG_OFFSET_AT)
    if jpeg_offset <= 0 or jpeg_offset + 2 > len(data):
        raise ExifError("this Fuji raw file has no embedded JPEG")
    if data[jpeg_offset : jpeg_offset + 2] != b"\xff\xd8":
        raise ExifError("the embedded JPEG in this Fuji raw file is not where it says")
    return _find_tiff_in_jpeg(data, jpeg_offset)


def _find_tiff_in_cr3(data: bytes) -> list[int]:
    """Canon CR3: EXIF sits in CMT1/CMT2 boxes in Canon's uuid box under moov.

    Each box holds a complete, self-contained TIFF block rather than one block
    with an Exif IFD pointer, so this returns an offset per box.
    """
    moov = _find_box(data, 0, len(data), (b"moov",))
    if not moov:
        raise ExifError("no moov box in this CR3 file")

    for box_type, payload_start, payload_end in _iter_boxes(data, *moov):
        if box_type != b"uuid" or data[payload_start : payload_start + 16] != CR3_UUID:
            continue
        offsets = [
            start
            for kind, start, _ in _iter_boxes(data, payload_start + 16, payload_end)
            if kind in CR3_EXIF_BOXES
        ]
        if offsets:
            return offsets
    raise ExifError("no EXIF boxes in this CR3 file")


def _locate_tiff(data: bytes, suffix: str) -> tuple[str, list[int], tuple[int, int] | None]:
    """Return the container name, every TIFF block offset in it, and any PNG chunk."""
    if data[:2] == b"\xff\xd8":
        return "JPEG", [_find_tiff_in_jpeg(data)], None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        tiff_offset, chunk = _find_tiff_in_png(data)
        return "PNG", [tiff_offset], chunk
    if data[:2] in (b"II", b"MM"):
        container = "RAW" if suffix in RAW_EXTENSIONS else "TIFF"
        return container, [0], None
    if data[: len(RAF_SIGNATURE)] == RAF_SIGNATURE:
        return "RAF", [_find_tiff_in_raf(data)], None
    if data[4:8] == b"ftyp":
        # CR3 is ISOBMFF like HEIF, but stores EXIF Canon's own way.
        if suffix == ".cr3" or data[8:12] == b"crx ":
            return "CR3", _find_tiff_in_cr3(data), None
        return "HEIF", [_find_tiff_in_heif(data)], None
    raise ExifError("unrecognised file format")


# --------------------------------------------------------------------------
# TIFF/IFD walking
# --------------------------------------------------------------------------


def _read_ifd_dates(
    data: bytes,
    tiff_offset: int,
    ifd_offset: int,
    endian: str,
    ifd_name: str,
    fields: list[ExifDateField],
    seen: set[int],
) -> int | None:
    """Collect date fields from one IFD; return a nested Exif IFD offset if any."""
    absolute = tiff_offset + ifd_offset
    if absolute in seen or absolute + 2 > len(data):
        return None
    seen.add(absolute)

    (entry_count,) = struct.unpack_from(endian + "H", data, absolute)
    exif_ifd_offset = None
    for index in range(entry_count):
        entry = absolute + 2 + index * 12
        if entry + 12 > len(data):
            break
        tag, value_type, count = struct.unpack_from(endian + "HHI", data, entry)

        if tag == TAG_EXIF_IFD_POINTER and count == 1:
            (exif_ifd_offset,) = struct.unpack_from(endian + "I", data, entry + 8)
            continue
        if tag not in TAG_NAMES or value_type != _TYPE_ASCII:
            continue
        if count not in (19, 20):
            continue  # not a plain date string; leave it alone

        # An ASCII value longer than 4 bytes is stored out of line.
        (value_offset,) = struct.unpack_from(endian + "I", data, entry + 8)
        file_offset = tiff_offset + value_offset
        if file_offset + count > len(data):
            continue
        raw = data[file_offset : file_offset + count].decode("ascii", "replace")
        fields.append(
            ExifDateField(
                tag=tag,
                ifd=ifd_name,
                file_offset=file_offset,
                byte_count=count,
                raw=raw,
                value=parse_exif_datetime(raw),
            )
        )
    return exif_ifd_offset


def _read_dates(data: bytes, tiff_offset: int) -> list[ExifDateField]:
    if tiff_offset + 8 > len(data):
        raise ExifError("EXIF block is truncated")
    byte_order = data[tiff_offset : tiff_offset + 2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        raise ExifError("EXIF block has no valid byte-order mark")

    (magic,) = struct.unpack_from(endian + "H", data, tiff_offset + 2)
    if magic not in TIFF_MAGIC:
        raise ExifError(
            f"EXIF block has an unrecognised TIFF magic number ({magic:#06x})"
        )
    (ifd0_offset,) = struct.unpack_from(endian + "I", data, tiff_offset + 4)

    fields: list[ExifDateField] = []
    seen: set[int] = set()
    exif_ifd = _read_ifd_dates(data, tiff_offset, ifd0_offset, endian, "IFD0", fields, seen)
    if exif_ifd:
        _read_ifd_dates(data, tiff_offset, exif_ifd, endian, "ExifIFD", fields, seen)
    return fields


def read_exif_dates(path: Path) -> ExifDateInfo:
    """Read every writable date/time tag in ``path``.

    Raises :class:`ExifError` when the file has no usable EXIF block.
    """
    data = path.read_bytes()
    container, tiff_offsets, png_chunk = _locate_tiff(data, path.suffix.lower())

    fields: list[ExifDateField] = []
    seen_offsets: set[int] = set()
    errors: list[str] = []
    for tiff_offset in tiff_offsets:
        try:
            found = _read_dates(data, tiff_offset)
        except ExifError as error:
            # One unreadable block should not hide the dates in another.
            errors.append(str(error))
            continue
        for field_ in found:
            if field_.file_offset not in seen_offsets:
                seen_offsets.add(field_.file_offset)
                fields.append(field_)

    if not fields and errors:
        raise ExifError(errors[0])

    note = ""
    if container in ("RAW", "CR3", "RAF"):
        note = "Raw files may also store a copy of the time in the maker note, which is left as-is."
    return ExifDateInfo(
        container=container,
        tiff_offset=tiff_offsets[0] if tiff_offsets else 0,
        fields=fields,
        png_chunk=png_chunk,
        note=note,
    )


# --------------------------------------------------------------------------
# Patching
# --------------------------------------------------------------------------


def build_shift_patches(info: ExifDateInfo, delta: timedelta) -> list[BytePatch]:
    """Build the byte edits that shift every readable date field by ``delta``."""
    patches: list[BytePatch] = []
    for f in info.fields:
        if f.value is None:
            continue
        new_bytes = format_exif_datetime(f.value + delta, f.byte_count)
        old_bytes = f.raw.encode("ascii", "replace")
        if new_bytes == old_bytes:
            continue
        patches.append(BytePatch(f.file_offset, old_bytes, new_bytes))
    return patches


def apply_patches(path: Path, patches: list[BytePatch], png_chunk: tuple[int, int] | None) -> None:
    """Write ``patches`` in place, verifying each target first.

    The file is opened for in-place update: nothing is truncated, moved, or
    deleted, and its length is unchanged. If any target does not hold the bytes
    we expect, nothing at all is written.
    """
    if not patches:
        return
    with path.open("r+b") as handle:
        for patch in patches:
            handle.seek(patch.offset)
            current = handle.read(len(patch.expect))
            if current != patch.expect:
                raise ExifError(
                    f"{path.name} changed on disk since it was scanned "
                    f"(offset {patch.offset}); nothing was written"
                )
        for patch in patches:
            handle.seek(patch.offset)
            handle.write(patch.replacement)
        if png_chunk is not None:
            _fix_png_crc(handle, png_chunk)
        handle.flush()


def _fix_png_crc(handle, png_chunk: tuple[int, int]) -> None:
    """Recompute the CRC of the eXIf chunk we just edited."""
    chunk_start, data_length = png_chunk
    handle.seek(chunk_start + 4)
    payload = handle.read(4 + data_length)  # chunk type + data
    handle.seek(chunk_start + 8 + data_length)
    handle.write(struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF))
