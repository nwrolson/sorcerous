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
    def __init__(self, model: BoardModel, zones: dict[str, Zone], cards: list[Card],
                 zone: Zone, index: int, table_pos: dict[str, QPointF]):
        title = "Insert cards into zone" if len(cards) > 1 else "Insert card into zone"
        super().__init__(title)
        self.model = model
        self.zones = zones
        self.cards = list(dict.fromkeys(cards))
        self.zone = zone
        self.index = max(0, index)
        self.table_pos = {card_id: QPointF(pos) for card_id, pos in table_pos.items()}
        self._prev_states: list[dict] = []
        for card in self.cards:
            container = model.containers.get(card.card_id, "table")
            prev_zone = zones.get(container) if container != "table" else None
            prev_index = None
            if prev_zone and card in prev_zone.cards:
                prev_index = prev_zone.cards.index(card)
            self._prev_states.append({
                "card": card,
                "container": container,
                "zone": prev_zone,
                "index": prev_index,
            })

    def _update_zone_order(self, zone: Zone):
        self.model.zones[zone.zone_id]["order"] = [c.card_id for c in zone.cards]

    def _record_zone_positions(self, zone: Zone):
        for card in zone.cards:
            self.model.cards[card.card_id]["pos"] = card.pos()

    def _compute_insert_index(self) -> int:
        adjustment = sum(
            1
            for state in self._prev_states
            if state["zone"] is self.zone
            and state["index"] is not None
            and state["index"] < self.index
        )
        return self.index - adjustment

    def _detach_from_previous_zones(self):
        for state in self._prev_states:
            prev_zone: Zone | None = state["zone"]
            card: Card = state["card"]
            if prev_zone and card in prev_zone.cards:
                prev_zone.remove_card(card)
                self._update_zone_order(prev_zone)
                self._record_zone_positions(prev_zone)

    def _apply_insert(self):
        insert_at = self._compute_insert_index()
        self._detach_from_previous_zones()
        insert_at = max(0, min(insert_at, len(self.zone.cards)))
        for offset, card in enumerate(self.cards):
            self.zone.insert_card(insert_at + offset, card)
            self.model.containers[card.card_id] = self.zone.zone_id
        self._update_zone_order(self.zone)
        self._record_zone_positions(self.zone)

    def _apply_revert(self):
        for card in reversed(self.cards):
            if card in self.zone.cards:
                self.zone.remove_card(card)
        self._update_zone_order(self.zone)
        self._record_zone_positions(self.zone)

        zone_groups: dict[str, dict] = {}
        for state in self._prev_states:
            prev_zone: Zone | None = state["zone"]
            if prev_zone is None or state["container"] == "table":
                continue
            prev_index = state["index"] if state["index"] is not None else len(prev_zone.cards)
            data = zone_groups.setdefault(prev_zone.zone_id, {"zone": prev_zone, "entries": []})
            data["entries"].append((prev_index, state["card"]))

        for data in zone_groups.values():
            zone: Zone = data["zone"]
            for idx, card in sorted(data["entries"], key=lambda pair: pair[0]):
                zone.insert_card(idx, card)
                self.model.containers[card.card_id] = zone.zone_id
            self._update_zone_order(zone)
            self._record_zone_positions(zone)

        for state in self._prev_states:
            if state["container"] != "table":
                continue
            card: Card = state["card"]
            pos = QPointF(self.table_pos.get(card.card_id, card.pos()))
            card.setPos(pos)
            self.model.cards[card.card_id]["pos"] = pos
            self.model.containers[card.card_id] = "table"

    def redo(self):
        self._apply_insert()

    def undo(self):
        self._apply_revert()

class RemoveFromZoneCommand(QUndoCommand):
    def __init__(self, model: BoardModel,
                 removals: list[tuple[Card, Zone, int]],
                 to_table_pos: dict[str, QPointF]):
        title = "Remove cards from zone" if len(removals) > 1 else "Remove card from zone"
        super().__init__(title)
        self.model = model
        self.removals = [
            {
                "card": card,
                "zone": zone,
                "index": index
            } for (card, zone, index) in removals
        ]
        self.to_table_pos = {card_id: QPointF(pos) for card_id, pos in to_table_pos.items()}

    def _update_zone_order(self, zone: Zone):
        self.model.zones[zone.zone_id]["order"] = [c.card_id for c in zone.cards]
        for card in zone.cards:
            self.model.cards[card.card_id]["pos"] = card.pos()

    def redo(self):
        affected: dict[str, Zone] = {}
        for entry in self.removals:
            card: Card = entry["card"]
            zone: Zone = entry["zone"]
            if card in zone.cards:
                zone.remove_card(card)
                affected[zone.zone_id] = zone
            pos = QPointF(self.to_table_pos.get(card.card_id, card.pos()))
            card.setPos(pos)
            self.model.cards[card.card_id]["pos"] = pos
            self.model.containers[card.card_id] = 'table'
        for zone in affected.values():
            self._update_zone_order(zone)

    def undo(self):
        zone_groups: dict[str, dict] = {}
        for entry in self.removals:
            zone: Zone = entry["zone"]
            idx = entry["index"] if entry["index"] is not None else len(zone.cards)
            data = zone_groups.setdefault(zone.zone_id, {"zone": zone, "entries": []})
            data["entries"].append((idx, entry["card"]))

        for data in zone_groups.values():
            zone: Zone = data["zone"]
            for idx, card in sorted(data["entries"], key=lambda pair: pair[0]):
                zone.insert_card(idx, card)
                self.model.containers[card.card_id] = zone.zone_id
            self._update_zone_order(zone)
