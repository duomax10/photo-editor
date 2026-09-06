"""Render the application icon.

Run this only when the icon design changes; the generated files are committed
so that building on Windows needs no extra tooling:

    python tools/make_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication

RESOURCES = Path(__file__).resolve().parent.parent / "photo_timestamp_editor" / "resources"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

BACKDROP_TOP = QColor("#3d7dd8")
BACKDROP_BOTTOM = QColor("#2a5fb0")
PHOTO = QColor("#ffffff")
SKY = QColor("#cfe3ff")
HILL = QColor("#2f7d4f")
SUN = QColor("#ffc93c")
DIAL = QColor("#ffffff")
DIAL_EDGE = QColor("#1f3f73")


def draw_icon(size: int) -> QImage:
    """Draw the icon at ``size`` px: a photo with a clock badge on the corner."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    unit = size / 100.0

    # Rounded backdrop.
    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0.0, BACKDROP_TOP)
    gradient.setColorAt(1.0, BACKDROP_BOTTOM)
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, size, size), 22 * unit, 22 * unit)

    # The photo: a white card holding a small landscape.
    card = QRectF(16 * unit, 20 * unit, 60 * unit, 46 * unit)
    painter.setBrush(PHOTO)
    painter.drawRoundedRect(card, 4 * unit, 4 * unit)

    inner = card.adjusted(4 * unit, 4 * unit, -4 * unit, -4 * unit)
    painter.save()
    painter.setClipRect(inner)
    painter.setBrush(SKY)
    painter.drawRect(inner)
    painter.setBrush(SUN)
    painter.drawEllipse(
        QPointF(inner.left() + inner.width() * 0.72, inner.top() + inner.height() * 0.28),
        5 * unit,
        5 * unit,
    )
    painter.setBrush(HILL)
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(inner.left(), inner.bottom()),
                QPointF(inner.left() + inner.width() * 0.34, inner.top() + inner.height() * 0.42),
                QPointF(inner.left() + inner.width() * 0.62, inner.bottom()),
            ]
        )
    )
    painter.setBrush(HILL.darker(115))
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(inner.left() + inner.width() * 0.44, inner.bottom()),
                QPointF(inner.left() + inner.width() * 0.72, inner.top() + inner.height() * 0.55),
                QPointF(inner.right(), inner.bottom()),
            ]
        )
    )
    painter.restore()

    # Clock badge, sitting over the bottom-right corner of the photo.
    dial = QRectF(50 * unit, 48 * unit, 42 * unit, 42 * unit)
    painter.setBrush(DIAL)
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(dial.adjusted(-3 * unit, -3 * unit, 3 * unit, 3 * unit))
    painter.setBrush(DIAL_EDGE)
    painter.drawEllipse(dial)
    painter.setBrush(DIAL)
    painter.drawEllipse(dial.adjusted(3 * unit, 3 * unit, -3 * unit, -3 * unit))

    # Hands, at roughly 10:10 so they read as a clock even at 16 px.
    centre = dial.center()
    # Build the pen from scratch: the painter's current pen is NoPen, and
    # recolouring a NoPen pen still draws nothing.
    pen = QPen(DIAL_EDGE)
    pen.setCapStyle(Qt.RoundCap)
    pen.setWidthF(max(1.4, 3.4 * unit))
    painter.setPen(pen)
    painter.drawLine(centre, QPointF(centre.x(), centre.y() - dial.height() * 0.30))
    painter.drawLine(centre, QPointF(centre.x() + dial.width() * 0.24, centre.y()))

    painter.end()
    return image


def to_png_bytes(image: QImage) -> bytes:
    # QBuffer does not take ownership of an external QByteArray, so let it use
    # its own internal one rather than handing it a temporary that Python frees.
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def write_ico(path: Path, images: dict[int, QImage]) -> None:
    """Assemble a multi-resolution .ico using PNG-compressed entries."""
    payloads = {size: to_png_bytes(image) for size, image in images.items()}
    header = struct.pack("<HHH", 0, 1, len(payloads))  # reserved, type=icon, count
    offset = len(header) + 16 * len(payloads)

    entries, blobs = b"", b""
    for size in sorted(payloads):
        data = payloads[size]
        dimension = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset
        )
        blobs += data
        offset += len(data)

    path.write_bytes(header + entries + blobs)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    RESOURCES.mkdir(parents=True, exist_ok=True)

    images = {size: draw_icon(size) for size in ICO_SIZES}
    write_ico(RESOURCES / "app.ico", images)
    images[256].save(str(RESOURCES / "app.png"), "PNG")

    print(f"wrote {RESOURCES / 'app.ico'} ({(RESOURCES / 'app.ico').stat().st_size} bytes)")
    print(f"wrote {RESOURCES / 'app.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
