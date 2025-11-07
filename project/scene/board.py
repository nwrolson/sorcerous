from PySide6.QtGui import (
    QUndoStack
)

from PySide6.QtWidgets import (
    QGraphicsScene, QGraphicsView
)

from PySide6.QtCore import (
    QPointF, Qt
)

from commands.commands import InsertIntoZoneCommand,RemoveFromZoneCommand
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
        z = self._hover_zone_for(card)
        in_container = self.model.containers[card.card_id]
        if z is not None:
            idx = z.index_at(card.scenePos())
            cmd = InsertIntoZoneCommand(self.model, self.zones, card, z, idx, table_pos=card.pos())
            self.undo.push(cmd)
        else:
            if in_container != 'table':
                prev_zone = self.zones[in_container]
                idx = prev_zone.cards.index(card)
                cmd = RemoveFromZoneCommand(self.model, card, prev_zone, idx, to_table_pos=card.pos())
                self.undo.push(cmd)
            else:
                pass

class BoardView(QGraphicsView):
    def __init__(self, scene: BoardScene):
        super().__init__(scene)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scale = 1.0
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

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
