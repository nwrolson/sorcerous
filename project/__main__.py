import queue
import random
import numpy as np
import sys
import time
import statistics
from weakref import WeakSet

from PySide6.QtCore import Qt, QPointF, QTimer, QRectF, QPoint, Slot, QThread, QEvent
from PySide6.QtGui import QImage, QUndoStack, QPainter, QCursor
from PySide6.QtWidgets import QApplication, QMainWindow, QFrame, QGraphicsView

from scene.board import BoardScene, BoardView
from model.board import BoardModel
from commands.commands import InsertIntoZoneCommand
from camera.camera import VirtualCamThread
from zones.hand import HandZone
from zones.library import LibraryZone
from zones.preview import PreviewZone
from zones.graveyard import GraveyardZone
from zones.zone import Zone
from card.card import Card
from ui.deck_view import ListViewWidget
from ui.zone_viewer import ZoneViewerMenu
from ui.hidden_zone_proxy_controller import HiddenZoneProxyController
from ui.load_menu import LoadMenu
from ui.loading_spinner import LoadingSpinner
from ui.context_menu import CardContextMenu, TableContextMenu, ScryfallSearchMenu
from loader.loader import DeckLoader, DeckLoaderError
from _scryfall_search_worker import _ScryfallSearchWorker
from ui.icon_bar import IconBar
from ui.token_spawn_menu import TokenSpawnMenu
from cache.cache import ScryfallImageCache
from spawner.spawner import CardSpawner
from spawner.token_descriptor import TokenDescriptor

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


