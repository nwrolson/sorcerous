from __future__ import annotations

from pathlib import Path
from functools import partial

from typing import Callable, Iterable, Optional, Sequence

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QPixmap, QGuiApplication, QCursor
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
)

from .mana_symbols import ManaCostRenderer
from spawner.token_descriptor import TokenDescriptor


class BaseContextMenu(QFrame):
    """Shared styling/behavior for lightweight popup menus."""

    _PADDING = 12
    _SPACING = 8
    _BELEREN_FONT: QFont | None = None
    _FONT_PATH = (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "fonts"
        / "Beleren"
        / "Beleren2016-Bold.ttf"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sorcerousContextMenu")
        self.setWindowFlag(Qt.Popup, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self._ensure_font_loaded()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._PADDING, self._PADDING, self._PADDING, self._PADDING)
        layout.setSpacing(self._SPACING)
        self._layout = layout

        self.setStyleSheet(
            """
            #sorcerousContextMenu {
                background: #121212;
                border: 2px solid #3f3a2f;
                border-radius: 10px;
                color: #f5f5f5;
            }
            #sorcerousContextMenu QPushButton {
                background: #1b1b1b;
                color: #f5f5f5;
                border: 1px solid #3f3a2f;
                border-radius: 6px;
                padding: 8px 12px;
                text-align: left;
            }
            #sorcerousContextMenu QPushButton:hover {
                border-color: #f5f5f5;
            }
            """
        )
        self._apply_font()

    def show_at(self, global_pos: QPoint):
        self.adjustSize()
        # Popup windows expect global coordinates; do not remap to parent.
        self.move(global_pos)
        self.show()
        self.raise_()

    @classmethod
    def _ensure_font_loaded(cls):
        if cls._BELEREN_FONT is not None:
            return
        font_path = cls._FONT_PATH
        if not font_path.exists():
            return
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id == -1:
            return
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            return
        base = QFont(families[0])
        size = base.pointSize()
        if size <= 0:
            size = 22
        base.setPointSize(size + 2)
        cls._BELEREN_FONT = base

    def _apply_font(self):
        if self._BELEREN_FONT is None:
            return
        self.setFont(self._BELEREN_FONT)
        for btn in self.findChildren(QPushButton):
            btn.setFont(self._BELEREN_FONT)
        for line in self.findChildren(QLineEdit):
            line.setFont(self._BELEREN_FONT)
        for label in self.findChildren(QLabel):
            label.setFont(self._BELEREN_FONT)


class CardContextMenu(BaseContextMenu):
    """Popup menu for actions on a specific card."""

    def __init__(self, parent=None, *, cache=None, spawner=None, board_scene=None, board_view=None):
        super().__init__(parent)
        self._card = None
        self._cache = cache
        self._spawner = spawner
        self._board_scene = board_scene
        self._board_view = board_view
        self._actions: list[dict] = []
        self._action_buttons: list[QPushButton] = []
        self._list_menu = ActionListMenu(self)
        self._register_default_actions()
        self._apply_font()

    def show_for_card(self, card, global_pos: QPoint):
        self._card = card
        self._rebuild_action_buttons()
        self.adjustSize()
        target = self._card_menu_position(card, global_pos)
        self.show_at(target)

    # --- Actions ---
    def add_action(
        self,
        action_id: str,
        label: str,
        handler: Callable[[object, Optional[QPushButton]], None] | None = None,
        *,
        visible_if: Callable[[object], bool] | None = None,
        submenu_provider: Callable[[object], list[tuple[str, Callable[[], None]]]] | None = None,
    ):
        """Register an action descriptor."""
        self._actions.append(
            {
                "id": action_id,
                "label": label,
                "handler": handler,
                "visible_if": visible_if,
                "submenu_provider": submenu_provider,
            }
        )

    def _register_default_actions(self):
        self.add_action(
            "tokens",
            "Tokens",
            handler=self._handle_tokens_clicked,
            visible_if=lambda card: bool(self._linked_tokens(card)),
        )
        self.add_action(
            "send",
            "Send To",
            handler=self._handle_tokens_clicked,
            visible_if=lambda card: bool(self._linked_tokens(card)),
        )

    def _rebuild_action_buttons(self):
        for btn in self._action_buttons:
            btn.setParent(None)
            btn.deleteLater()
        self._action_buttons.clear()

        for action in self._actions:
            visible_fn = action.get("visible_if")
            if callable(visible_fn) and not visible_fn(self._card):
                continue
            btn = QPushButton(action.get("label") or action.get("id", "Action"), self)
            btn.setCursor(Qt.PointingHandCursor)
            submenu_provider = action.get("submenu_provider")
            handler = action.get("handler")
            if callable(submenu_provider):
                btn.clicked.connect(partial(self._handle_submenu_action, submenu_provider, btn))
            elif callable(handler):
                btn.clicked.connect(partial(handler, self._card, btn))
            self._layout.addWidget(btn)
            self._action_buttons.append(btn)
        self._apply_font()

    def _handle_tokens_clicked(self, card=None, button: Optional[QPushButton] = None):
        linked = self._linked_tokens(card)
        if not linked:
            self.hide()
            return
        descriptors = self._resolve_token_descriptors(linked)
        if not descriptors:
            self.hide()
            return
        items = [(desc.name or desc.id, partial(self._spawn_token_from_descriptor, desc)) for desc in descriptors]
        self._show_submenu(items, button)

    def _handle_submenu_action(
        self,
        provider: Callable[[object], list[tuple[str, Callable[[], None]]]],
        button: QPushButton,
    ):
        items = provider(self._card) if callable(provider) else []
        if not items:
            self.hide()
            return
        self._show_submenu(items, button)

    def _show_submenu(self, items: list[tuple[str, Callable[[], None]]], button: Optional[QPushButton]):
        menu_top = self.mapToGlobal(self.rect().topRight()).y()
        anchor_btn_right = button.mapToGlobal(button.rect().topRight()) if button else self.mapToGlobal(self.rect().topRight())
        target = QPoint(anchor_btn_right.x() + 12, menu_top)
        self._list_menu.show_items(items, target)

    def _card_menu_position(self, card, fallback: QPoint) -> QPoint:
        view = self._board_view
        if card is not None and view is not None:
            try:
                scene_rect = card.mapToScene(card.boundingRect()).boundingRect()
                right_mid_scene = QPointF(scene_rect.right(), scene_rect.center().y())
                view_point = view.mapFromScene(right_mid_scene)
                global_point = view.mapToGlobal(view_point.toPoint())
                y = int(global_point.y() - self.height() / 2)
                return QPoint(int(global_point.x() + 12), y)
            except Exception:
                pass
        return QPoint(fallback.x() + 12, fallback.y() - self.height() // 2)

    def _linked_tokens(self, card=None) -> list[str]:
        card = card if card is not None else self._card
        data = getattr(card, "card_data", None)
        if not isinstance(data, dict):
            return []
        linked = data.get("linked_tokens")
        if not isinstance(linked, (list, tuple)):
            return []
        return [str(x) for x in linked if isinstance(x, (str, int))]

    def _resolve_token_descriptors(self, linked: Sequence[str]) -> list[TokenDescriptor]:
        descriptors: list[TokenDescriptor] = []
        if self._cache is None:
            return descriptors
        for cache_id in linked:
            result = self._cache.get_token_images_by_cache_id(str(cache_id))
            if not result.ok or not result.images:
                continue
            data = result.data if isinstance(result.data, dict) else {}
            name = data.get("name") if isinstance(data, dict) else None
            image_path = self._pick_front_image(result.images)
            if not image_path:
                continue
            desc = TokenDescriptor(
                id=result.id,
                name=name or result.id,
                category="linked",
                image_path=image_path,
            )
            descriptors.append(desc)
        return descriptors

    @staticmethod
    def _pick_front_image(images: Sequence) -> Optional[str]:
        for entry in images:
            face = getattr(entry, "face", None)
            path = getattr(entry, "path", None)
            if face == "front" and path:
                return path
        # fallback to first
        if images:
            path = getattr(images[0], "path", None)
            if path:
                return path
        return None

    def _spawn_token_from_descriptor(self, descriptor: TokenDescriptor):
        if self._spawner is None or self._board_scene is None:
            return
        try:
            card = self._spawner.spawn_token(descriptor)
        except Exception as exc:
            print(f"[ContextMenu] Failed to spawn token: {exc}")
            return

        scene_pos = None
        parent = self.parentWidget()
        if parent and hasattr(parent, "_cursor_scene_pos"):
            try:
                scene_pos = parent._cursor_scene_pos()
            except Exception:
                scene_pos = None
        if scene_pos is None and self._board_view is not None:
            view_point = self._board_view.mapFromGlobal(QCursor.pos())
            if self._board_view.rect().contains(view_point):
                scene_pos = self._board_view.mapToScene(view_point)
        if scene_pos is None:
            scene_pos = getattr(self._card, "scenePos", lambda: None)() or QPoint(0, 0)
        try:
            if hasattr(scene_pos, "toPointF"):
                scene_pos = scene_pos.toPointF()
        except Exception:
            pass
        self._board_scene.add_card(card, scene_pos)
        if parent and hasattr(parent, "_register_card_item"):
            try:
                parent._register_card_item(card)
            except Exception:
                pass
        self._board_scene.clearSelection()
        card.setSelected(True)
        self._board_scene.drop_released(card)
        self.hide()


class TableContextMenu(BaseContextMenu):
    """Popup menu for actions when right-clicking the table."""

    def __init__(self, parent=None, search_menu=None):
        super().__init__(parent)
        self._last_scene_pos = None
        self._search_menu = search_menu
        self.search_btn = QPushButton("Scryfall Search", self)
        self.search_btn.clicked.connect(self._handle_search_clicked)
        self._layout.addWidget(self.search_btn)
        self._apply_font()

    def show_for_table(self, global_pos: QPoint, scene_pos):
        self._last_scene_pos = scene_pos
        self.show_at(global_pos)

    def _handle_search_clicked(self):
        self.hide()
        if self._search_menu is not None:
            self._search_menu.show_for_search(None)
            # Ensure spinner can position with the menu now visible
            parent = self.parentWidget()
            if parent and hasattr(parent, "_position_search_loading_spinner"):
                try:
                    parent._position_search_loading_spinner()
                except Exception:
                    pass


class ScryfallSearchMenu(BaseContextMenu):
    """Popup menu that accepts a Scryfall search query."""

    def __init__(self, parent=None, on_submit=None, on_result_click=None):
        super().__init__(parent)
        self._on_submit = on_submit
        self._on_result_click = on_result_click
        self._mana_renderer = ManaCostRenderer()

        self._search_input = QLineEdit(self)
        self._search_input.setPlaceholderText("Search Scryfall...")
        self._search_input.returnPressed.connect(self._handle_submit)
        self._layout.addWidget(self._search_input)

        self._results_container = QVBoxLayout()
        self._results_container.setContentsMargins(0, 8, 0, 0)
        self._results_container.setSpacing(6)
        self._layout.addLayout(self._results_container)
        self._apply_font()

    def show_for_search(self, global_pos: QPoint | None = None):
        # Ignore provided position; center on parent or screen
        self.adjustSize()
        target: QPoint
        parent = self.parentWidget()
        if parent is not None:
            global_center = parent.mapToGlobal(parent.rect().center())
            target = QPoint(
                max(0, global_center.x() - self.width() // 2),
                max(0, global_center.y() - self.height() // 2),
            )
        else:
            screen = QGuiApplication.primaryScreen()
            geom = screen.availableGeometry() if screen else None
            if geom is not None:
                global_center = geom.center()
                target = QPoint(
                    max(0, global_center.x() - self.width() // 2),
                    max(0, global_center.y() - self.height() // 2),
                )
            else:
                target = QPoint(0, 0)
        self.show_at(target)
        self._search_input.setFocus()
        parent = self.parentWidget()
        if parent and hasattr(parent, "_position_search_loading_spinner"):
            try:
                parent._position_search_loading_spinner()
            except Exception:
                pass

    def _handle_submit(self):
        query = self._search_input.text()
        if callable(self._on_submit):
            self._on_submit(query)
        else:
            print(f"[ScryfallSearchMenu] Submitted query: {query}")

    def set_results(self, cards: list[dict]):
        # Limit to top 5
        cards = list(cards or [])[:5]
        # Clear previous
        while self._results_container.count():
            item = self._results_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not cards:
            return
        for card in cards:
            btn = self._build_result_button(card)
            self._results_container.addWidget(btn)
        self._apply_font()

    def _build_result_button(self, card: dict) -> QPushButton:
        name = str(card.get("name") or "Unknown Card")
        mana_costs = self._extract_mana_costs(card)
        mana_runs = self._mana_renderer.render_costs(mana_costs)
        set_code = (card.get("set") or "").strip()
        collector_number = str(card.get("collector_number") or "").strip()

        btn = QPushButton(self)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            """
            QPushButton {
                background: #1b1b1b;
                color: #f5f5f5;
                border: 1px solid #3f3a2f;
                border-radius: 6px;
                padding: 8px 10px;
                text-align: left;
            }
            QPushButton:hover {
                border-color: #f5f5f5;
            }
            """
        )

        row = QHBoxLayout(btn)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)
        name_lbl = QLabel(name, btn)
        name_lbl.setStyleSheet("color: #f5f5f5;")
        row.addWidget(name_lbl, 1)

        if mana_runs:
            mana_row = QHBoxLayout()
            mana_row.setContentsMargins(0, 0, 0, 0)
            mana_row.setSpacing(4)
            for run_index, run in enumerate(mana_runs):
                for symbol_index, pixmap in enumerate(run):
                    if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                        icon_lbl = QLabel(btn)
                        icon_lbl.setPixmap(pixmap)
                        mana_row.addWidget(icon_lbl)
                    if symbol_index < len(run) - 1:
                        mana_row.addSpacing(4)
                if run_index < len(mana_runs) - 1:
                    mana_row.addSpacing(8)
            row.addLayout(mana_row)
        btn.clicked.connect(
            lambda *_: self._handle_result_clicked(
                card,
                set_code,
                collector_number,
            )
        )
        return btn

    def _handle_result_clicked(self, card: dict, set_code: str, collector_number: str):
        self.hide()
        if callable(self._on_result_click):
            self._on_result_click(
                {
                    "card": card,
                    "set": set_code,
                    "collector_number": collector_number,
                }
            )

    def _extract_mana_costs(self, card: dict) -> list[str]:
        if not isinstance(card, dict):
            return []
        costs: list[str] = []
        primary = (card.get("mana_cost") or "").strip()
        if primary:
            costs.append(primary)
        faces = card.get("card_faces")
        if isinstance(faces, list):
            for face in faces:
                mana = (face.get("mana_cost") or "").strip() if isinstance(face, dict) else ""
                if mana:
                    costs.append(mana)
        return costs


class ActionListMenu(BaseContextMenu):
    """Modal popover showing a simple list of actions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        self._items: list[tuple[str, Callable[[], None]]] = []

    def show_items(self, items: Iterable[tuple[str, Callable[[], None]]], global_pos: QPoint):
        self._items = list(items or [])
        self._clear()
        for label, callback in self._items:
            btn = QPushButton(label, self)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, cb=callback: self._handle_click(cb))
            self._layout.addWidget(btn)
            self._buttons.append(btn)
        self._apply_font()
        self.show_at(global_pos)

    def _clear(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()

    def _handle_click(self, callback: Callable[[], None]):
        self.hide()
        try:
            callback()
        except Exception as exc:
            print(f"[ActionListMenu] callback failed: {exc}")
