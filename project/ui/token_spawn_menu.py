from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal, QEvent
from PySide6.QtGui import QFont, QFontDatabase, QPixmap, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from spawner.token_descriptor import TokenDescriptor


class TokenProxyViewport(QGraphicsView):
    """Scrollable view that hosts the token proxy scene."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        # Anchor content to the top-left to minimize wasted horizontal space.
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet("background: #0b0b0b; border-radius: 8px;")


class TokenProxyItem(QGraphicsObject):
    """Proxy that paints a token image and delegates drag lifecycle via signals."""

    dragStarted = Signal(object, object)  # self, event
    dragMoved = Signal(object, object)
    dragReleased = Signal(object, object)

    def __init__(self, token: TokenDescriptor, width: float, height: float, parent=None):
        super().__init__(parent)
        self.token = token
        self._size = (float(width), float(height))
        self._pixmap = self._load_pixmap(token.image_path, int(width), int(height))
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setAcceptHoverEvents(False)

    @staticmethod
    def _load_pixmap(path: str, width: int, height: int) -> Optional[QPixmap]:
        if not path:
            return None
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return None
        return pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

    def boundingRect(self):
        return QRectF(0, 0, self._size[0], self._size[1])

    def paint(self, painter: QPainter, option, widget=None):
        if isinstance(self._pixmap, QPixmap) and not self._pixmap.isNull():
            painter.drawPixmap(self.boundingRect(), self._pixmap, self._pixmap.rect())
        else:
            painter.fillRect(self.boundingRect(), option.palette.window())

    def mousePressEvent(self, event):
        self.dragStarted.emit(self, event)
        event.accept()

    def mouseMoveEvent(self, event):
        self.dragMoved.emit(self, event)
        event.accept()

    def mouseReleaseEvent(self, event):
        self.dragReleased.emit(self, event)
        event.accept()


class TokenProxyController(QWidget):
    """
    Builds token proxies and spawns real cards on drag, using board view/scene.
    """

    def __init__(
        self,
        board_view,
        board_scene,
        spawner,
        *,
        on_card_created: Optional[Callable[[object], None]] = None,
        columns: int = 2,
        parent=None,
    ):
        super().__init__(parent)
        self.board_view = board_view
        self.board_scene = board_scene
        self.spawner = spawner
        self.on_card_created = on_card_created
        self._columns = max(1, columns)
        self._gap = 24.0
        self._card_width = float(getattr(spawner, "_default_width", 745.0 * 0.33))
        self._card_height = float(getattr(spawner, "_default_height", 1040.0 * 0.33))
        self.scene = QGraphicsScene(self)
        self._tokens: list[TokenDescriptor] = []
        self._active: dict[str, object] = {}

    def set_tokens(self, tokens: Iterable[TokenDescriptor]):
        self._tokens = list(tokens or [])
        self.refresh()

    def refresh(self):
        self.scene.clear()
        if not self._tokens:
            self.scene.setSceneRect(QRectF(0, 0, self._card_width, self._card_height))
            return

        x0 = 0.0
        y0 = 0.0
        max_w = 0.0
        max_h = 0.0
        for idx, token in enumerate(self._tokens):
            col = idx % self._columns
            row = idx // self._columns
            x = x0 + col * (self._card_width + self._gap)
            y = y0 + row * (self._card_height + self._gap)
            proxy = TokenProxyItem(token, self._card_width, self._card_height)
            proxy.setPos(QPointF(x, y))
            proxy.dragStarted.connect(self._handle_press)
            proxy.dragMoved.connect(self._handle_move)
            proxy.dragReleased.connect(self._handle_release)
            self.scene.addItem(proxy)
            max_w = max(max_w, x + self._card_width)
            max_h = max(max_h, y + self._card_height)

        padding = 20
        self.scene.setSceneRect(QRectF(0, 0, max_w + padding, max_h + padding))

    # --- Drag lifecycle ---
    def _handle_press(self, proxy: TokenProxyItem, event):
        if self._active:
            return
        try:
            card = self.spawner.spawn_token(proxy.token, width=self._card_width, height=self._card_height)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[TokenProxyController] Failed to spawn token {proxy.token.id}: {exc}")
            return
        scene_pos = self._map_to_board_scene(event.screenPos())
        offset = event.pos()
        target = scene_pos - offset if scene_pos is not None else QPointF(0, 0)
        self.board_scene.add_card(card, target)
        if self.on_card_created:
            self.on_card_created(card)
        self.board_scene.clearSelection()
        card.setSelected(True)
        self._active = {"proxy": proxy, "card": card, "offset": offset}
        proxy.setOpacity(0.25)

    def _handle_move(self, proxy: TokenProxyItem, event):
        if not self._active or self._active.get("proxy") is not proxy:
            return
        scene_pos = self._map_to_board_scene(event.screenPos())
        if scene_pos is None:
            return
        card = self._active.get("card")
        offset: QPointF = self._active.get("offset", QPointF())
        if card:
            card.setPos(scene_pos - offset)

    def _handle_release(self, proxy: TokenProxyItem, event):
        if not self._active or self._active.get("proxy") is not proxy:
            return
        scene_pos = self._map_to_board_scene(event.screenPos())
        card = self._active.get("card")
        offset: QPointF = self._active.get("offset", QPointF())
        if card and scene_pos is not None:
            card.setPos(scene_pos - offset)
        proxy.setOpacity(1.0)
        if card:
            self.board_scene.drop_released(card)
        self._active = {}

    def _map_to_board_scene(self, screen_pos) -> Optional[QPointF]:
        if self.board_view is None:
            return None
        if hasattr(screen_pos, "toPoint"):
            global_point = screen_pos.toPoint()
        else:
            global_point = screen_pos
        viewport_point = self.board_view.viewport().mapFromGlobal(global_point)
        return self.board_view.mapToScene(viewport_point)


class TokenSpawnMenu(QWidget):
    """Floating menu for spawning tokens via proxies."""

    closeRequested = Signal()
    dragMoved = Signal(QPoint)
    categoryChanged = Signal(str)

    _CATEGORIES = [
        ("common", "Common"),
        ("deck", "Deck"),
        ("mechanics", "Mechanics"),
        ("dungeon", "Dungeon"),
        ("custom", "Custom"),
    ]

    def __init__(self, board_view, board_scene, spawner, *, on_card_created=None, parent=None):
        super().__init__(parent)
        self.setObjectName("tokenSpawnMenu")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self._beleren_font = self._load_beleren_font()
        self._dragging = False
        self._drag_offset = QPoint()
        self._tokens_by_category: dict[str, list[TokenDescriptor]] = {}

        self.setStyleSheet(
            """
            #tokenSpawnMenu {
                background: #121212;
                border: 2px solid #3f3a2f;
                border-radius: 12px;
                color: #f5f5f5;
            }
            #tokenSpawnMenu QLabel {
                color: #f5f5f5;
            }
            QListWidget {
                background: #1b1b1b;
                border: 1px solid #3f3a2f;
                border-radius: 8px;
                color: #f5f5f5;
                padding: 6px;
            }
            QListWidget::item {
                padding: 10px 12px;
            }
            QListWidget::item:selected {
                background: rgba(255, 255, 255, 0.12);
                border: 1px solid #f5f5f5;
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.title_label = QLabel("Token Spawner", header)
        self.close_button = QPushButton("X", header)
        self.close_button.setFixedSize(24, 24)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.clicked.connect(self.closeRequested.emit)

        header_layout.addWidget(self.title_label, 1)
        header_layout.addWidget(self.close_button, 0)
        header.setCursor(Qt.OpenHandCursor)
        header.installEventFilter(self)
        self._header_bar = header

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)

        self.nav_list = QListWidget(body)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setSelectionMode(QListWidget.SingleSelection)
        self.nav_list.setFocusPolicy(Qt.NoFocus)
        for key, label in self._CATEGORIES:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.nav_list.addItem(item)
        self.nav_list.setFixedWidth(180)

        self.viewport = TokenProxyViewport(body)
        self.controller = TokenProxyController(
            board_view,
            board_scene,
            spawner,
            on_card_created=on_card_created,
            columns=2,
            parent=self,
        )
        self.viewport.setScene(self.controller.scene)

        body_layout.addWidget(self.nav_list, 0)
        body_layout.addWidget(self.viewport, 1)

        layout.addWidget(header)
        layout.addWidget(body, 1)

        self.nav_list.currentItemChanged.connect(self._handle_category_changed)
        self._apply_custom_font()
        if self.nav_list.count() > 0:
            self.nav_list.setCurrentRow(0)

    def set_tokens_for_category(self, category: str, tokens: Iterable[TokenDescriptor]):
        self._tokens_by_category[category] = list(tokens or [])
        current = self.current_category()
        if current == category:
            self.controller.set_tokens(self._tokens_by_category.get(category, []))

    def current_category(self) -> Optional[str]:
        item = self.nav_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def refresh_current_category(self):
        category = self.current_category()
        if category is None:
            return
        tokens = self._tokens_by_category.get(category, [])
        self.controller.set_tokens(tokens)

    def resize_for_window(self, size):
        # Prefer a tighter width: nav + two cards + small padding.
        margins = self.layout().contentsMargins()
        body_layout = self.layout().itemAt(1).layout() if self.layout().count() > 1 else None
        body_spacing = body_layout.spacing() if body_layout is not None else 10
        nav_w = self.nav_list.width() or 180
        cards_w = (
            self.controller._columns * self.controller._card_width
            + (self.controller._columns - 1) * self.controller._gap
            + 32  # padding for scene/scrollbar
        )
        desired_w = (
            margins.left()
            + margins.right()
            + nav_w
            + body_spacing
            + int(cards_w)
        )
        max_width = int(size.width() * 0.55)
        target_w = min(int(desired_w), max_width)
        max_height = int(size.height() * 0.8)
        self.setFixedSize(target_w, max_height)

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

    def _handle_category_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        if current is None:
            return
        category = current.data(Qt.UserRole)
        if category is None:
            return
        self.controller.set_tokens(self._tokens_by_category.get(category, []))
        self.categoryChanged.emit(category)

    def _load_beleren_font(self) -> Optional[QFont]:
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
        for widget in (self, self._header_bar, self.nav_list, self.title_label):
            widget.setFont(self._beleren_font)