# -------- Main Window --------
class MainWindow(QMainWindow):
    TOKEN_CATEGORIES = ("common", "deck", "mechanics", "dungeon", "custom")

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
        self.view.register_shortcut(Qt.Key_Z, lambda ev: self._handle_duplicate_shortcut())
        self.view.register_shortcut(Qt.Key_D, lambda ev: self._handle_draw_shortcut())
        self.view.register_shortcut(Qt.Key_S, lambda ev: self._handle_shuffle_shortcut())
        self.view.register_shortcut(
            Qt.Key_S,
            lambda ev: self._handle_ctrl_s_shortcut(),
            modifiers=Qt.ControlModifier,
        )
        self.view.register_shortcut(Qt.Key_L, lambda ev: self._toggle_deck_view())
        self.view.register_shortcut(Qt.Key_V, lambda ev: self._toggle_zone_viewer())
        self.view.register_shortcut(Qt.Key_G, lambda ev: self._handle_graveyard_shortcut())
        self.view.register_shortcut(Qt.Key_X, self._handle_delete_shortcut)
        self.view.register_shortcut(Qt.Key_Escape, self._handle_escape_shortcut)
        self.view.register_shortcut(Qt.Key_M, lambda ev: self._toggle_icon_bar())
        self.view.cardContextRequested.connect(self._show_card_context_menu)
        self.view.tableContextRequested.connect(self._show_table_context_menu)

        self.load_menu = LoadMenu(self)
        self.load_menu.raise_()
        self.deck_view_widget = ListViewWidget(parent=self)
        self.deck_view_widget.closeRequested.connect(self._hide_deck_view)
        self.deck_view_widget.dragMoved.connect(self._on_deck_view_moved)
        self.deck_view_widget.cardSelected.connect(self._handle_deck_selection)
        self._deck_view_user_pos: QPoint | None = None
        self.deck_view_widget.hide()
        self.zone_viewer = ZoneViewerMenu(parent=self)
        self.zone_viewer.closeRequested.connect(self._hide_zone_viewer)
        self.zone_viewer.dragMoved.connect(self._on_zone_viewer_moved)
        self.zone_viewer.zoneChanged.connect(self._handle_zone_viewer_zone_changed)
        self._zone_viewer_user_pos: QPoint | None = None
        self.zone_viewer.hide()
        spinner_icon = BASE_DIR / "resources" / "brass-eye.svg"
        self.loading_spinner = self._create_spinner(spinner_icon)
        self.search_loading_spinner = self._create_spinner(spinner_icon)
        self.deck_loader = DeckLoader()
        self.scryfall_search_menu = ScryfallSearchMenu(
            self,
            on_submit=self._handle_scryfall_search,
            on_result_click=self._handle_scryfall_result_click,
        )
        self.icon_bar = IconBar(self, icon_size=100)
        self.table_context_menu = TableContextMenu(self, search_menu=self.scryfall_search_menu)
        self.undo.indexChanged.connect(self._refresh_deck_view)
        self.undo.indexChanged.connect(self._refresh_zone_viewer)
        self.installEventFilter(self)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        self._configure_icon_bar()

        # Freeze window size after initial layout
        QTimer.singleShot(0, self._sync_scene_rect_to_viewport)
        QTimer.singleShot(0, self._position_load_menu)
        QTimer.singleShot(0, self._position_loading_spinner)
        QTimer.singleShot(0, self._position_deck_view)
        QTimer.singleShot(0, self._position_zone_viewer)
        QTimer.singleShot(0, self._position_token_menu)

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
        self.graveyard_zone = GraveyardZone("graveyard")
        self.preview_zone = PreviewZone("preview", width=260, slot_h=360)
        self.scene.add_zone(self.hand_zone, QPointF(0, 0))
        self.scene.add_zone(self.library_zone, QPointF(0, 0))
        self.scene.add_zone(self.graveyard_zone, QPointF(0, 0))
        self.scene.add_zone(self.preview_zone, QPointF(0, 0))
        self.graveyard_zone.move_offscreen()
        self.hidden_zone_proxy = HiddenZoneProxyController(self.graveyard_zone, self.view, self.scene, self.model)
        self.zone_viewer.set_scene(self.hidden_zone_proxy.proxy_scene())
        self.zone_viewer.set_column_width(int(self.graveyard_zone.width + 48))
        self.preview_zone.setVisible(False)
        self.deck_view_widget.set_preview_sources(self.scene, self.preview_zone)
        self.deck_view_widget.previewAreaChanged.connect(self._position_preview_zone)
        self.zone_viewer.set_available_zones([self.graveyard_zone])
        self.zone_viewer.show_zone(self.graveyard_zone.zone_id)

        # Shared cache root for cards and tokens.
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
        self.card_context_menu = CardContextMenu(
            self,
            cache=self.card_cache,
            spawner=self.spawner,
            board_scene=self.scene,
            board_view=self.view,
        )

        self.token_menu = TokenSpawnMenu(
            self.view,
            self.scene,
            self.spawner,
            on_card_created=self._register_card_item,
            parent=self,
        )
        self.token_menu.closeRequested.connect(self._hide_token_menu)
        self.token_menu.dragMoved.connect(self._on_token_menu_moved)
        self.token_menu.categoryChanged.connect(self._handle_token_category_changed)
        self._token_menu_user_pos: QPoint | None = None
        self.token_menu.hide()
        for category in self.TOKEN_CATEGORIES:
            self._refresh_token_category(category)
        self._position_token_menu()

        self._register_existing_cards()
        self._refresh_deck_view()

        self._last_stack_cycle_ids: list[str] = []
        self._stack_selection_key: tuple[str, ...] | None = None
        self._stack_anchor_point: QPointF | None = None
        self._preview_state: dict | None = None
        self._drag_visible_cards: WeakSet[Card] = WeakSet()
        self._drag_rehide_cards: WeakSet[Card] = WeakSet()

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
        self._position_search_loading_spinner()
        self._position_deck_view()
        self._position_zone_viewer()
        self._position_token_menu()
        self._position_icon_bar()
        self._hide_context_menus()

    def moveEvent(self, ev):
        super().moveEvent(ev)
        self._position_icon_bar()
        self._position_token_menu()

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

    def _create_spinner(self, icon_path):
        spinner = LoadingSpinner(icon_path, self)
        spinner.hide()
        return spinner

    def _position_search_loading_spinner(self):
        spinner = getattr(self, "search_loading_spinner", None)
        if spinner is None:
            return
        size = spinner.size()
        if size.isEmpty():
            return
        menu = getattr(self, "scryfall_search_menu", None)
        if menu is not None:
            input_widget = getattr(menu, "_search_input", None)
            if input_widget is not None:
                input_rect = input_widget.geometry()
                input_top_left = menu.mapToGlobal(input_rect.topLeft())
                target_x = input_top_left.x() + input_rect.width() // 2 - size.width() // 2
                target_y = input_top_left.y() - size.height() - 12
                top_left = QPoint(max(0, target_x), max(0, target_y))
            else:
                rect = menu.geometry()
                center = rect.center()
                top_left = QPoint(
                    max(0, center.x() - size.width() // 2),
                    max(0, center.y() - size.height() // 2),
                )
        else:
            center = self.rect().center()
            top_left = QPoint(
                max(0, center.x() - size.width() // 2),
                max(0, center.y() - size.height() // 2),
            )
        spinner.move(top_left)
        if spinner.isVisible():
            spinner.raise_()

    def _position_deck_view(self):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        self.deck_view_widget.resize_for_window(self.size())
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

    def _position_preview_zone(self):
        widget = getattr(self, "deck_view_widget", None)
        preview = getattr(self, "preview_zone", None)
        if widget is None or preview is None:
            return
        if not widget.isVisible():
            preview.setVisible(False)
            widget.sync_preview_zone_view()
            return
        rect = widget.preview_area_rect()
        if rect.isNull():
            preview.setVisible(False)
            widget.sync_preview_zone_view()
            return
        preview.set_width(rect.width())
        preview.set_height(rect.height())
        top_left = self.view.viewport().mapFromGlobal(rect.topLeft())
        scene_pos = self.view.mapToScene(top_left)
        preview.setPos(scene_pos)
        preview.setVisible(True)
        preview.reflow_cards()
        widget.sync_preview_zone_view()
        self._position_search_loading_spinner()
        self._position_icon_bar()

    def _position_icon_bar(self):
        bar = getattr(self, "icon_bar", None)
        if bar is None:
            return
        size = bar.size()
        if size.isEmpty():
            bar.adjustSize()
            size = bar.size()
        margin = 16
        top_offset = 48
        x = max(0, self.width() - size.width() - margin)
        y = max(0, top_offset)
        bar.move(x, y)
        if bar.isVisible():
            bar.raise_()

    def _configure_icon_bar(self):
        bar = getattr(self, "icon_bar", None)
        if bar is None:
            return
        base = Path(__file__).resolve().parent / "resources" / "icons"
        actions = [
            {
                "id": "library",
                "icon": base / "library.svg",
                "tooltip": "Library",
                "name": "Library",
            },
            {
                "id": "graveyard",
                "icon": base / "graveyard.svg",
                "tooltip": "Graveyard",
                "name": "Graveyard",
            },
            {
                "id": "search",
                "icon": base / "search.svg",
                "tooltip": "Search",
                "name": "Search",
            },            {
                "id": "tokens",
                "icon": base / "tokens.svg",
                "tooltip": "Tokens",
                "name": "Tokens",
            },
        ]
        bar.set_actions(actions)
        bar.buttonClicked.connect(self._handle_icon_bar_click)
        bar.show()
        bar.raise_()
        self._position_icon_bar()

    def _handle_icon_bar_click(self, key: str):
        print(f"[IconBar] Button clicked: {key}")
        match key:
            case "library":
                self._toggle_deck_view()
            case "graveyard":
                self._toggle_zone_viewer()
            case "tokens":
                self._toggle_token_menu()

    def _position_zone_viewer(self):
        viewer = getattr(self, "zone_viewer", None)
        if viewer is None:
            return
        viewer.resize_for_window(self.size())
        size = viewer.size()
        user_pos = getattr(self, "_zone_viewer_user_pos", None)
        if user_pos is None:
            top_left = QPoint(
                max(0, self.width() - size.width() - 24),
                max(0, 32),
            )
        else:
            top_left = self._clamp_point_to_window(user_pos, size)
        viewer.move(top_left)
        if viewer.isVisible():
            viewer.raise_()

    def _position_token_menu(self):
        menu = getattr(self, "token_menu", None)
        if menu is None:
            return
        menu.resize_for_window(self.size())
        size = menu.size()
        user_pos = getattr(self, "_token_menu_user_pos", None)
        if user_pos is None:
            top_left = QPoint(
                max(0, 24),
                max(0, self.height() // 6),
            )
        else:
            top_left = self._clamp_point_to_window(user_pos, size)
        menu.move(top_left)
        if menu.isVisible():
            menu.raise_()

    def _clamp_point_to_window(self, pos: QPoint, size) -> QPoint:
        max_x = max(0, self.width() - size.width())
        max_y = max(0, self.height() - size.height())
        clamped_x = max(0, min(pos.x(), max_x))
        clamped_y = max(0, min(pos.y(), max_y))
        return QPoint(clamped_x, clamped_y)
    
    def _hide_token_menu(self):
        menu = getattr(self, "token_menu", None)
        if menu is None:
            return
        menu.hide()

    def _on_token_menu_moved(self, pos: QPoint):
        self._token_menu_user_pos = QPoint(pos)

    def _handle_token_category_changed(self, category: str):
        if not category:
            return
        self._refresh_token_category(category)

    def _refresh_token_category(self, category: str):
        menu = getattr(self, "token_menu", None)
        cache = getattr(self, "card_cache", None)
        if menu is None or cache is None:
            return
        if category == "deck":
            tokens: list[TokenDescriptor] = []
        else:
            tokens = cache.list_tokens(category)
        menu.set_tokens_for_category(category, tokens)


    def _refresh_deck_view(self, *_):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        self.deck_view_widget.set_cards(self._library_card_entries())
        self._sync_preview_state()
        self._position_preview_zone()
        self._refresh_zone_viewer()

    def _library_card_entries(self) -> list[dict[str, object]]:
        library = getattr(self, "library_zone", None)
        if library is None or not hasattr(library, "cards"):
            return []
        entries: list[dict[str, object]] = []
        for card in library.cards:
            data = getattr(card, "card_data", None) or {}
            display_name = data.get("name") or getattr(card, "print_id", card.card_id)
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
        card.set_zone_hidden(True)
        card.set_face_down(False)
        preview.insert_card(0, card)
        card.set_zone_hidden(False)
        self.scene.model.containers[card.card_id] = preview.zone_id
        self._update_zone_state(library)
        self._update_zone_state(preview)
        card_data = getattr(card, "card_data", None) or {}
        name = card_data.get("name") if isinstance(card_data, dict) else None
        if self.deck_view_widget:
            label = name or getattr(card, "print_id", card.card_id)
            self.deck_view_widget.set_preview_title(label)
            self.deck_view_widget.sync_preview_zone_view()
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
        in_preview = preview is not None and card in getattr(preview, "cards", [])
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
        if self.deck_view_widget:
            self.deck_view_widget.clear_preview_title()
            self.deck_view_widget.sync_preview_zone_view()
        if preview:
            preview.setVisible(False)

    def _sync_preview_state(self):
        if not getattr(self, "_preview_state", None):
            return
        card = self._preview_state.get("card")
        if card is None:
            self._preview_state = None
            return
        container = self.scene.model.containers.get(card.card_id)
        preview_zone = getattr(self, "preview_zone", None)
        target_zone = getattr(preview_zone, "zone_id", "preview")
        if container != target_zone:
            self._preview_state = None
            if self.deck_view_widget:
                self.deck_view_widget.clear_preview_title()
                self.deck_view_widget.sync_preview_zone_view()
            if preview_zone:
                preview_zone.setVisible(False)

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

    def _toggle_zone_viewer(self):
        viewer = getattr(self, "zone_viewer", None)
        if viewer is None:
            return
        if viewer.isVisible():
            self._hide_zone_viewer()
            return
        self._refresh_zone_viewer()
        self._position_zone_viewer()
        viewer.show()
        viewer.raise_()

    def _toggle_token_menu(self):
        menu = getattr(self, "token_menu", None)
        if menu is None:
            return
        if menu.isVisible():
            self._hide_token_menu()
            return
        # Refresh the current category from disk before showing.
        current_category = menu.current_category() or self.TOKEN_CATEGORIES[0]
        self._refresh_token_category(current_category)
        self._position_token_menu()
        menu.show()
        menu.raise_()

    def _toggle_icon_bar(self):
        bar = getattr(self, "icon_bar", None)
        if bar is None:
            return
        if bar.isVisible():
            bar.hide()
        else:
            self._position_icon_bar()
            bar.show()
            bar.raise_()

    def _handle_escape_shortcut(self, ev=None):
        load_menu = getattr(self, "load_menu", None)
        if load_menu is None:
            return
        self._hide_context_menus()
        if load_menu.isVisible():
            load_menu.hide()
            return
        self._position_load_menu()
        load_menu.show_import_menu()

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
            removed_from_zone = False
            if zone and card in zone.cards:
                zone.remove_card(card)
                self._update_zone_state(zone)
                removed_from_zone = True
            if not removed_from_zone:
                for candidate in scene.zones.values():
                    if candidate is zone:
                        continue
                    if card in candidate.cards:
                        candidate.remove_card(card)
                        self._update_zone_state(candidate)
                        removed_from_zone = True
                        break

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

    def _handle_graveyard_shortcut(self, ev=None):
        scene = getattr(self, "scene", None)
        zone = getattr(self, "graveyard_zone", None)
        if scene is None or zone is None:
            return
        selected_cards = self._selected_cards_for_drop()
        if not selected_cards:
            return
        if getattr(self, "_preview_state", None):
            preview_card = self._preview_state.get("card")
            if preview_card in selected_cards:
                self._clear_preview(return_to_library=False)
        self._insert_cards_into_zone(selected_cards, zone)
        self._refresh_hidden_proxy()

    def _selected_cards_for_drop(self, primary: Card | None = None) -> list[Card]:
        scene = getattr(self, "scene", None)
        if scene is None:
            return []
        selected_cards = [item for item in scene.selectedItems() if isinstance(item, Card)]
        if primary and primary not in selected_cards:
            selected_cards.append(primary)
        hover_card = getattr(scene, "hover_card", None)
        if hover_card and hover_card not in selected_cards:
            selected_cards.append(hover_card)
        return selected_cards

    def _insert_cards_into_zone(self, cards: list[Card], zone: Zone):
        if not cards or zone is None:
            return
        table_positions = {card.card_id: QPointF(card.pos()) for card in cards}
        cmd = InsertIntoZoneCommand(
            self.model,
            self.scene.zones,
            cards,
            zone,
            len(zone.cards),
            table_pos=table_positions,
        )
        self.undo.push(cmd)
        self._on_card_action()

    def _refresh_hidden_proxy(self):
        proxy = getattr(self, "hidden_zone_proxy", None)
        viewer = getattr(self, "zone_viewer", None)
        if proxy and viewer and viewer.isVisible():
            proxy.refresh()

    def _hide_context_menus(self, *, keep_search_spinner: bool = False):
        if getattr(self, "card_context_menu", None):
            self.card_context_menu.hide()
        if getattr(self, "table_context_menu", None):
            self.table_context_menu.hide()
        if getattr(self, "scryfall_search_menu", None):
            self.scryfall_search_menu.hide()
        if not keep_search_spinner:
            spinner = getattr(self, "search_loading_spinner", None)
            if spinner is not None:
                spinner.finish(immediate=True)

    def _show_card_context_menu(self, card: Card, global_pos):
        self._hide_context_menus()
        if getattr(self, "card_context_menu", None):
            self.card_context_menu.show_for_card(card, global_pos)

    def _show_table_context_menu(self, global_pos, scene_pos):
        self._hide_context_menus()
        if getattr(self, "table_context_menu", None):
            self.table_context_menu.show_for_table(global_pos, scene_pos)

    def _handle_scryfall_search(self, query: str):
        loader = getattr(self, "deck_loader", None)
        normalized = (query or "").strip()
        if not normalized:
            print("[ScryfallSearch] Ignoring empty query")
            return
        if loader is None:
            print("[ScryfallSearch] DeckLoader unavailable")
            return
        self._start_search_spinner()
        self._launch_scryfall_search_thread(loader, normalized)

    def _start_search_spinner(self):
        spinner = getattr(self, "search_loading_spinner", None)
        if spinner is None:
            return
        self._position_search_loading_spinner()
        spinner.start()
        spinner.raise_()
        # Ensure we don't have a stale worker reference hanging around
        if getattr(self, "_search_worker", None) is None:
            self._search_worker = None

    def _handle_scryfall_result_click(self, payload: dict):
        # payload: {"card": card_dict, "set": set_code, "collector_number": collector}
        self._hide_context_menus(keep_search_spinner=True)
        set_code = (payload.get("set") or "").strip().upper() if isinstance(payload, dict) else ""
        collector = (payload.get("collector_number") or "").strip() if isinstance(payload, dict) else ""
        card_name = ""
        if isinstance(payload, dict):
            card_obj = payload.get("card") or {}
            if isinstance(card_obj, dict):
                card_name = card_obj.get("name") or ""
        if not set_code or not collector:
            print("[ScryfallSearch] Missing set_code or collector_number; cannot spawn.")
            if getattr(self, "search_loading_spinner", None):
                self.search_loading_spinner.finish(immediate=True)
            return
        spinner = getattr(self, "search_loading_spinner", None)
        if spinner is not None:
            self._position_search_loading_spinner()
            spinner.start()
            spinner.raise_()
        try:
            card = self.spawner.spawn_card(set_code, collector)
            self._layout_cards_on_table([card])
            print(f"[ScryfallSearch] Spawned card {set_code}/{collector} {card_name}")
        except Exception as exc:
            print(f"[ScryfallSearch] Failed to spawn card {set_code}/{collector}: {exc}")
        finally:
            if spinner is not None:
                spinner.finish()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            load_menu = getattr(self, "load_menu", None)
            if load_menu and load_menu.isVisible():
                global_pos = None
                if hasattr(event, "globalPosition"):
                    try:
                        global_pos = event.globalPosition().toPoint()
                    except Exception:
                        global_pos = None
                if global_pos is None and hasattr(event, "globalPos"):
                    global_pos = event.globalPos()
                if global_pos is not None:
                    local_pos = load_menu.mapFromGlobal(global_pos)
                    if not load_menu.rect().contains(local_pos):
                        load_menu.hide()
        return super().eventFilter(obj, event)
    def _launch_scryfall_search_thread(self, loader, query: str):
        # Cancel any previous search thread
        prev_thread = getattr(self, "_search_thread", None)
        if prev_thread and prev_thread.isRunning():
            prev_thread.requestInterruption()

        worker = _ScryfallSearchWorker(loader, query)
        thread = QThread(self)
        worker.moveToThread(thread)

        worker.finished.connect(self._on_scryfall_search_success)
        worker.failed.connect(self._on_scryfall_search_failure)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: setattr(self, "_search_worker", None))
        thread.finished.connect(lambda: setattr(self, "_search_thread", None))

        self._search_thread = thread
        self._search_worker = worker
        thread.started.connect(worker.run)
        thread.start()

    @Slot(list, str)
    def _on_scryfall_search_success(self, results: list, query: str):
        print(f"[ScryfallSearch] Received {len(results)} results for '{query}':")
        print(results)
        if getattr(self, "scryfall_search_menu", None):
            self.scryfall_search_menu.set_results(results)
        spinner = getattr(self, "search_loading_spinner", None)
        if spinner is not None:
            spinner.finish()
        thread = getattr(self, "_search_thread", None)
        if thread and thread.isRunning():
            thread.quit()

    @Slot(str, str)
    def _on_scryfall_search_failure(self, message: str, query: str):
        print(f"[ScryfallSearch] Failed search for '{query}': {message}")
        spinner = getattr(self, "search_loading_spinner", None)
        if spinner is not None:
            spinner.finish()
        thread = getattr(self, "_search_thread", None)
        if thread and thread.isRunning():
            thread.quit()

    def _hide_deck_view(self):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        self.deck_view_widget.hide()
        self._clear_preview(return_to_library=True)
        if hasattr(self, "preview_zone") and self.preview_zone:
            self.preview_zone.setVisible(False)

    def _hide_zone_viewer(self):
        viewer = getattr(self, "zone_viewer", None)
        if viewer is None:
            return
        viewer.hide()
        proxy = getattr(self, "hidden_zone_proxy", None)
        if proxy:
            proxy.refresh()

    def _on_deck_view_moved(self, pos: QPoint):
        if not hasattr(self, "deck_view_widget") or self.deck_view_widget is None:
            return
        size = self.deck_view_widget.size()
        clamped = self._clamp_point_to_window(pos, size)
        if clamped != pos:
            self.deck_view_widget.move(clamped)
        self._deck_view_user_pos = clamped
        self._position_preview_zone()

    def _on_zone_viewer_moved(self, pos: QPoint):
        viewer = getattr(self, "zone_viewer", None)
        if viewer is None:
            return
        size = viewer.size()
        clamped = self._clamp_point_to_window(pos, size)
        if clamped != pos:
            viewer.move(clamped)
        self._zone_viewer_user_pos = clamped

    def _refresh_zone_viewer(self, *_):
        viewer = getattr(self, "zone_viewer", None)
        if viewer is None:
            return
        hidden_zones: list[Zone] = []
        graveyard = getattr(self, "graveyard_zone", None)
        if graveyard is not None:
            hidden_zones.append(graveyard)
        viewer.set_available_zones(hidden_zones)
        proxy = getattr(self, "hidden_zone_proxy", None)
        if proxy:
            proxy.refresh()

    def _handle_zone_viewer_zone_changed(self, zone_id: str):
        proxy = getattr(self, "hidden_zone_proxy", None)
        if proxy:
            proxy.refresh()

    def _wrap_release(self, original_release, card: Card):
        def handler(ev):
            viewer = getattr(self, "zone_viewer", None)
            drop_to_graveyard = False
            if viewer and viewer.isVisible():
                rect = viewer.viewport_rect_global()
                pos = ev.screenPos()
                if hasattr(pos, "toPoint"):
                    pos = pos.toPoint()
                if rect.contains(pos):
                    drop_to_graveyard = True
            if drop_to_graveyard and ev.button() == Qt.LeftButton:
                dragged = card.consume_user_drag() if hasattr(card, "consume_user_drag") else False
                cards = self._selected_cards_for_drop(card)
                self._insert_cards_into_zone(cards, getattr(self, "graveyard_zone", None))
                if dragged:
                    self._clear_stack_anchor()
                self._refresh_hidden_proxy()
                return
            original_release(ev)
            dragged = card.consume_user_drag() if hasattr(card, "consume_user_drag") else False
            if ev.button() == Qt.LeftButton:
                self.scene.drop_released(card)
                if dragged:
                    self._clear_stack_anchor()
                self._on_card_action()
            if dragged:
                self._reset_zone_drag_visibility()
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

    def _handle_duplicate_shortcut(self, ev=None):
        scene = getattr(self, "scene", None)
        spawner = getattr(self, "spawner", None)
        if scene is None or spawner is None:
            return

        selected_cards = [
            it for it in scene.selectedItems()
            if isinstance(it, Card)
        ]
        hover_card = getattr(scene, "hover_card", None)
        if hover_card and hover_card not in selected_cards:
            selected_cards.append(hover_card)
        if not selected_cards:
            return

        table_cards = [
            card for card in selected_cards
            if scene.model.containers.get(card.card_id, "table") == "table"
        ]
        if not table_cards:
            return

        duplicates: list[Card] = []
        for card in table_cards:
            try:
                duplicate = spawner.duplicate_card(card)
            except Exception as exc:
                print(f"[MainWindow] Failed to duplicate card {getattr(card, 'card_id', '?')}: {exc}")
                continue
            card_rect = card.boundingRect()
            spread = QPointF(card_rect.width() * 0.2, card_rect.height() * 0.15)
            pos = QPointF(card.pos()) + spread
            self.scene.add_card(duplicate, pos)
            duplicate.setZValue(card.zValue() + 1.0)
            self._register_card_item(duplicate)
            duplicates.append(duplicate)

        if duplicates:
            self._clear_stack_anchor()
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

    def _draw_cards(self, count: int):
        if count <= 0:
            return
        library = getattr(self, "library_zone", None)
        hand = getattr(self, "hand_zone", None)
        if library is None or hand is None:
            return
        available = len(getattr(library, "cards", []))
        if available <= 0:
            return
        draw_count = min(count, available)
        cards_to_draw = list(reversed(library.cards[-draw_count:]))
        if not cards_to_draw:
            return
        insert_at = len(hand.cards)
        table_positions = {card.card_id: QPointF(card.pos()) for card in cards_to_draw}
        cmd = InsertIntoZoneCommand(
            self.model,
            self.scene.zones,
            cards_to_draw,
            hand,
            insert_at,
            table_pos=table_positions,
        )
        self.undo.push(cmd)
        self._on_card_action()

    def _handle_draw_shortcut(self):
        self._draw_cards(1)

    def _handle_shuffle_shortcut(self):
        library = getattr(self, "library_zone", None)
        cards = getattr(library, "cards", None) if library is not None else None
        if not cards or len(cards) < 2:
            return
        random.shuffle(cards)
        library.reflow_cards()
        self._update_zone_state(library)
        self._refresh_deck_view()
        self._on_card_action()

    def _handle_ctrl_s_shortcut(self):
        library = getattr(self, "library_zone", None)
        hand = getattr(self, "hand_zone", None)
        if library is None or hand is None:
            return

        if hand.cards:
            cards_to_move = list(hand.cards)
            insert_at = len(library.cards)
            table_positions = {card.card_id: QPointF(card.pos()) for card in cards_to_move}
            cmd = InsertIntoZoneCommand(
                self.model,
                self.scene.zones,
                cards_to_move,
                library,
                insert_at,
                table_pos=table_positions,
            )
            self.undo.push(cmd)
            self._on_card_action()

        self._handle_shuffle_shortcut()
        self._draw_cards(7)

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
        self._ensure_zone_drag_visibility(card)

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

        tokens_to_table: list[Card] = []
        inserted_any = False
        for card in cards:
            if zone.zone_id == "library" and getattr(card, "is_token", False):
                tokens_to_table.append(card)
                continue
            # Add to scene so zone can take ownership and hide/reflow as needed.
            self.scene.add_card(card, zone.pos())
            self._register_card_item(card)
            insert_at = len(zone.cards)
            zone.insert_card(insert_at, card)
            self.scene.model.containers[card.card_id] = zone.zone_id
            inserted_any = True

        zone_order = [c.card_id for c in zone.cards]
        self.scene.model.zones[zone.zone_id]["order"] = zone_order
        for card in zone.cards:
            self.scene.model.cards[card.card_id]["pos"] = card.pos()
        if tokens_to_table:
            self._layout_cards_on_table(tokens_to_table)
        if zone.zone_id == "library":
            self._refresh_deck_view()
        return inserted_any or bool(tokens_to_table)

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

    def _ensure_zone_drag_visibility(self, anchor_card: Card):
        scene = getattr(self, "scene", None)
        view = getattr(self, "view", None)
        if scene is None or view is None:
            return
        viewport = view.viewport()
        if viewport is None:
            return
        selected_cards = [it for it in scene.selectedItems() if isinstance(it, Card)]
        if anchor_card not in selected_cards:
            selected_cards.append(anchor_card)
        for card in selected_cards:
            add_override = getattr(card, "add_hidden_viewport", None)
            if callable(add_override):
                add_override(viewport)
                self._drag_visible_cards.add(card)
            if getattr(card, "_zone_hidden", False):
                card.set_zone_hidden(False)
                self._drag_rehide_cards.add(card)

    def _reset_zone_drag_visibility(self):
        if not self._drag_visible_cards:
            return
        view = getattr(self, "view", None)
        viewport = view.viewport() if view else None
        for card in list(self._drag_visible_cards):
            remove_override = getattr(card, "remove_hidden_viewport", None)
            if callable(remove_override) and viewport is not None:
                remove_override(viewport)
        self._drag_visible_cards.clear()
        if self._drag_rehide_cards:
            scene = getattr(self, "scene", None)
            model = getattr(self, "model", None)
            for card in list(self._drag_rehide_cards):
                container = None
                if model is not None:
                    container = model.containers.get(card.card_id)
                zone = None
                if scene is not None and container:
                    zone = scene.zones.get(container)
                if zone and getattr(zone, "hide_cards", False):
                    card.set_zone_hidden(True)
            self._drag_rehide_cards.clear()

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
