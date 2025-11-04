from PySide6.QtCore import (
    Qt, QRectF, QPointF, Signal
)

from PySide6.QtWidgets import (
    QGraphicsObject
)

from PySide6.QtGui import (
    QBrush, QColor, QPen, QPixmap, QPainter
)

class Card(QGraphicsObject):
    moved = Signal(QPointF)
    def __init__(self, card_id: str, image_path: str = None, w: float=120, h: float=80, color: QColor=QColor(240,240,240)):
        super().__init__()
        self.card_id = card_id
        self.w = w
        self.h = h
        self.color = color

        # Pre-scale pixmap to card dimensions to avoid scaling on every paint
        if image_path:
            original = QPixmap(image_path)
            if not original.isNull():
                self.pixmap = original.scaled(
                    int(w), int(h),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            else:
                self.pixmap = None
        else:
            self.pixmap = None

        self.setFlags(
            QGraphicsObject.GraphicsItemFlag.ItemIsMovable
            | QGraphicsObject.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)
        self._press_pos: QPointF | None = None
        self._selection_offsets: list[tuple[Card, QPointF]] = []

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.w, self.h)

    def paint(self, painter, option, widget=None):
        r = self.boundingRect()

        if self.pixmap and not self.pixmap.isNull():
            # Draw pre-scaled pixmap (already sized to card dimensions)
            # Use fast rendering mode - no smooth pixmap transform needed
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(0, 0, self.pixmap)
        else:
            # Fallback to manual drawing if no image provided
            painter.setPen(QPen(Qt.black, 1))
            painter.setBrush(QBrush(self.color))
            painter.drawRoundedRect(r, 8, 8)
            painter.setBrush(QBrush(QColor(210,210,210)))
            painter.drawRoundedRect(QRectF(0,0,self.w,20), 8, 8)
            painter.drawText(r.adjusted(6,22,-6,-6),
                             Qt.AlignLeft | Qt.AlignTop, self.card_id)

        # Draw selection highlight on top
        if self.isSelected():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor(0,120,215),2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(r.adjusted(1,1,-1,-1), 8, 8)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.scenePos()
            items = [it for it in self.scene().selectedItems() if isinstance(it, Card)]
            if self not in items:
                self.scene().clearSelection()
                self.setSelected(True)
                items = [self]
            self._selection_offsets = [(it, it.pos() - self.pos()) for it in items]
            self.setZValue(10)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._press_pos is not None:
            super().mouseMoveEvent(ev)
            for (it, off) in self._selection_offsets:
                if it is self:
                    continue
                it.setPos(self.pos() + off)
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self.setZValue(0)
        self._press_pos = None
        self._selection_offsets.clear()
        super().mouseReleaseEvent(ev)

    def itemChange(self, change, value):
        # Clamp while moving
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionChange and self.scene():
            new_pos = QPointF(value)  # value is the proposed pos in scene coords
            rect = self.scene().sceneRect()
            x = max(rect.left(),  min(new_pos.x(), rect.right()  - self.w))
            y = max(rect.top(),   min(new_pos.y(), rect.bottom() - self.h))
            return QPointF(x, y)

        # Notify after move
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionHasChanged:
            self.moved.emit(self.pos())

        return super().itemChange(change, value)