from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QPixmap, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
)

from .mana_symbols import ManaCostRenderer


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
        target = global_pos
        parent = self.parentWidget()
        if parent is not None:
            target = parent.mapFromGlobal(global_pos)
        self.move(target)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._card = None
        self.placeholder_btn = QPushButton("Card Action (WIP)", self)
        self.placeholder_btn.clicked.connect(self._handle_placeholder_clicked)
        self._layout.addWidget(self.placeholder_btn)
        self._apply_font()

    def show_for_card(self, card, global_pos: QPoint):
        self._card = card
        self.show_at(global_pos)

    def _handle_placeholder_clicked(self):
        identifier = getattr(self._card, "card_id", None) or getattr(self._card, "print_id", "Unknown")
        print(f"[ContextMenu] Card action clicked for card {identifier}")
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
