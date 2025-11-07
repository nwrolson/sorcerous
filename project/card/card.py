from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtWidgets import QGraphicsObject
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap, QPainter

class Card(QGraphicsObject):
    # Render target for all Card paints: "screen" or "camera"
    render_target = "screen"
    # Shared card back cache (pre-scaled per (w,h) on demand)
    _back_cache: dict[tuple[int,int], QPixmap] = {}

    moved = Signal(QPointF)

    def __init__(self, card_id: str, image_path: str = None,
                 w: float = 120, h: float = 80,
                 color: QColor = QColor(240, 240, 240),
                 visible: bool = True,
                 back_image_path: str | None = None):
        super().__init__()
        self.card_id = card_id
        self.w = w
        self.h = h
        self.color = color
        self.visible = visible
        self._back_image_path = back_image_path

        # Front image
        self.pixmap: QPixmap | None = None
        if image_path:
            src = QPixmap(image_path)
            if not src.isNull():
                self.pixmap = src.scaled(int(w), int(h),
                                         Qt.IgnoreAspectRatio,
                                         Qt.SmoothTransformation)

        self.setFlags(
            QGraphicsObject.ItemIsMovable
            | QGraphicsObject.ItemIsSelectable
            | QGraphicsObject.ItemSendsGeometryChanges
        )
        self.setCacheMode(QGraphicsObject.DeviceCoordinateCache)
        self._press_pos: QPointF | None = None
        self._selection_offsets: list[tuple["Card", QPointF]] = []

    def _back_pixmap(self) -> QPixmap | None:
        """Return a pre-scaled back pixmap for (w,h). Cache per size."""
        key = (int(self.w), int(self.h))
        pm = self._back_cache.get(key)
        if pm is None:
            src: QPixmap | None = None
            if self._back_image_path:
                src = QPixmap(self._back_image_path)
            # Fallback: simple gray back if no file
            if not src or src.isNull():
                tmp = QPixmap(int(self.w), int(self.h))
                tmp.fill(QColor(60, 60, 60))
                p = QPainter(tmp)
                p.setPen(QPen(QColor(200, 200, 200), 2))
                p.drawRoundedRect(QRectF(1, 1, self.w - 2, self.h - 2), 8, 8)
                p.end()
                pm = tmp
            else:
                pm = src.scaled(int(self.w), int(self.h),
                                Qt.IgnoreAspectRatio,
                                Qt.SmoothTransformation)
            self._back_cache[key] = pm
        return pm

    def set_card_back(self, image_path: str | None):
        """Optional: set/replace the back art and clear cache for this size."""
        print("Setting card back to: " + str(image_path))
        self._back_image_path = image_path
        key = (int(self.w), int(self.h))
        if key in self._back_cache:
            del self._back_cache[key]
        # trigger redraws when toggling between passes
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.w, self.h)

    def paint(self, painter: QPainter, option, widget=None):
        r = self.boundingRect()

        # Choose face based on pass
        face_down = (Card.render_target == "camera" and not self.visible)

        if face_down:
            back = self._back_pixmap()
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawPixmap(0, 0, back)
        elif self.pixmap and not self.pixmap.isNull():
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawPixmap(0, 0, self.pixmap)
        else:
            painter.setPen(QPen(Qt.black, 1))
            painter.setBrush(QBrush(self.color))
            painter.drawRoundedRect(r, 8, 8)
            painter.setBrush(QBrush(QColor(210, 210, 210)))
            painter.drawRoundedRect(QRectF(0, 0, self.w, 20), 8, 8)
            painter.drawText(r.adjusted(6, 22, -6, -6),
                             Qt.AlignLeft | Qt.AlignTop, self.card_id)

        # Hide selection chrome in camera pass
        if self.isSelected() and Card.render_target != "camera":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(0, 120, 215), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)

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