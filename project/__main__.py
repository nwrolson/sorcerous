import queue
import numpy as np
import sys
import time
import statistics

from PySide6.QtCore import Qt, QPointF, QTimer, QRectF, QPoint, Slot
from PySide6.QtGui import QImage, QUndoStack, QPainter, QCursor
from PySide6.QtWidgets import QApplication, QMainWindow, QFrame, QGraphicsView

from scene.board import BoardScene, BoardView
from model.board import BoardModel
from commands.commands import InsertIntoZoneCommand
from camera.camera import VirtualCamThread
from zones.hand import HandZone
from zones.library import LibraryZone
from zones.preview import PreviewZone
from card.card import Card
from ui.deck_view import ListViewWidget
from ui.load_menu import LoadMenu
from ui.loading_spinner import LoadingSpinner
from cache.cache import ScryfallImageCache
from spawner.spawner import CardSpawner

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


# -------- Main Window --------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sorcerous")
        self.resize(1920, 1080)

        self.model = BoardModel()
        self.undo = QUndoStack(self)
        self.scene = BoardScene(self.model, self.undo,
                                on_manual_drag=lambda card: self._on_card_manual_drag(card))
        self.view = BoardView(self.scene)
        self.setCentralWidget(self.view)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setViewportMargins(0, 0, 0, 0)
        self.view.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.view.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.view.setFocus()

        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Keyboard Shortcuts
        self.view.register_shortcut(Qt.Key_T, lambda ev: self._handle_tap_shortcut())
        self.view.register_shortcut(Qt.Key_Q, lambda ev: self._handle_stack_shortcut(ev))
        self.view.register_shortcut(Qt.Key_D, lambda ev: self._handle_draw_shortcut())
        self.view.register_shortcut(Qt.Key_L, lambda ev: self._toggle_deck_view())
        self.view.register_shortcut(Qt.Key_X, self._handle_delete_shortcut)
        self.view.register_shortcut(Qt.Key_Escape, self._handle_escape_shortcut)

        self.load_menu = LoadMenu(self)
        self.load_menu.raise_()
        self.deck_view_widget = ListViewWidget(parent=self)
        self.deck_view_widget.closeRequested.connect(self._hide_deck_view)
        self.deck_view_widget.dragMoved.connect(self._on_deck_view_moved)
        self.deck_view_widget.cardSelected.connect(self._handle_deck_selection)
        self._deck_view_user_pos: QPoint | None = None
        self.deck_view_widget.hide()
        spinner_icon = BASE_DIR / "resources" / "brass-eye.svg"
        self.loading_spinner = LoadingSpinner(spinner_icon, self)
        self.loading_spinner.hide()
        self.undo.indexChanged.connect(self._refresh_deck_view)

        # Freeze window size after initial layout
        QTimer.singleShot(0, self._sync_scene_rect_to_viewport)
        QTimer.singleShot(0, self._position_load_menu)
        QTimer.singleShot(0, self._position_loading_spinner)
        QTimer.singleShot(0, self._position_deck_view)

        # Virtual cam setup
        self.frame_queue: queue.Queue = queue.Queue(maxsize=1)
        self.vcam_thread = VirtualCamThread(self.frame_queue)
        self.vcam_thread.start()
        self.streaming_enabled = True

        self._last_capture_time = 0.0
        self._capture_interval = 1.0 / 60.0  # seconds

        # Populate items
        self.hand_zone = HandZone("hand")
        self.library_zone = LibraryZone("library")
        self.preview_zone = PreviewZone("preview", width=260, slot_h=360)
        self.scene.add_zone(self.hand_zone, QPointF(0, 0))
        self.scene.add_zone(self.library_zone, QPointF(0, 0))
        self.scene.add_zone(self.preview_zone, QPointF(0, 0))
        self.preview_zone.setVisible(False)

        cache_root = BASE_DIR / "scryfall-cache"
        self.card_cache = ScryfallImageCache(
            root_dir=str(cache_root),
            memory_items=512,
            user_agent="VirtualCardPlayer/1.0",
        )
        self.spawner = CardSpawner(
            self.card_cache,
            parent=self,
            on_cards_spawned=self._handle_spawned_cards,
        )
        self.spawner.spawnFailed.connect(self._handle_spawn_failure)
        self.spawner.spawnCompleted.connect(
            lambda *_: self.loading_spinner.finish()
        )
        self.load_menu.importRequested.connect(self._on_import_requested)

        self._register_existing_cards()
        self._refresh_deck_view()
        self._last_stack_cycle_ids: list[str] = []
        self._stack_selection_key: tuple[str, ...] | None = None
        self._stack_anchor_point: QPointF | None = None
        self._preview_state: dict | None = None

    def _qimage_to_rgb(self, img: QImage) -> np.ndarray:
        # Ensure RGBA8888
        if img.format() != QImage.Format.Format_RGBA8888:
            img = img.convertToFormat(QImage.Format.Format_RGBA8888)

        h, w = img.height(), img.width()
        expected = w * h * 4

        ptr = img.bits()  # PySide6: memoryview; older PySide: sip.voidptr

        # Build array without copying, then copy to own buffer
        arr = np.frombuffer(ptr, dtype=np.uint8, count=expected).reshape((h, w, 4))
        return arr[:, :, :3].copy(order="C")  # RGB for pyvirtualcam

    def _render_scene_for_camera(self, out_w: int, out_h: int) -> np.ndarray:
        img = QImage(out_w, out_h, QImage.Format.Format_RGBA8888)
        img.setDevicePixelRatio(1.0)
        img.fill(Qt.black)

        # Visible area of the view in scene coords
        view_src = self.view.mapToScene(self.view.viewport().rect()).boundingRect()

        ta = out_w / out_h            # target aspect
        sa = view_src.width() / view_src.height()  # source aspect
        src = view_src
        if sa > ta:
            # Wider than camera -> crop width (center horizontally is fine)
            new_w = view_src.height() * ta
            x = view_src.center().x() - new_w / 2.0
            src = QRectF(x, view_src.top(), new_w, view_src.height())
        elif sa < ta:
            # Taller than camera -> crop height BUT anchor to bottom
            new_h = view_src.width() / ta
            y = view_src.bottom() - new_h      # align bottoms
            src = QRectF(view_src.left(), y, view_src.width(), new_h)

        p = QPainter(img)
        prev = Card.render_target
        Card.render_target = "camera"
        try:
            self.scene.render(p, QRectF(0, 0, out_w, out_h), src)
        finally:
            Card.render_target = prev
            p.end()
        return self._qimage_to_rgb(img)

    def capture_and_queue_frame(self):
        target_w = self.vcam_thread.width
        target_h = self.vcam_thread.height
        rgb = self._render_scene_for_camera(target_w, target_h)
        try:
            self.frame_queue.put_nowait(rgb)
        except queue.Full:
            pass


    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._sync_scene_rect_to_viewport()
        self._position_load_menu()
        self._position_loading_spinner()
        self._position_deck_view()

    def _sync_scene_rect_to_viewport(self):
        view_src = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        margin = 12.0
        self.scene.setSceneRect(view_src.adjusted(-margin, -margin, margin, margin))
        self._layout_zones()

    def _layout_zones(self):
        if not hasattr(self, "hand_zone") or not hasattr(self, "library_zone"):
            return
        view_rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        if view_rect.isNull() or view_rect.width() <= 0:
            return
        self.hand_zone.layout_to_view(view_rect)
        self.library_zone.layout_to_view(view_rect, self.hand_zone)

    def _position_load_menu(self):
        if not hasattr(self, "load_menu") or self.load_menu is None:
            return
        self.load_menu.adjustSize()
        size = self.load_menu.size()
        center = self.rect().center()
        if hasattr(self.load_menu, "primary_center_offset"):
            y_offset = self.load_menu.primary_center_offset()
        else:
            y_offset = size.height() / 2
        top_left = QPoint(
            max(0, center.x() - size.width() // 2),
            max(0, center.y() - int(y_offset)),
        )
        self.load_menu.move(top_left)

    def _position_loading_spinner(self):
        if not hasattr(self, "loading_spinner") or self.loading_spinner is None:
            return
        size = self.loading_spinner.size()
        if size.isEmpty():
            return
        center = self.rect().center()
        top_left = QPoint(
            max(0, center.x() - size.width() // 2),
            max(0, center.y() - size.height() // 2),
        )
        self.loading_spinner.move(top_left)
        if self.loading_spinner.isVisible():
            self.loading_spinner.raise_()

    def _position_deck_view(self):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        size = self.deck_view_widget.size()
        if self._deck_view_user_pos is None:
            center = self.rect().center()
            top_left = QPoint(
                max(0, center.x() - size.width() // 2),
                max(0, center.y() - size.height() // 2),
            )
        else:
            top_left = self._clamp_point_to_window(self._deck_view_user_pos, size)
        self.deck_view_widget.move(top_left)
        if self.deck_view_widget.isVisible():
            self.deck_view_widget.raise_()
        self._position_preview_zone()

    def _clamp_point_to_window(self, pos: QPoint, size) -> QPoint:
        max_x = max(0, self.width() - size.width())
        max_y = max(0, self.height() - size.height())
        clamped_x = max(0, min(pos.x(), max_x))
        clamped_y = max(0, min(pos.y(), max_y))
        return QPoint(clamped_x, clamped_y)
    
    def _position_preview_zone(self):
        if not hasattr(self, "preview_zone") or self.preview_zone is None:
            return
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        if not self.deck_view_widget.isVisible():
            self.preview_zone.setVisible(False)
            return
        deck_global = self.deck_view_widget.mapToGlobal(self.deck_view_widget.rect().topLeft())
        viewport_pos = self.view.viewport().mapFromGlobal(deck_global)
        scene_pos = self.view.mapToScene(viewport_pos)
        margin = 16
        zone_pos = scene_pos + QPointF(self.deck_view_widget.width() + margin, 0)
        self.preview_zone.setPos(zone_pos)
        self.preview_zone.setVisible(True)
        self.preview_zone.set_height(max(self.deck_view_widget.height(), 200))
        self.preview_zone.reflow_cards()

    def _refresh_deck_view(self, *_):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        self.deck_view_widget.set_cards(self._library_card_entries())
        self._sync_preview_state()

    def _library_card_entries(self) -> list[dict[str, object]]:
        library = getattr(self, "library_zone", None)
        if library is None or not hasattr(library, "cards"):
            return []
        entries: list[dict[str, object]] = []
        for card in library.cards:
            data = getattr(card, "card_data", None) or {}
            display_name = data.get("name") or card.card_id
            entries.append(
                {
                    "card_id": getattr(card, "id", -1),
                    "name": display_name,
                    "thumbnail": getattr(card, "thumbnail", None),
                    "mana_costs": self._extract_mana_costs(data),
                }
            )
        return entries

    def _extract_mana_costs(self, data: dict | None) -> list[str]:
        """Collect every available mana_cost string for the card, preserving order."""
        if not isinstance(data, dict):
            return []
        costs: list[str] = []
        primary = (data.get("mana_cost") or "").strip()
        if primary:
            costs.append(primary)
        faces = data.get("card_faces")
        if isinstance(faces, list):
            for face in faces:
                mana = (face.get("mana_cost") or "").strip() if isinstance(face, dict) else ""
                if mana:
                    costs.append(mana)
        return costs

    def _handle_deck_selection(self, card_numeric_id: int):
        if not getattr(self, "deck_view_widget", None) or not self.deck_view_widget.isVisible():
            return
        card = self.spawner.get_card(card_numeric_id)
        if card is None:
            return
        self._show_preview_card(card)

    def _show_preview_card(self, card: Card):
        if getattr(self, "_preview_state", None):
            prev_card = self._preview_state.get("card")
            if prev_card is card:
                return
            self._clear_preview(return_to_library=True)
        library = getattr(self, "library_zone", None)
        preview = getattr(self, "preview_zone", None)
        if library is None or preview is None:
            return
        if card not in library.cards:
            return
        index = library.cards.index(card)
        library.remove_card(card)
        # Keep card hidden until preview zone repositions it.
        card.set_zone_hidden(True)
        card.set_face_down(False)
        preview.insert_card(0, card)
        card.set_zone_hidden(False)
        self.scene.model.containers[card.card_id] = preview.zone_id
        self._update_zone_state(library)
        self._update_zone_state(preview)
        self._preview_state = {
            "card": card,
            "index": index,
        }
        self._position_preview_zone()

    def _clear_preview(self, return_to_library: bool):
        if not getattr(self, "_preview_state", None):
            return
        card = self._preview_state.get("card")
        preview = getattr(self, "preview_zone", None)
        library = getattr(self, "library_zone", None)
        in_preview = preview is not None and card in preview.cards
        if preview and in_preview:
            preview.remove_card(card)
            self._update_zone_state(preview)
        if (
            return_to_library
            and in_preview
            and library is not None
            and card is not None
        ):
            insert_at = min(self._preview_state.get("index", 0), len(library.cards))
            library.insert_card(insert_at, card)
            self.scene.model.containers[card.card_id] = library.zone_id
            self._update_zone_state(library)
        self._preview_state = None

    def _sync_preview_state(self):
        if not getattr(self, "_preview_state", None):
            return
        card = self._preview_state.get("card")
        if card is None:
            self._preview_state = None
            return
        container = self.scene.model.containers.get(card.card_id)
        if container != getattr(self.preview_zone, "zone_id", "preview"):
            self._preview_state = None

    def _update_zone_state(self, zone):
        if zone is None:
            return
        self.scene.model.zones.setdefault(zone.zone_id, {})
        self.scene.model.zones[zone.zone_id]["order"] = [c.card_id for c in zone.cards]
        for card in zone.cards:
            self.scene.model.cards.setdefault(card.card_id, {})
            self.scene.model.cards[card.card_id]["pos"] = card.pos()

    def _toggle_deck_view(self):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        if self.deck_view_widget.isVisible():
            self._hide_deck_view()
            return
        self._position_deck_view()
        self._refresh_deck_view()
        self.deck_view_widget.show()
        self.deck_view_widget.raise_()
        self._position_preview_zone()

    def _handle_escape_shortcut(self, ev=None):
        load_menu = getattr(self, "load_menu", None)
        if load_menu is None:
            return
        load_menu.show_import_menu()
        self._position_load_menu()

    def _handle_delete_shortcut(self, ev=None):
        scene = getattr(self, "scene", None)
        if scene is None:
            return
        selected_cards = [item for item in scene.selectedItems() if isinstance(item, Card)]
        if not selected_cards:
            hover_card = getattr(scene, "hover_card", None)
            if isinstance(hover_card, Card):
                selected_cards.append(hover_card)
        if not selected_cards:
            return
        self._destroy_cards(selected_cards)

    def _destroy_cards(self, cards: list[Card]):
        scene = getattr(self, "scene", None)
        if scene is None or not cards:
            return

        unique: list[Card] = []
        seen_ids: set[int] = set()
        for card in cards:
            if not isinstance(card, Card):
                continue
            key = id(card)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            unique.append(card)
        if not unique:
            return

        preview_card = None
        if getattr(self, "_preview_state", None):
            preview_card = self._preview_state.get("card")

        for card in unique:
            if preview_card is card:
                self._clear_preview(return_to_library=False)
                preview_card = None

            container = scene.model.containers.get(card.card_id, "table")
            zone = scene.zones.get(container) if container != "table" else None
            if zone and card in zone.cards:
                zone.remove_card(card)
                self._update_zone_state(zone)

            if getattr(scene, "hover_card", None) is card:
                scene.hover_card = None

            card.setSelected(False)
            active_scene = card.scene()
            if active_scene is not None:
                active_scene.removeItem(card)
            card.deleteLater()

            scene.model.cards.pop(card.card_id, None)
            scene.model.containers.pop(card.card_id, None)

            numeric_id = getattr(card, "id", None)
            if isinstance(numeric_id, int):
                self.spawner.handle_destroy_card(numeric_id)

        self._refresh_deck_view()
        self._on_card_action()

    def _hide_deck_view(self):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        self.deck_view_widget.hide()
        self._clear_preview(return_to_library=True)
        if hasattr(self, "preview_zone") and self.preview_zone:
            self.preview_zone.setVisible(False)

    def _on_deck_view_moved(self, pos: QPoint):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        size = self.deck_view_widget.size()
        clamped = self._clamp_point_to_window(pos, size)
        if clamped != pos:
            self.deck_view_widget.move(clamped)
        self._deck_view_user_pos = clamped
        self._position_preview_zone()

    def _wrap_release(self, original_release, card: Card):
        def handler(ev):
            original_release(ev)
            dragged = card.consume_user_drag() if hasattr(card, "consume_user_drag") else False
            if ev.button() == Qt.LeftButton:
                self.scene.drop_released(card)
                if dragged:
                    self._clear_stack_anchor()
                self._on_card_action()
        return handler

    def _handle_tap_shortcut(self):
        selected_cards = [
            it for it in self.scene.selectedItems()
            if isinstance(it, Card)
        ]
        hover_card = getattr(self.scene, "hover_card", None)
        if hover_card and hover_card not in selected_cards:
            selected_cards.append(hover_card)
        table_cards = [
            card for card in selected_cards
            if self.scene.model.containers.get(card.card_id, "table") == "table"
        ]
        if not table_cards:
            return
        tapped_states = [card.is_tapped() for card in table_cards]
        all_tapped = all(tapped_states)
        any_tapped = any(tapped_states)
        if all_tapped or not any_tapped:
            new_state = not all_tapped
        else:
            new_state = True
        for card in table_cards:
            card.set_tapped(new_state)
        self._on_card_action()

    def _handle_stack_shortcut(self, ev=None):
        selected_cards = [
            it for it in self.scene.selectedItems()
            if isinstance(it, Card)
        ]
        table_cards = [
            card for card in selected_cards
            if self.scene.model.containers.get(card.card_id, "table") == "table"
        ]
        if len(table_cards) < 2:
            return

        ordered_cards = sorted(
            table_cards,
            key=lambda c: (round(c.scenePos().y(), 2), round(c.scenePos().x(), 2))
        )
        selection_ids = [card.card_id for card in ordered_cards]
        selection_key = tuple(sorted(selection_ids))

        reuse_anchor = (
            selection_key == self._stack_selection_key
            and bool(self._last_stack_cycle_ids)
            and self._stack_anchor_point is not None
        )

        if reuse_anchor:
            rotated = self._last_stack_cycle_ids[1:] + self._last_stack_cycle_ids[:1]
        else:
            rotated = selection_ids
            self._stack_selection_key = selection_key
            self._stack_anchor_point = self._selection_median_point(ordered_cards)
        self._last_stack_cycle_ids = rotated[:]

        id_to_card = {card.card_id: card for card in table_cards}
        ordered_cards = [id_to_card[card_id] for card_id in rotated if card_id in id_to_card]
        if len(ordered_cards) < 2:
            return

        first = ordered_cards[0]
        card_rect = first.boundingRect()
        anchor_center = self._stack_anchor_point or self._selection_median_point(ordered_cards)
        anchor = QPointF(
            anchor_center.x() - card_rect.width() / 2.0,
            anchor_center.y() - card_rect.height() / 2.0
        )
        spread = QPointF(card_rect.width() * 0.2, card_rect.height() * 0.15)

        for idx, card in enumerate(ordered_cards):
            pos = anchor + QPointF(spread.x() * idx, spread.y() * idx)
            card.setPos(pos)
            card.setZValue(5 + idx)
            self.scene.model.cards[card.card_id]["pos"] = pos
        self._reanchor_active_drag(self._cursor_scene_pos())
        self._on_card_action()

    def _handle_draw_shortcut(self):
        library = getattr(self, "library_zone", None)
        hand = getattr(self, "hand_zone", None)
        if library is None or hand is None:
            return
        if not library.cards:
            return
        card = library.cards[-1]
        insert_at = len(hand.cards)
        table_positions = {card.card_id: QPointF(card.pos())}
        cmd = InsertIntoZoneCommand(
            self.model,
            self.scene.zones,
            [card],
            hand,
            insert_at,
            table_pos=table_positions,
        )
        self.undo.push(cmd)
        self._on_card_action()

    def _cursor_scene_pos(self) -> QPointF | None:
        cursor_global = QCursor.pos()
        view_point = self.view.mapFromGlobal(cursor_global)
        if not self.view.rect().contains(view_point):
            return None
        return self.view.mapToScene(view_point)

    def _reanchor_active_drag(self, cursor_scene: QPointF | None):
        grabber = self.scene.mouseGrabberItem()
        if isinstance(grabber, Card):
            grabber.reset_user_drag_state()
            if cursor_scene is not None:
                grabber.reanchor_drag(cursor_scene)

    def _on_card_manual_drag(self, card: Card):
        self._clear_stack_anchor()

    def _clear_stack_anchor(self):
        self._stack_selection_key = None
        self._stack_anchor_point = None
        self._last_stack_cycle_ids.clear()

    def _selection_median_point(self, cards: list[Card]) -> QPointF:
        xs = sorted(card.scenePos().x() for card in cards)
        if not xs:
            return QPointF(0, 0)
        median_x = float(statistics.median(xs))
        bottom_y = max(card.scenePos().y() for card in cards)
        return QPointF(median_x, float(bottom_y))

    def _register_existing_cards(self):
        for item in self.scene.items():
            if isinstance(item, Card):
                self._register_card_item(item)

    def _register_card_item(self, card: Card):
        if getattr(card, "_sorcerous_card_registered", False):
            return
        card._sorcerous_card_registered = True
        orig = card.mouseReleaseEvent
        card.mouseReleaseEvent = self._wrap_release(orig, card)
        card.moved.connect(lambda pos, c=card: self._on_card_action())

    @Slot(str, str)
    def _on_import_requested(self, payload: str, target_zone: str) -> None:
        accepted = self.spawner.handle_import_signal(payload, target_zone)
        if accepted:
            self._position_loading_spinner()
            self.loading_spinner.start()
        else:
            self.loading_spinner.finish(immediate=True)

    def _handle_spawned_cards(self, cards: list[Card], target_zone: str) -> None:
        if not cards:
            return
        placed_in_zone = self._place_cards_in_zone(cards, target_zone)
        if not placed_in_zone:
            self._layout_cards_on_table(cards)
        self._on_card_action()

    def _place_cards_in_zone(self, cards: list[Card], zone_id: str) -> bool:
        if not zone_id or zone_id == "table":
            return False
        zone = self.scene.zones.get(zone_id)
        if zone is None:
            return False

        for card in cards:
            # Add to scene so zone can take ownership and hide/reflow as needed.
            self.scene.add_card(card, zone.pos())
            self._register_card_item(card)
            insert_at = len(zone.cards)
            zone.insert_card(insert_at, card)
            self.scene.model.containers[card.card_id] = zone.zone_id

        zone_order = [c.card_id for c in zone.cards]
        self.scene.model.zones[zone.zone_id]["order"] = zone_order
        for card in zone.cards:
            self.scene.model.cards[card.card_id]["pos"] = card.pos()
        if zone.zone_id == "library":
            self._refresh_deck_view()
        return True

    def _layout_cards_on_table(self, cards: list[Card]) -> None:
        if not cards:
            return
        cols = 5
        spacing = QPointF(150, 210)
        start = QPointF(40, 40)
        existing_cards = sum(
            1 for container in self.scene.model.containers.values() if container == "table"
        )
        for offset, card in enumerate(cards):
            idx = existing_cards + offset
            col = idx % cols
            row = idx // cols
            pos = QPointF(
                start.x() + col * spacing.x(),
                start.y() + row * spacing.y(),
            )
            self.scene.add_card(card, pos)
            self._register_card_item(card)

    def _handle_spawn_failure(self, message: str) -> None:
        self.loading_spinner.finish(immediate=True)
        print(f"[MainWindow] Card spawn failed: {message}")
        if self.load_menu:
            self.load_menu.show()

    def _on_card_action(self):
        if not self.streaming_enabled:
            return
        now = time.time()
        if now - self._last_capture_time < self._capture_interval:
            return
        self._last_capture_time = now
        self.capture_and_queue_frame()

    def set_stream_params(self, width: int, height: int, fps: float):
        self.vcam_thread.update_params(width, height, fps)

    def closeEvent(self, ev):
        self.vcam_thread.stop()
        self.vcam_thread.join(timeout=2.0)
        super().closeEvent(ev)

# -------- Entry Point --------
if __name__ == "__main__":
    print("Hello World!")
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
