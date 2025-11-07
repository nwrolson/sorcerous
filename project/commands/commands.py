from PySide6.QtGui import (
    QUndoCommand
)

from PySide6.QtCore import (
    QPointF
)

from zones.zone import Zone
from card.card import Card
from model.board import BoardModel


# -------- Undo Commands --------
class MoveCardsCommand(QUndoCommand):
    def __init__(self, model: BoardModel, cards: list[Card],
                 from_pos: dict[str, QPointF], to_pos: dict[str, QPointF]):
        super().__init__("Move cards")
        self.model = model
        self.cards = cards
        self.from_pos = from_pos
        self.to_pos = to_pos

    def redo(self):
        for c in self.cards:
            p = self.to_pos[c.card_id]
            c.setPos(p)
            self.model.cards[c.card_id]["pos"] = p

    def undo(self):
        for c in self.cards:
            p = self.from_pos[c.card_id]
            c.setPos(p)
            self.model.cards[c.card_id]["pos"] = p

class InsertIntoZoneCommand(QUndoCommand):
    def __init__(self, model: BoardModel, zones: dict[str, Zone], card: Card, zone: Zone,
                 index: int, table_pos: QPointF):
        super().__init__("Insert card into zone")
        self.model = model
        self.zones = zones
        self.card = card
        self.zone = zone
        self.index = index
        self.table_pos = table_pos
        self.prev_container = model.containers[card.card_id]
        self.prev_zone: Zone | None = None
        self.prev_index: int | None = None
        if self.prev_container != 'table':
            self.prev_zone = zones[self.prev_container]
            self.prev_index = self.prev_zone.cards.index(card)

    def _apply_insert(self):
        if self.prev_container != 'table' and self.prev_zone:
            self.prev_zone.remove_card(self.card)
        self.zone.insert_card(self.index, self.card)
        self.model.containers[self.card.card_id] = self.zone.zone_id
        self.model.zones[self.zone.zone_id]["order"] = [c.card_id for c in self.zone.cards]

    def _apply_revert(self):
        if self.card in self.zone.cards:
            self.zone.remove_card(self.card)
        if self.prev_container == 'table':
            self.model.containers[self.card.card_id] = 'table'
            self.card.setPos(self.table_pos)
        else:
            if self.prev_zone is not None and self.prev_index is not None:
                self.prev_zone.insert_card(self.prev_index, self.card)
                self.model.containers[self.card.card_id] = self.prev_zone.zone_id
                self.model.zones[self.prev_zone.zone_id]["order"] = [c.card_id for c in self.prev_zone.cards]

    def redo(self):
        self._apply_insert()

    def undo(self):
        self._apply_revert()

class RemoveFromZoneCommand(QUndoCommand):
    def __init__(self, model: BoardModel, card: Card, from_zone: Zone,
                 index: int, to_table_pos: QPointF):
        super().__init__("Remove card from zone")
        self.model = model
        self.card = card
        self.zone = from_zone
        self.index = index
        self.to_table_pos = to_table_pos

    def redo(self):
        if self.card in self.zone.cards:
            self.zone.remove_card(self.card)
        self.model.containers[self.card.card_id] = 'table'
        self.model.zones[self.zone.zone_id]["order"] = [c.card_id for c in self.zone.cards]
        self.card.setPos(self.to_table_pos)
        self.model.cards[self.card.card_id]["pos"] = self.to_table_pos

    def undo(self):
        self.zone.insert_card(self.index, self.card)
        self.model.containers[self.card.card_id] = self.zone.zone_id
        self.model.zones[self.zone.zone_id]["order"] = [c.card_id for c in self.zone.cards]
