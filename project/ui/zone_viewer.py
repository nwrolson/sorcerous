from __future__ import annotations

from typing import Sequence

from pathlib import Path

from PySide6.QtCore import Qt, QSize, QPoint, QEvent, Signal, QRect
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from zones.zone import Zone


class ZoneViewport(QGraphicsView):
    """Dedicated view that renders the proxy scene."""

    widthChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet("background: #0b0b0b; border-radius: 8px;")
        self._column_width = 260

    def set_column_width(self, width: int):
        width = max(200, int(width))
        if self._column_width == width:
            return
        self._column_width = width
        self.setFixedWidth(self._column_width)
        self.widthChanged.emit(self._column_width)

    def setScene(self, scene):
        super().setScene(scene)
        self.setFixedWidth(self._column_width)


class ZoneViewerMenu(QWidget):
    """Floating menu that shows hidden zones via the proxy scene."""

    _MIN_HEIGHT = 260
    _HEIGHT_RATIO = 0.75

    closeRequested = Signal()
    dragMoved = Signal(QPoint)
    zoneChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("zoneViewerMenu")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self._beleren_font = self._load_beleren_font()
        self._zones: dict[str, Zone] = {}
        self._current_zone_id: str | None = None
        self._dragging = False
        self._drag_offset = QPoint()
        self._scrollbar_padding = 16

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        margins = layout.contentsMargins()
        self._horizontal_padding = margins.left() + margins.right()

        self.setStyleSheet(
            """
            #zoneViewerMenu {
                background: #121212;
                border: 2px solid #3f3a2f;
                border-radius: 12px;
                color: #f5f5f5;
            }
            #zoneViewerMenu QLabel {
                color: #f5f5f5;
            }
            QComboBox {
                background: #1e1e1e;
                border: 1px solid #3f3a2f;
                border-radius: 4px;
                padding: 4px 8px;
                color: #f5f5f5;
            }
            QComboBox QAbstractItemView {
                background: #1e1e1e;
                selection-background-color: rgba(255, 255, 255, 0.15);
                border-radius: 4px;
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

        header = QWidget(self)
        header.setObjectName("zoneViewerHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.title_label = QLabel("Zone Viewer", header)
        self.title_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self.zone_selector = QComboBox(header)
        self.zone_selector.setFocusPolicy(Qt.NoFocus)
        self.zone_selector.currentIndexChanged.connect(self._handle_zone_selected)

        self.close_button = QPushButton("X", header)
        self.close_button.setFixedSize(24, 24)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.clicked.connect(self.closeRequested.emit)

        header_layout.addWidget(self.title_label, 1)
        header_layout.addWidget(self.zone_selector, 0)
        header_layout.addWidget(self.close_button, 0)
        header.setCursor(Qt.OpenHandCursor)
        header.installEventFilter(self)
        self._header_bar = header

        self.viewport = ZoneViewport(self)
        self.viewport.widthChanged.connect(self._handle_viewport_width_changed)

        layout.addWidget(header)
        layout.addWidget(self.viewport, 1)

        self._apply_custom_font()

    def set_scene(self, scene):
        self.viewport.setScene(scene)
        self._handle_viewport_width_changed(self.viewport.width())

    def set_column_width(self, width: int):
        self.viewport.set_column_width(width)

    def viewport_rect_global(self) -> QRect:
        view = self.viewport.viewport()
        if view is None:
            return QRect()
        rect = view.rect()
        top_left = view.mapToGlobal(rect.topLeft())
        bottom_right = view.mapToGlobal(rect.bottomRight())
        return QRect(top_left, bottom_right)

    def resize_for_window(self, window_size: QSize):
        target_h = int(window_size.height() * self._HEIGHT_RATIO)
        height = max(self._MIN_HEIGHT, target_h)
        self.setFixedHeight(height)

    def set_available_zones(self, zones: Sequence[Zone]):
        current = self._current_zone_id
        mapping: dict[str, Zone] = {}
        self.zone_selector.blockSignals(True)
        self.zone_selector.clear()
        for zone in zones:
            if not isinstance(zone, Zone):
                continue
            zone_id = getattr(zone, "zone_id", None)
            if not zone_id:
                continue
            mapping[zone_id] = zone
            display = getattr(zone, "zone_name", None) or zone_id.replace("_", " ").title()
            self.zone_selector.addItem(display, zone_id)
        self.zone_selector.blockSignals(False)
        self._zones = mapping
        if current and current in mapping:
            self.show_zone(current)
        elif mapping:
            first_id = next(iter(mapping))
            self.show_zone(first_id)
        else:
            self._current_zone_id = None

    def show_zone(self, zone_id: str):
        zone = self._zones.get(zone_id)
        if zone is None:
            return
        if self._current_zone_id != zone_id:
            index = self.zone_selector.findData(zone_id)
            if index >= 0 and index != self.zone_selector.currentIndex():
                self.zone_selector.blockSignals(True)
                self.zone_selector.setCurrentIndex(index)
                self.zone_selector.blockSignals(False)
            self._current_zone_id = zone_id
            self.title_label.setText(f"Zone: {zone_id.replace('_', ' ').title()}")
            self.zoneChanged.emit(zone_id)

    def current_zone_id(self) -> str | None:
        return self._current_zone_id

    def eventFilter(self, obj, event):
        if obj is self._header_bar:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._dragging = True
                self._drag_offset = event.position().toPoint()
                self._header_bar.setCursor(Qt.ClosedHandCursor)
                self._header_bar.grabMouse()
                return True
            if event.type() == QEvent.MouseMove and self._dragging:
                parent = self.parentWidget()
                global_pos = event.globalPosition().toPoint()
                parent_pos = parent.mapFromGlobal(global_pos) if parent is not None else global_pos
                target = parent_pos - self._drag_offset
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

    def _handle_zone_selected(self, index: int):
        if index < 0:
            return
        zone_id = self.zone_selector.itemData(index)
        if not zone_id:
            return
        self.show_zone(zone_id)

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

    def _apply_custom_font(self):
        if not isinstance(self._beleren_font, QFont):
            return
        for widget in (self, self._header_bar, self.zone_selector, self.title_label):
            widget.setFont(self._beleren_font)

    def _handle_viewport_width_changed(self, width: int):
        total = width + getattr(self, "_horizontal_padding", 0) + self._scrollbar_padding
        self.setFixedWidth(total)
