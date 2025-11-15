from PySide6.QtCore import QPointF, QRectF

from .zone import Zone
from card.card import Card


class PreviewZone(Zone):
    """Special zone that displays a single card next to the deck view."""

    def __init__(self, zone_id: str = "preview", width: float = 220, slot_h: float = 320):
        super().__init__(
            zone_id=zone_id,
            width=width,
            slot_h=slot_h,
            padding=12,
            orientation="vertical",
            hide_cards=False,
            interactive=False,
            allow_drops=False,
        )

    def paint(self, painter, option, widget=None):
        # No chrome; the deck view widget provides the surrounding frame.
        return

    def reflow_cards(self):
        if not self.cards:
            return
        for card in self.cards:
            target = self._center_pos(card)
            card.setPos(target)

    def _center_pos(self, card: Card) -> QPointF:
        rect = self.boundingRect()
        card_rect = card.boundingRect()
        x = rect.x() + (rect.width() - card_rect.width()) / 2
        y = rect.y() + (rect.height() - card_rect.height()) / 2
        return self.mapToScene(QPointF(x, y))

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width, self.slot_h)
