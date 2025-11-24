from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QToolButton,
)


class IconBar(QWidget):
    """Vertical column of icon buttons styled like other Sorcerous menus."""

    buttonClicked = Signal(str)

    _BELEREN_FONT: QFont | None = None
    _FONT_PATH = (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "fonts"
        / "Beleren"
        / "Beleren2016-Bold.ttf"
    )

    def __init__(self, parent=None, *, icon_size: int = 75):
        super().__init__(parent)
        self.setObjectName("iconBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        self.setStyleSheet(
            """
            #iconBar {
                background: #121212;
                border: 2px solid #3f3a2f;
                border-radius: 12px;
            }
            #iconBar QToolButton {
                border: 1px solid #3f3a2f;
                border-radius: 10px;
                background: #1b1b1b;
                padding: 6px;
            }
            #iconBar QToolButton:hover {
                border-color: #f5f5f5;
            }
            """
        )
        self._ensure_font()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self._layout = layout
        self._icon_size = QSize(icon_size, icon_size)
        self._buttons: list[QToolButton] = []

    def set_actions(self, actions: Sequence[dict]):
        """Replace buttons with provided action descriptors."""
        # clear existing
        for btn in self._buttons:
            btn.deleteLater()
        self._buttons.clear()

        for action in actions or []:
            btn = self._build_button(action)
            self._layout.addWidget(btn)
            self._buttons.append(btn)
        self.adjustSize()

    def _build_button(self, action: dict) -> QToolButton:
        icon_path = action.get("icon")
        key = str(action.get("id") or action.get("name") or icon_path or "")
        tooltip = action.get("tooltip") or action.get("name") or ""
        btn = QToolButton(self)
        btn.setFixedSize(self._icon_size)
        btn.setIconSize(self._icon_size - QSize(16, 16))
        btn.setCursor(Qt.PointingHandCursor)
        if icon_path:
            btn.setIcon(QIcon(str(icon_path)))
        if tooltip:
            btn.setToolTip(tooltip)
        if self._BELEREN_FONT is not None:
            btn.setFont(self._BELEREN_FONT)
        btn.clicked.connect(lambda *_: self._handle_click(key))
        return btn

    def _handle_click(self, key: str):
        print(f"[IconBar] Clicked {key}")
        self.buttonClicked.emit(key)

    @classmethod
    def _ensure_font(cls):
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
        cls._BELEREN_FONT = QFont(families[0])
