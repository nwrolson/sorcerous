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
    def __init__(self, zone_id: str, width: float=160, slot_h: float=90,
                 padding: float=8, orientation: str="vertical", hide_cards: bool=False,
                 interactive: bool=True, allow_drops: bool=True,
                 suppress_paint_when_hidden: bool=False):
        super().__init__()
        self.zone_id = zone_id
        self.width = width
        self.slot_h = slot_h
        self.padding = padding
        self.orientation = orientation
        self.hide_cards = hide_cards
        self._suppress_paint_when_hidden = suppress_paint_when_hidden
        self._interactive = interactive
        self.allow_drops = allow_drops
        self.cards: list[Card] = []
        self.highlight = False
        self.anchor_left: float | None = None
        self.anchor_bottom: float | None = None
        self.fixed_height: float | None = None
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        if self.fixed_height is not None:
            total_h = self.fixed_height
        elif self.orientation == "vertical":
            total_h = max(self.slot_h * max(1,len(self.cards)), self.slot_h)
        else:
            total_h = self.slot_h
        return QRectF(0,0,self.width, total_h)

    def paint(self, painter, option, widget=None):
        if not self._interactive:
            return
        r = self.boundingRect()
        bg = QColor(250,250,240) if not self.highlight else QColor(255,245,200)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor(160,160,120),1,Qt.DashLine))
        painter.drawRoundedRect(r, 10, 10)
        if self.orientation == "vertical":
            painter.setPen(QPen(QColor(200,200,180),1))
            y = self.padding
            for _ in range(max(1, len(self.cards))):
                painter.drawLine(QLineF(6, y, self.width-6, y))
                y += self.slot_h

    def index_at(self, scene_pos: QPointF) -> int:
        local = self.mapFromScene(scene_pos)
        if self.orientation == "vertical":
            y = local.y() - self.padding
            idx = int(max(0, y // self.slot_h))
            return min(idx, len(self.cards))
        else:
            x = local.x() - self.padding
            if x <= 0:
                return 0
            pos = self.padding
            for i, card in enumerate(self.cards):
                w = card.boundingRect().width()
                midpoint = pos + w / 2.0
                if x < midpoint:
                    return i
                pos += w + self.padding
            return len(self.cards)

    def pos_for(self, index: int) -> QPointF:
        if self.orientation == "vertical":
            y = self.padding + index * self.slot_h
            return self.mapToScene(QPointF(self.padding, y))
        x = self.padding
        for card in self.cards[:index]:
            x += card.boundingRect().width() + self.padding
        return self.mapToScene(QPointF(x, self.padding))

    def set_width(self, width: float):
        if width == self.width:
            return
        self.prepareGeometryChange()
        self.width = width
        self.update()
        self.reflow_cards()

    def set_height(self, height: float):
        height = max(1.0, height)
        if self.fixed_height == height:
            return
        self.prepareGeometryChange()
        self.fixed_height = height
        self.update()
        self.reflow_cards()

    def set_bottom_anchor(self, left: float, bottom: float):
        self.anchor_left = left
        self.anchor_bottom = bottom
        self.reflow_cards()

    def is_interactive(self) -> bool:
        return self._interactive

    def insert_card(self, index: int, card: Card):
        self.prepareGeometryChange()
        idx = max(0, min(index, len(self.cards)))
        self.cards.insert(idx, card)
        if hasattr(card, "set_tapped"):
            card.set_tapped(False)
        if self.hide_cards:
            card.set_face_down(True)
            card.set_zone_hidden(self._suppress_paint_when_hidden)
        self.reflow_cards()

    def remove_card(self, card: Card):
        if card not in self.cards:
            return
        self.prepareGeometryChange()
        self.cards.remove(card)
        if self.hide_cards:
            card.set_face_down(False)
            if self._suppress_paint_when_hidden:
                card.set_zone_hidden(False)
        self.reflow_cards()

    def reflow_cards(self):
        self._update_anchor_pos()
        for i, c in enumerate(self.cards):
            c.setPos(self.pos_for(i))

    def _update_anchor_pos(self):
        if self.anchor_left is None or self.anchor_bottom is None:
            return
        zone_height = self.boundingRect().height()
        desired = QPointF(self.anchor_left, self.anchor_bottom - zone_height)
        if self.pos() != desired:
            self.setPos(desired)
