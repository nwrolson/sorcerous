from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF

from .zone import Zone


class LibraryZone(Zone):
    """Vertical zone representing the player's library along the right edge."""

    def __init__(self, zone_id: str = "library", width: float = 200,
                 slot_h: float = 120, padding: float = 12, card_gap: float = 16):
        super().__init__(
            zone_id=zone_id,
            width=width,
            slot_h=slot_h,
            padding=padding,
            orientation="vertical",
            hide_cards=True,
        )
        self.card_gap = card_gap

    def layout_to_view(self, view_rect: QRectF, hand_zone: Optional[Zone] = None):
        """Stretch from the top of the view down to just above the given hand zone."""
        if view_rect.isNull() or view_rect.width() <= 0:
            return

        hand_height = hand_zone.boundingRect().height() if hand_zone else 0.0
        available_height = max(self.slot_h, view_rect.height() - hand_height)

        self.set_height(available_height)
        left = view_rect.right() - self.width
        top = view_rect.top()
        self.setPos(QPointF(left, top))
        self.reflow_cards()

    def index_at(self, scene_pos: QPointF) -> int:
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

    def pos_for(self, index: int) -> QPointF:
        y = self.padding
        for card in self.cards[:index]:
            y += card.boundingRect().height() + self.card_gap
        return self.mapToScene(QPointF(self.padding, y))
