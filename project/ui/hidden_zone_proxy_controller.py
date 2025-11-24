from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QPointF
from PySide6.QtWidgets import QGraphicsScene

from zones.zone import Zone
from card.card import Card
from model.board import BoardModel
from .proxy_card_item import ProxyCardItem


@dataclass
class _ActiveDrag:
    proxy: ProxyCardItem
    card: Card
    offset: QPointF


class HiddenZoneProxyController(QObject):
    """Keeps a proxy scene in sync with a hidden zone and orchestrates drags."""

    def __init__(self, zone: Zone, board_view, board_scene, model: BoardModel):
        super().__init__()
        self.zone = zone
        self.board_view = board_view
        self.board_scene = board_scene
        self.model = model
        self.scene = QGraphicsScene(self)
        self._gap = 20.0
        self._active_drag: _ActiveDrag | None = None
        self._proxies: list[ProxyCardItem] = []

    def proxy_scene(self) -> QGraphicsScene:
        return self.scene

    def refresh(self):
        self.scene.clear()
        self._proxies.clear()
        y = 0.0
        for card in self.zone.cards:
            proxy = ProxyCardItem(card)
            proxy.setPos(QPointF(0.0, y))
            proxy.dragStarted.connect(self._handle_proxy_press)
            proxy.dragMoved.connect(self._handle_proxy_move)
            proxy.dragReleased.connect(self._handle_proxy_release)
            self.scene.addItem(proxy)
            self._proxies.append(proxy)
            y += proxy.boundingRect().height() + self._gap
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20))

    # --- Drag Lifecycle ---
    def _handle_proxy_press(self, proxy: ProxyCardItem, event):
        if self._active_drag is not None:
            return
        card = proxy.card
        if card not in self.zone.cards:
            return
        self.zone.remove_card(card)
        card.set_zone_hidden(False)
        card.set_face_down(False)
        self._update_zone_state()
        scene_pos = self._map_to_board_scene(event.screenPos())
        if scene_pos is None:
            scene_pos = QPointF(card.pos())
        proxy_offset = event.pos()
        card.setPos(scene_pos - proxy_offset)
        self.board_scene.clearSelection()
        card.setSelected(True)
        self._active_drag = _ActiveDrag(proxy=proxy, card=card, offset=proxy_offset)
        proxy.setOpacity(0.25)

    def _handle_proxy_move(self, proxy: ProxyCardItem, event):
        if self._active_drag is None or self._active_drag.proxy is not proxy:
            return
        scene_pos = self._map_to_board_scene(event.screenPos())
        if scene_pos is None:
            return
        card = self._active_drag.card
        offset = self._active_drag.offset
        card.setPos(scene_pos - offset)

    def _handle_proxy_release(self, proxy: ProxyCardItem, event):
        if self._active_drag is None or self._active_drag.proxy is not proxy:
            return
        scene_pos = self._map_to_board_scene(event.screenPos())
        card = self._active_drag.card
        offset = self._active_drag.offset
        if scene_pos is not None:
            card.setPos(scene_pos - offset)
        proxy.setOpacity(1.0)
        self._active_drag = None
        self.board_scene.drop_released(card)
        self.refresh()

    def _map_to_board_scene(self, screen_pos) -> QPointF | None:
        if self.board_view is None:
            return None
        if hasattr(screen_pos, "toPoint"):
            global_point = screen_pos.toPoint()
        else:
            global_point = screen_pos
        viewport_point = self.board_view.viewport().mapFromGlobal(global_point)
        return self.board_view.mapToScene(viewport_point)

    def _update_zone_state(self):
        self.model.zones.setdefault(self.zone.zone_id, {})
        self.model.zones[self.zone.zone_id]["order"] = [c.card_id for c in self.zone.cards]
        for card in self.zone.cards:
            self.model.cards.setdefault(card.card_id, {})
            self.model.cards[card.card_id]["pos"] = card.pos()
