from pathlib import Path

from PySide6.QtCore import Qt, Slot, Signal
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QWidget,
    QHBoxLayout,
    QFileDialog,
)


class LoadMenu(QWidget):
    """Root widget that wraps the primary and secondary action menus."""

    PRIMARY_OPTIONS = ("Load Deck", "Import Cards")
    IMPORT_OPTIONS = ("From Clipboard", "From File", "From URL")
    DEFAULT_IMPORT_ZONE = "library"

    _MENU_MARGIN = 12
    _LIST_SPACING = 16
    _FONT_SIZE = 20
    _FONT_FAMILY = None
    _FONT_PATH = (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "fonts"
        / "Almendra"
        / "Almendra-Regular.ttf"
    )

    # payload text, target zone id
    importRequested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loadMenu")

        self.main_list = QListWidget(self)
        self.import_list = QListWidget(self)
        self._primary_size = (0, 0)
        self._clipboard_snapshot: str | None = None
        self._file_contents: str | None = None

        self._configure_list(self.main_list)
        self._configure_list(self.import_list)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self._LIST_SPACING)
        layout.addWidget(self.main_list)
        layout.addWidget(self.import_list)
        layout.setAlignment(Qt.AlignTop)

        self._ensure_font_loaded()
        self._apply_font()

        self._populate_primary()
        self._cache_primary_size()
        self._populate_import()
        self.import_list.hide()

        self.main_list.itemClicked.connect(self._handle_main_clicked)
        self.main_list.currentItemChanged.connect(self._on_main_selection_changed)
        self.import_list.itemClicked.connect(self._handle_import_clicked)

        self._update_size()

    def _configure_list(self, widget: QListWidget):
        widget.setUniformItemSizes(True)
        widget.setSelectionMode(QListWidget.SingleSelection)
        widget.setFocusPolicy(Qt.NoFocus)

    def _populate_primary(self):
        self.main_list.clear()
        for option in self.PRIMARY_OPTIONS:
            QListWidgetItem(option, self.main_list)
        self.main_list.setCurrentRow(0)

    def _populate_import(self):
        self.import_list.clear()
        for option in self.IMPORT_OPTIONS:
            QListWidgetItem(option, self.import_list)

    @Slot(QListWidgetItem, QListWidgetItem)
    def _on_main_selection_changed(
        self, current: QListWidgetItem, previous: QListWidgetItem
    ):
        if current and current.text() == "Import Cards":
            self._toggle_import_menu(True)
        else:
            self._toggle_import_menu(False)

    @Slot(QListWidgetItem)
    def _handle_main_clicked(self, item: QListWidgetItem):
        print(f"[LoadMenu] {item.text()} clicked")

    @Slot(QListWidgetItem)
    def _handle_import_clicked(self, item: QListWidgetItem):
        print(f"[LoadMenu] Import option: {item.text()}")
        text = item.text()
        loaded_string = ""
        if text == "From Clipboard":
            loaded_string = self._capture_clipboard_text()
            print(f"[LoadMenu] Clipboard captured ({len(loaded_string or '')} chars)")
            self.hide()
        elif text == "From File":
            file_text = self._open_file_and_read()
            if file_text is not None:
                loaded_string = file_text
                print(f"[LoadMenu] Loaded file ({len(loaded_string)} chars)")
                self.hide()
        if loaded_string:
            print("[LoadMenu] Emitting importRequested signal")
            self.importRequested.emit(loaded_string, self.DEFAULT_IMPORT_ZONE)

    def _toggle_import_menu(self, show: bool):
        currently_visible = self.import_list.isVisible()
        if show == currently_visible:
            return
        self.import_list.setVisible(show)
        if not show:
            self.import_list.clearSelection()
        self._update_size()

    def _update_size(self):
        main_w, main_h = self._primary_size if any(self._primary_size) else self._list_size(self.main_list)
        self.main_list.setFixedSize(main_w, main_h)

        import_w, import_h = (0, 0)
        if self.import_list.isVisible():
            import_w, import_h = self._list_size(self.import_list)
            self.import_list.setFixedSize(import_w, import_h)

        spacing = self._LIST_SPACING if self.import_list.isVisible() and import_w > 0 else 0
        total_width = main_w + spacing + import_w
        total_height = max(main_h, import_h)
        self.setFixedSize(total_width, total_height)

    def _cache_primary_size(self):
        if self.main_list.count() == 0:
            self._primary_size = (0, 0)
        else:
            self._primary_size = self._list_size(self.main_list)

    def _list_size(self, widget: QListWidget):
        if widget.count() == 0:
            return (0, 0)
        column_width = widget.sizeHintForColumn(0)
        row_height = sum(widget.sizeHintForRow(i) for i in range(widget.count()))
        frame = widget.frameWidth()
        total_margin = 2 * (frame + self._MENU_MARGIN)
        width = column_width + total_margin
        height = row_height + total_margin
        return width, height

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
        self.main_list.setFont(font)
        self.import_list.setFont(font)

    def primary_center_offset(self) -> float:
        if self.main_list is None:
            return self.height() / 2
        return self.main_list.y() + self.main_list.height() / 2

    def _capture_clipboard_text(self) -> str:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return ""
        text = clipboard.text()
        return text if text is not None else ""

    def _open_file_and_read(self) -> str | None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Cards From File",
            "",
            "All Files (*.*)",
        )
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            print(f"[LoadMenu] Failed to read file '{path}': {exc}")
            return None
