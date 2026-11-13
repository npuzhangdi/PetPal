"""Real rounded native window regions, including Windows 10."""
from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainterPath, QRegion


def round_window(widget, radius=18):
    path = QPainterPath()
    path.addRoundedRect(QRectF(widget.rect()), radius, radius)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))
