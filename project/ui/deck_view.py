from __future__ import annotations

from typing import Mapping, Sequence

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QEvent, Signal
from PySide6.QtGui import QPalette, QPixmap, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QPushButton,
    QFrame,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)

from pathlib import Path
from .mana_symbols import ManaCostRenderer


class DeckListDelegate(QStyledItemDelegate):
    """Custom delegate that paints the card name and a right-aligned thumbnail."""

    THUMB_SIZE = QSize(275, 100)
    PADDING = 8
    MANA_TEXT_GAP = 32
    MANA_SYMBOL_SPACING = 4
    MANA_SEPARATOR_SPACING = 4
    MANA_DELIMITER = "//"

    def paint(self, painter, option, index):
        payload = index.data(Qt.UserRole) or {}
        name = payload.get("name") or index.data(Qt.DisplayRole) or ""
        thumbnail: QPixmap | None = payload.get("thumbnail")
        mana_runs: list[list[QPixmap]] = payload.get("mana_runs") or []

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        content_rect = opt.rect.adjusted(self.PADDING, self.PADDING, -self.PADDING, -self.PADDING)
        text_rect = QRect(content_rect)

        if isinstance(thumbnail, QPixmap) and not thumbnail.isNull():
            thumb = thumbnail
            thumb_rect = QRect(
                content_rect.right() - self.THUMB_SIZE.width(),
                content_rect.top(),
                self.THUMB_SIZE.width(),
                self.THUMB_SIZE.height(),
            )
            text_rect.setRight(thumb_rect.left() - self.PADDING)
            scaled = thumb if thumb.size() == self.THUMB_SIZE else thumb.scaled(
                self.THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            painter.save()
            painter.setOpacity(0.66)
            painter.drawPixmap(thumb_rect, scaled)
            painter.restore()

        mana_width = self._mana_display_width(mana_runs, opt.fontMetrics)
        mana_rect = None
        text_rect = QRect(text_rect)
        if mana_width > 0 and text_rect.width() > mana_width + self.MANA_TEXT_GAP:
            text_rect.setRight(text_rect.right() - int(mana_width + self.MANA_TEXT_GAP))
            mana_rect = QRect(
                text_rect.right() + self.MANA_TEXT_GAP,
                text_rect.top(),
                int(mana_width),
                text_rect.height(),
            )
        else:
            mana_runs = []

        painter.save()
        painter.setPen(opt.palette.color(QPalette.Text))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, name)
        if mana_rect and mana_runs:
            self._draw_mana_symbols(painter, mana_runs, mana_rect, opt)
        painter.restore()

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        height = max(base.height(), self.THUMB_SIZE.height() + self.PADDING * 2)
        return QSize(base.width(), height)

    def _mana_display_width(self, mana_runs: list[list[QPixmap]], metrics) -> int:
        if not mana_runs:
            return 0
        width = 0
        delimiter_width = metrics.horizontalAdvance(self.MANA_DELIMITER)
        for run_index, run in enumerate(mana_runs):
            for symbol_index, pixmap in enumerate(run):
                width += pixmap.width()
                if symbol_index < len(run) - 1:
                    width += self.MANA_SYMBOL_SPACING
            if run_index < len(mana_runs) - 1:
                width += self.MANA_SEPARATOR_SPACING * 2 + delimiter_width
        return width

    def _draw_mana_symbols(self, painter, mana_runs: list[list[QPixmap]], rect: QRect, opt):
        x = rect.x()
        center_y = rect.center().y()
        delimiter_width = opt.fontMetrics.horizontalAdvance(self.MANA_DELIMITER)
        painter.setPen(opt.palette.color(QPalette.Text))
        for run_index, run in enumerate(mana_runs):
            for symbol_index, pixmap in enumerate(run):
                y = int(center_y - pixmap.height() / 2)
                painter.drawPixmap(x, y, pixmap)
                x += pixmap.width()
                if symbol_index < len(run) - 1:
                    x += self.MANA_SYMBOL_SPACING
            if run_index < len(mana_runs) - 1:
                x += self.MANA_SEPARATOR_SPACING
                delimiter_rect = QRect(
                    x,
                    rect.top(),
                    delimiter_width,
                    rect.height(),
                )
                painter.drawText(delimiter_rect, Qt.AlignVCenter | Qt.AlignLeft, self.MANA_DELIMITER)
                x += delimiter_width + self.MANA_SEPARATOR_SPACING


