from PySide6.QtCore import (
    QPointF
)

class BoardModel:
    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.containers: dict[str, str] = {}
        self.zones: dict[str, dict[str, list[str]]] = {}

    def ensure_card(self, card_id: str):
        self.cards.setdefault(card_id, {"pos": QPointF(0, 0)})
        self.containers.setdefault(card_id, "table")

    def ensure_zone(self, zone_id: str):
        self.zones.setdefault(zone_id, {"order": []})