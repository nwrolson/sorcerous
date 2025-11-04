from PySide6.QtCore import (
    Qt, QRectF, QPointF, QLineF
)

from PySide6.QtWidgets import (
    QGraphicsObject
)

from PySide6.QtGui import (
    QBrush, QColor, QPen
)

from card.card import Card

class Zone(QGraphicsObject):
    def __init__(self, zone_id: str, width: float=160, slot_h: float=90, padding: float=8):
        super().__init__()
        self.zone_id = zone_id
        self.width = width
        self.slot_h = slot_h
        self.padding = padding
        self.cards: list[Card] = []
        self.highlight = False
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        total_h = max(self.slot_h * max(1,len(self.cards)), self.slot_h)
        return QRectF(0,0,self.width, total_h)

    def paint(self, painter, option, widget=None):
        r = self.boundingRect()
        bg = QColor(250,250,240) if not self.highlight else QColor(255,245,200)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor(160,160,120),1,Qt.DashLine))
        painter.drawRoundedRect(r, 10, 10)
        painter.setPen(QPen(QColor(200,200,180),1))
        y = self.padding
        for _ in range(max(1, len(self.cards))):
            painter.drawLine(QLineF(6, y, self.width-6, y))
            y += self.slot_h

    def index_at(self, scene_pos: QPointF) -> int:
        y = self.mapFromScene(scene_pos).y() - self.padding
        idx = int(max(0, y // self.slot_h))
        return min(idx, len(self.cards))

    def pos_for(self, index: int) -> QPointF:
        y = self.padding + index * self.slot_h
        return self.mapToScene(QPointF(self.padding, y))