from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Signal, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from card.card import Card


class ProxyCardItem(QGraphicsObject):
    """Lightweight proxy that renders a hidden card inside the zone viewer."""

    dragStarted = Signal(object, object)   # self, event
    dragMoved = Signal(object, object)
    dragReleased = Signal(object, object)

    def __init__(self, card: Card, parent=None):
        super().__init__(parent)
        self.card = card
        self.setAcceptHoverEvents(False)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self._pixmap = None
        self._size = card.boundingRect().size()
        self._refresh_pixmap()

    def _refresh_pixmap(self):
        pixmap = getattr(self.card, "pixmap", None)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._pixmap = pixmap

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._size.width(), self._size.height())

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        if isinstance(self._pixmap, QPixmap) and not self._pixmap.isNull():
            painter.drawPixmap(self.boundingRect(), self._pixmap, self._pixmap.rect())
        else:
            painter.fillRect(self.boundingRect(), option.palette.window())

    # Mouse handling emits signals to the controller so it can orchestrate drag lifecycle.
    def mousePressEvent(self, event):
        self.dragStarted.emit(self, event)
        event.accept()

    def mouseMoveEvent(self, event):
        self.dragMoved.emit(self, event)
        event.accept()

    def mouseReleaseEvent(self, event):
        self.dragReleased.emit(self, event)
        event.accept()
