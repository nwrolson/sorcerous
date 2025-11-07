from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QListWidget, QListWidgetItem


class LoadMenu(QListWidget):
    """Simple list-based menu showing the first set of actions."""

    OPTIONS = ("Load Deck", "Import Cards")
    _MENU_MARGIN = 12
    _FONT_SIZE = 20
    _FONT_FAMILY = None
    _FONT_PATH = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "Almendra" / "Almendra-Regular.ttf"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loadMenu")
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self._ensure_font_loaded()
        self._apply_font()
        self._populate()
        self.itemClicked.connect(self._handle_item_clicked)

    def _populate(self):
        self.clear()
        for option in self.OPTIONS:
            QListWidgetItem(option, self)
        self.setCurrentRow(0)
        self._update_size()

    @Slot(QListWidgetItem)
    def _handle_item_clicked(self, item: QListWidgetItem):
        print(f"[LoadMenu] {item.text()} clicked")

    def _update_size(self):
        if self.count() == 0:
            self.setFixedSize(0, 0)
            return
        column_width = self.sizeHintForColumn(0)
        row_height = sum(self.sizeHintForRow(i) for i in range(self.count()))
        frame = self.frameWidth()
        total_margin = 2 * (frame + self._MENU_MARGIN)
        width = column_width + total_margin
        height = row_height + total_margin
        self.setFixedSize(width, height)

    def _ensure_font_loaded(self):
        if LoadMenu._FONT_FAMILY is not None:
            return
        font_path = LoadMenu._FONT_PATH
        if not font_path.exists():
            print(f"[LoadMenu] Font not found at {font_path}")
            return
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id == -1:
            print(f"[LoadMenu] Failed to load font at {font_path}")
            return
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            LoadMenu._FONT_FAMILY = families[0]

    def _apply_font(self):
        if LoadMenu._FONT_FAMILY is None:
            return
        font = QFont(LoadMenu._FONT_FAMILY, self._FONT_SIZE)
        self.setFont(font)
