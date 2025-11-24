from __future__ import annotations

from .zone import Zone
from card.card import Card
from PySide6.QtCore import QPointF


class GraveyardZone(Zone):
    """Hidden zone that keeps cards face up but off the battlefield."""

    _OFFSCREEN_POS = QPointF(-50000.0, -50000.0)

    def __init__(self, zone_id: str = "graveyard", width: float = 220,
                 slot_h: float = 120, padding: float = 12, card_gap: float = 16):
        super().__init__(
            zone_id=zone_id,
            width=width,
            slot_h=slot_h,
            padding=padding,
            orientation="vertical",
            hide_cards=True,
            interactive=False,
            allow_drops=False,
            suppress_paint_when_hidden=True,
        )
        self.card_gap = card_gap
        self.setVisible(False)
        self.move_offscreen()

    def insert_card(self, index: int, card: Card):
        super().insert_card(index, card)
        card.set_face_down(False)
        card.set_zone_hidden(True)

    def remove_card(self, card: Card):
        super().remove_card(card)
        card.set_zone_hidden(False)

    def layout_to_view(self, *_):
        """Hidden zone; nothing to layout within the scene."""
        return

    def move_offscreen(self):
        self.setPos(self._OFFSCREEN_POS)

    def index_at(self, scene_pos):
        local = self.mapFromScene(scene_pos)
        y = local.y() - self.padding
        if y <= 0:
            return 0
        offset = 0.0
        for i, card in enumerate(self.cards):
            card_h = card.boundingRect().height()
            midpoint = offset + card_h / 2.0
            if y < midpoint:
                return i
            offset += card_h + self.card_gap
        return len(self.cards)

    def pos_for(self, index: int):
        y = self.padding
        for card in self.cards[:index]:
            y += card.boundingRect().height() + self.card_gap
        return self.mapToScene(QPointF(self.padding, y))