class PreviewGraphicsView(QGraphicsView):
    """Lightweight view that shows the PreviewZone from the shared scene."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: #0b0b0b; border-radius: 8px;")
        self._zone = None
        self._hidden_items: list[tuple[object, bool]] = []

    def set_preview_zone(self, zone):
        self._zone = zone
        self.refresh_view()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_view()

    def refresh_view(self):
        if not self.scene() or self._zone is None:
            return
        rect = self._zone.sceneBoundingRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.centerOn(rect.center())

    def paintEvent(self, event):
        scene = self.scene()
        if scene is None or self._zone is None:
            super().paintEvent(event)
            return
        allowed_items = {self._zone, *getattr(self._zone, "cards", [])}
        hidden: list[tuple[object, bool]] = []
        # Hide any other items whose bounding rect overlaps the preview zone area.
        zone_rect = self._zone.sceneBoundingRect()
        for item in scene.items(zone_rect):
            if item in allowed_items:
                continue
            if item.isVisible():
                hidden.append((item, True))
                item.setVisible(False)
        try:
            super().paintEvent(event)
        finally:
            for item, _ in hidden:
                item.setVisible(True)

class ListViewWidget(QWidget):
    """Deck list overlay that shows the current contents of the library zone."""

    _MIN_WIDTH = 240
    _MIN_HEIGHT = 220
    _PREVIEW_MIN_WIDTH = 260
    _PREVIEW_RATIO = 0.35
    _WIDTH_RATIO = 0.5
    _HEIGHT_RATIO = 0.7

    closeRequested = Signal()
    dragMoved = Signal(QPoint)
    cardSelected = Signal(int)
    previewAreaChanged = Signal()

    def __init__(
        self,
        entries: Sequence[Mapping[str, object]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("listViewWidget")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self._beleren_font = self._load_beleren_font()
        self._mana_renderer = ManaCostRenderer()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.setStyleSheet(
            """
            #listViewWidget {
                background: #121212;
                border: 2px solid #3f3a2f;
                border-radius: 12px;
                color: #f5f5f5;
            }
            #listViewWidget QLabel {
                color: #f5f5f5;
            }
            QListWidget {
                border: none;
                background: transparent;
                color: #f5f5f5;
            }
            QListWidget::viewport {
                background: #1b1b1b;
                border-radius: 8px;
            }
            QListWidget::item:selected {
                background: rgba(255, 255, 255, 0.15);
            }
            #deckHeader {
                background: #1b1b1b;
                border-radius: 8px;
            }
            #previewFrame {
                background: #1b1b1b;
                border: 2px solid #3f3a2f;
                border-radius: 12px;
            }
            QLineEdit {
                border: 2px solid #3f3a2f;
                border-radius: 6px;
                padding: 6px;
                background: #1e1e1e;
                color: #f5f5f5;
                selection-background-color: #f5f5f5;
                selection-color: #121212;
            }
            QPushButton {
                background: transparent;
                border: 1px solid transparent;
                color: #f5f5f5;
            }
            QPushButton:hover {
                border-color: #f5f5f5;
            }
            """
        )

        self._drag_offset = QPoint()
        self._dragging = False
        self._all_entries: list[Mapping[str, object]] = []

        header_bar = QWidget(self)
        header_bar.setObjectName("deckHeader")
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.header = QLabel("Library", header_bar)
        self.header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.close_button = QPushButton("X", header_bar)
        self.close_button.setFixedSize(24, 24)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.clicked.connect(self.closeRequested.emit)
        self.close_button.setCursor(Qt.PointingHandCursor)

        header_layout.addWidget(self.header, 1)
        header_layout.addWidget(self.close_button, 0, Qt.AlignRight)
        header_bar.setCursor(Qt.OpenHandCursor)
        header_bar.installEventFilter(self)
        self._header_bar = header_bar

        self.list_widget = QListWidget(self)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setItemDelegate(DeckListDelegate(self.list_widget))
        self.list_widget.currentItemChanged.connect(self._handle_selection_changed)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.preview_frame = QFrame(self)
        self.preview_frame.setObjectName("previewFrame")
        self.preview_frame.setMinimumWidth(self._PREVIEW_MIN_WIDTH)
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(12)
        self.preview_title = QLabel("Select a card", self.preview_frame)
        self.preview_title.setAlignment(Qt.AlignCenter)
        self.preview_title.setWordWrap(True)
        self.preview_view = PreviewGraphicsView(self.preview_frame)
        self.preview_view.setMinimumSize(self._PREVIEW_MIN_WIDTH - 20, 360)
        preview_layout.addWidget(self.preview_title)
        preview_layout.addWidget(self.preview_view, 1)

        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("Search...")
        self.search_box.textChanged.connect(self._apply_filter)

        self._apply_custom_font()
        layout.addWidget(header_bar)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(16)
        content.addWidget(self.list_widget, 1)
        content.addWidget(self.preview_frame, 0)
        layout.addLayout(content, 1)
        layout.addWidget(self.search_box)

        self.set_cards(entries or [])

    def resize_for_window(self, window_size: QSize):
        width = max(self._MIN_WIDTH, int(window_size.width() * self._WIDTH_RATIO))
        height = max(self._MIN_HEIGHT, int(window_size.height() * self._HEIGHT_RATIO))
        preview_width = max(self._PREVIEW_MIN_WIDTH, int(width * self._PREVIEW_RATIO))
        self.preview_frame.setFixedWidth(preview_width)
        list_width = max(240, width - preview_width - 48)
        self.list_widget.setMinimumWidth(list_width)
        self.setFixedSize(width, height)
        self.sync_preview_zone_view()
        self.previewAreaChanged.emit()

    def set_cards(self, entries: Sequence[Mapping[str, object]]):
        """Replace the list contents with the provided deck entries."""
        self._all_entries = list(entries or [])
        self._apply_filter(self.search_box.text())

    def card_ids(self) -> list[int]:
        """Return the card ids currently tracked by the widget."""
        ids: list[int] = []
        for idx in range(self.list_widget.count()):
            data = self.list_widget.item(idx).data(Qt.UserRole)
            if isinstance(data, dict) and isinstance(data.get("card_id"), int):
                ids.append(data["card_id"])
        return ids

    def _handle_selection_changed(self, current, previous):
        if current is None or not (current.flags() & Qt.ItemIsEnabled):
            return
        data = current.data(Qt.UserRole)
        if isinstance(data, dict) and isinstance(data.get("card_id"), int):
            self.cardSelected.emit(data["card_id"])

    def _apply_filter(self, text: str):
        query = (text or "").strip().lower()
        selected_id = None
        current = self.list_widget.currentItem()
        if current:
            data = current.data(Qt.UserRole)
            if isinstance(data, dict):
                selected_id = data.get("card_id")

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self._filtered_entries(query):
            name = str(entry.get("name") or "Unknown Card")
            mana_runs = self._mana_renderer.render_costs(entry.get("mana_costs"))
            payload = {
                "card_id": entry.get("card_id"),
                "name": name,
                "thumbnail": self._scaled_thumbnail(entry.get("thumbnail")),
                "mana_runs": mana_runs,
            }
            item = QListWidgetItem(name, self.list_widget)
            item.setData(Qt.UserRole, payload)
            if selected_id is not None and payload["card_id"] == selected_id:
                self.list_widget.setCurrentItem(item)
        if self.list_widget.count() == 0:
            placeholder = QListWidgetItem("No cards found", self.list_widget)
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsEnabled)
            self.list_widget.blockSignals(False)
            self._notify_layout_changed()
            return
        if self.list_widget.currentItem() is None:
            self.list_widget.setCurrentRow(0)
        self.list_widget.blockSignals(False)
        if self.list_widget.currentItem():
            self._handle_selection_changed(self.list_widget.currentItem(), None)
        self._notify_layout_changed()

    def _filtered_entries(self, query: str):
        if not query:
            return self._all_entries
        return [
            entry
            for entry in self._all_entries
            if query in str(entry.get("name") or "").lower()
        ]

    def _scaled_thumbnail(self, thumb) -> QPixmap | None:
        if not isinstance(thumb, QPixmap) or thumb.isNull():
            return None
        return thumb.scaled(
            DeckListDelegate.THUMB_SIZE,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def _notify_layout_changed(self):
        self.sync_preview_zone_view()
        self.previewAreaChanged.emit()

    def _max_entry_width(self) -> float:
        """Estimate the widest row so the widget can expand to avoid clipping."""
        delegate = self.list_widget.itemDelegate()
        font_metrics = self.list_widget.fontMetrics()
        max_width = 0.0
        for idx in range(self.list_widget.count()):
            item = self.list_widget.item(idx)
            if item is None:
                continue
            payload = item.data(Qt.UserRole) or {}
            name = str(payload.get("name") or item.text() or "")
            text_width = font_metrics.horizontalAdvance(name)
            mana_runs = payload.get("mana_runs") or []
            mana_width = 0
            if isinstance(delegate, DeckListDelegate):
                mana_width = delegate._mana_display_width(mana_runs, font_metrics)
            gap = DeckListDelegate.MANA_TEXT_GAP if (mana_width and text_width) else 0
            total = (
                DeckListDelegate.PADDING * 4
                + text_width
                + mana_width
                + gap
                + DeckListDelegate.THUMB_SIZE.width()
            )
            if total > max_width:
                max_width = total
        return max_width

    def set_preview_sources(self, scene, preview_zone):
        if self.preview_view.scene() is not scene:
            self.preview_view.setScene(scene)
        self.preview_view.set_preview_zone(preview_zone)
        self.sync_preview_zone_view()

    def sync_preview_zone_view(self):
        self.preview_view.refresh_view()

    def preview_area_rect(self) -> QRect:
        if not hasattr(self, "preview_view"):
            return QRect()
        local = self.preview_view.rect()
        top_left = self.preview_view.mapToGlobal(local.topLeft())
        bottom_right = self.preview_view.mapToGlobal(local.bottomRight())
        return QRect(top_left, bottom_right)

    def set_preview_title(self, title: str):
        self.preview_title.setText(title or "Select a card")

    def clear_preview_title(self):
        self.preview_title.setText("Select a card")

    def _load_beleren_font(self) -> QFont | None:
        font_path = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "fonts"
            / "Beleren"
            / "Beleren2016-Bold.ttf"
        )
        if not font_path.exists():
            return None
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id == -1:
            return None
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            return None
        font = QFont(families[0])
        base_size = self.font().pointSize()
        if base_size <= 0:
            base_size = 16
        font.setPointSize(base_size + 2)
        return font

    def _apply_custom_font(self) -> None:
        if not isinstance(self._beleren_font, QFont):
            return
        for widget in (
            self,
            self.header,
            self.list_widget,
            self.search_box,
            getattr(self, "preview_title", None),
        ):
            if widget is None:
                continue
            widget.setFont(self._beleren_font)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_preview_zone_view()
        self.previewAreaChanged.emit()

    def eventFilter(self, obj, event):
        if obj is self._header_bar:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._dragging = True
                self._header_bar.setCursor(Qt.ClosedHandCursor)
                self._drag_offset = event.position().toPoint()
                self._header_bar.grabMouse()
                return True
            if event.type() == QEvent.MouseMove and self._dragging:
                parent = self.parentWidget()
                global_pos = event.globalPosition().toPoint()
                parent_top_left = parent.mapFromGlobal(global_pos) if parent is not None else global_pos
                target = parent_top_left - self._drag_offset
                self.move(target)
                self.dragMoved.emit(self.pos())
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self._dragging:
                    self._dragging = False
                    self._header_bar.releaseMouse()
                    self._header_bar.setCursor(Qt.OpenHandCursor)
                    self.dragMoved.emit(self.pos())
                return True
        return super().eventFilter(obj, event)
