from PySide6.QtGui import (
    QUndoStack
)

from PySide6.QtWidgets import (
    QGraphicsScene, QGraphicsView, QRubberBand
)

from PySide6.QtCore import (
    QPoint, QPointF, QRect, Qt
)

from commands.commands import InsertIntoZoneCommand, RemoveFromZoneCommand
from model.board import BoardModel
from zones.zone import Zone
from card.card import Card

class BoardScene(QGraphicsScene):
    def __init__(self, model: BoardModel, undo: QUndoStack):
        super().__init__()
        self.model = model
        self.undo = undo
        self.zones: dict[str, Zone] = {}
        self.hover_zone: Zone | None = None

    def add_zone(self, zone: Zone, scene_pos: QPointF):
        self.addItem(zone)
        zone.setPos(scene_pos)
        self.zones[zone.zone_id] = zone
        self.model.ensure_zone(zone.zone_id)

    def add_card(self, card: Card, scene_pos: QPointF):
        self.addItem(card)
        self.model.ensure_card(card.card_id)
        card.setPos(scene_pos)
        self.model.cards[card.card_id]["pos"] = scene_pos
        self.model.cards[card.card_id]["tapped"] = card.is_tapped()
        self.model.containers[card.card_id] = "table"
        card.moved.connect(lambda pos, c=card: self._on_card_moved(c))

    def _on_card_moved(self, card: Card):
        z = self._hover_zone_for(card)
        if z is not self.hover_zone:
            if self.hover_zone:
                self.hover_zone.highlight = False
                self.hover_zone.update()
            self.hover_zone = z
            if self.hover_zone:
                self.hover_zone.highlight = True
                self.hover_zone.update()

    def _hover_zone_for(self, card: Card) -> Zone | None:
        card_rect = card.mapToScene(card.boundingRect()).boundingRect()
        items = self.items(card_rect, Qt.ItemSelectionMode.IntersectsItemShape)
        for it in items:
            if it is card:
                continue
            if isinstance(it, Zone):
                return it
        return None

    def drop_released(self, card: Card):
        cards = self._selected_cards(card)
        table_positions = {c.card_id: QPointF(c.pos()) for c in cards}
        zone = self._hover_zone_for(card)
        if zone is not None:
            ordered = self._ordered_cards(cards, zone)
            idx = zone.index_at(card.scenePos())
            self._preview_zone_snap(zone, ordered, idx)
            cmd = InsertIntoZoneCommand(self.model, self.zones, ordered, zone, idx, table_pos=table_positions)
            self.undo.push(cmd)
            return

        removals: list[tuple[Card, Zone, int]] = []
        for selected in cards:
            container = self.model.containers.get(selected.card_id, "table")
            if container == 'table':
                continue
            prev_zone = self.zones.get(container)
            if prev_zone is None:
                continue
            prev_index = prev_zone.cards.index(selected) if selected in prev_zone.cards else len(prev_zone.cards)
            removals.append((selected, prev_zone, prev_index))
        if removals:
            cmd = RemoveFromZoneCommand(self.model, removals, table_positions)
            self.undo.push(cmd)

    def _selected_cards(self, primary: Card) -> list[Card]:
        selected = [it for it in self.selectedItems() if isinstance(it, Card)]
        if not selected:
            selected = [primary]
        # Preserve current stacking order while removing duplicates
        unique: list[Card] = []
        seen = set()
        for card in selected:
            if card.card_id in seen:
                continue
            seen.add(card.card_id)
            unique.append(card)
        return unique

    def _ordered_cards(self, cards: list[Card], zone: Zone | None = None) -> list[Card]:
        if zone and zone.orientation == "horizontal":
            return sorted(cards, key=lambda c: (c.scenePos().x(), c.scenePos().y()))
        return sorted(cards, key=lambda c: (c.scenePos().y(), c.scenePos().x()))

    def _preview_zone_snap(self, zone: Zone, cards: list[Card], index: int):
        if not cards:
            return
        insert_at = max(0, min(index, len(zone.cards)))
        original_order = list(zone.cards)
        try:
            working_index = insert_at
            for card in cards:
                if card in zone.cards:
                    existing_idx = zone.cards.index(card)
                    zone.cards.pop(existing_idx)
                    if existing_idx < working_index:
                        working_index -= 1
            insert_at = working_index
            for offset, card in enumerate(cards):
                idx = insert_at + offset
                zone.cards.insert(idx, card)
                if self.model.containers.get(card.card_id, "table") == "table":
                    card.set_tapped(False)
                target = zone.pos_for(idx)
                card.setPos(target)
        finally:
            zone.cards[:] = original_order

class BoardView(QGraphicsView):
    def __init__(self, scene: BoardScene):
        super().__init__(scene)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scale = 1.0
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._rubber_band = QRubberBand(QRubberBand.Rectangle, self.viewport())
        self._rubber_band.hide()
        self._selection_origin: QPoint | None = None
        self._drag_selecting = False

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier:
            if ev.angleDelta().y() > 0:
                factor = 1.1
            else:
                factor = 1/1.1
            new_scale = self._scale * factor
            new_scale = max(0.5, min(2.0, new_scale))
            factor = new_scale / self._scale
            self.scale(factor, factor)
            self._scale = new_scale
        else:
            super().wheelEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            item = self.itemAt(ev.position().toPoint())
            if not isinstance(item, Card):
                self._begin_drag_select(ev)
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_selecting and self._selection_origin is not None:
            self._update_drag_select(ev.position().toPoint())
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._drag_selecting:
            self._end_drag_select()
            return
        super().mouseReleaseEvent(ev)

    def _begin_drag_select(self, ev):
        self._drag_selecting = True
        origin = ev.position().toPoint()
        self._selection_origin = origin
        self.scene().clearSelection()
        self._rubber_band.setGeometry(QRect(origin, origin))
        self._rubber_band.show()

    def _update_drag_select(self, current_pos: QPoint):
        if self._selection_origin is None:
            return
        rect = QRect(self._selection_origin, current_pos).normalized()
        self._rubber_band.setGeometry(rect)
        self._select_cards_in_rect(rect)

    def _end_drag_select(self):
        self._drag_selecting = False
        self._selection_origin = None
        self._rubber_band.hide()

    def _select_cards_in_rect(self, rect: QRect):
        scene_poly = self.mapToScene(rect)
        if not scene_poly:
            self.scene().clearSelection()
            return
        scene_rect = scene_poly.boundingRect()
        self.scene().clearSelection()
        for item in self.scene().items(scene_rect, Qt.ItemSelectionMode.IntersectsItemShape):
            if isinstance(item, Card):
                item.setSelected(True)
