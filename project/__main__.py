import queue
import numpy as np
import sys
import time

from PySide6.QtCore import (
    Qt,
    QPointF,
    QTimer,
    QRectF,
    QPoint,
    QEasingCurve,
    QPropertyAnimation,
    Slot,
)
from PySide6.QtGui import QImage, QUndoStack, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QFrame,
    QGraphicsOpacityEffect,
    QGraphicsView,
    QWidget,
)

from scene.board import BoardScene, BoardView
from model.board import BoardModel
from camera.camera import VirtualCamThread
from zones.hand import HandZone
from zones.library import LibraryZone
from card.card import Card
from ui.load_menu import LoadMenu
from cache.cache import ScryfallImageCache
from spawner.spawner import CardSpawner

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class LoadingSpinner(QWidget):
    """Simple brass-eye spinner that rotates until dismissed."""

    def __init__(self, icon_path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)

        pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            size = 96
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setPen(Qt.white)
            painter.drawEllipse(4, 4, size - 8, size - 8)
            painter.end()
        self._pixmap = pixmap.scaled(
            96,
            96,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.setFixedSize(self._pixmap.size())

        self._angle = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)

        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(500)
        self._fade.setEasingCurve(QEasingCurve.InOutQuad)
        self._fade.finished.connect(self._on_fade_finished)

        self.hide()

    def start(self):
        self._fade.stop()
        self._effect.setOpacity(1.0)
        self._angle = 0.0
        self._timer.start()
        self.show()
        self.raise_()
        self.update()

    def finish(self, immediate: bool = False):
        if not self.isVisible():
            return
        if immediate:
            self._timer.stop()
            self.hide()
            self._effect.setOpacity(0.0)
            return
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _tick(self):
        self._angle = (self._angle + 3.0) % 360.0
        self.update()

    def _on_fade_finished(self):
        if self._effect.opacity() <= 0.0:
            self._timer.stop()
            self.hide()

    def paintEvent(self, event):
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._angle)
        half_w = self._pixmap.width() / 2
        half_h = self._pixmap.height() / 2
        painter.drawPixmap(
            int(-half_w),
            int(-half_h),
            self._pixmap,
        )
        painter.end()

# -------- Main Window --------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sorcerous")
        self.resize(1920, 1080)

        self.model = BoardModel()
        self.undo = QUndoStack(self)
        self.scene = BoardScene(self.model, self.undo)
        self.view = BoardView(self.scene)
        self.setCentralWidget(self.view)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setViewportMargins(0, 0, 0, 0)
        self.view.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.view.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.load_menu = LoadMenu(self)
        self.load_menu.raise_()
        spinner_icon = BASE_DIR / "resources" / "brass-eye.svg"
        self.loading_spinner = LoadingSpinner(spinner_icon, self)
        self.loading_spinner.hide()

        # Freeze window size after initial layout
        QTimer.singleShot(0, self._sync_scene_rect_to_viewport)
        QTimer.singleShot(0, self._position_load_menu)
        QTimer.singleShot(0, self._position_loading_spinner)

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
        self.scene.add_zone(self.hand_zone, QPointF(0, 0))
        self.scene.add_zone(self.library_zone, QPointF(0, 0))

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
            lambda _: self.loading_spinner.finish()
        )
        self.load_menu.importRequested.connect(self._on_import_requested)

        # cols = 4
        # spacing = QPointF(150, 120)
        # start = QPointF(40, 40)
        # for i in range(2):
        #     c = self.spawner.spawn_card(
        #         "9ed",
        #         "100",
        #         width=745 * 0.25,
        #         height=1040 * 0.25,
        #     )
        #     pos = start + QPointF((i % cols)*spacing.x(),
        #                            (i // cols)*spacing.y())
        #     self.scene.add_card(c, pos)

        # Hook release events
        for item in self.scene.items():
            if isinstance(item, Card):
                orig = item.mouseReleaseEvent
                item.mouseReleaseEvent = self._wrap_release(orig, item)

        # Hook drag move or position changed to capture frame
        for item in self.scene.items():
            if isinstance(item, Card):
                item.moved.connect(lambda pos, c=item: self._on_card_action())

    def _qimage_to_rgb(self, img: QImage) -> np.ndarray:
        # Ensure RGBA8888
        if img.format() != QImage.Format.Format_RGBA8888:
            img = img.convertToFormat(QImage.Format.Format_RGBA8888)

        h, w = img.height(), img.width()
        expected = w * h * 4

        ptr = img.bits()  # PySide6: memoryview; older PySide: sip.voidptr

        # # Backward compatibility: only call setsize if available
        # try:
        #     ptr.setsize(expected)  # older PySide only
        # except AttributeError:
        #     pass  # PySide6 memoryview does not need setsize

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

    # def _render_scene_for_camera(self, out_w: int, out_h: int) -> np.ndarray:
    #     # 1) Visible area of the view in scene coordinates
    #     view_src = self.view.mapToScene(self.view.viewport().rect()).boundingRect()

    #     # 2) Render that source rect into an intermediate offscreen image
    #     #    Make the offscreen size proportional to the visible rect to avoid
    #     #    fractional scaling during the scene render.
    #     src_w = max(1, int(round(view_src.width())))
    #     src_h = max(1, int(round(view_src.height())))
    #     off = QImage(src_w, src_h, QImage.Format.Format_RGBA8888)
    #     off.setDevicePixelRatio(1.0)   # deterministic pixel math on all DPRs
    #     off.fill(Qt.black)

    #     prev = Card.render_target
    #     Card.render_target = "camera"
    #     try:
    #         p = QPainter(off)
    #         # Render the visible scene rect to a 1:1 target image
    #         self.scene.render(p, QRectF(0, 0, src_w, src_h), view_src)
    #         p.end()
    #     finally:
    #         Card.render_target = prev

    #     # 3) Scale to the camera output while FILLING the frame
    #     #    KeepAspectRatioByExpanding ensures no letterboxing, then we crop.
    #     scaled = off.scaled(out_w, out_h,
    #                         Qt.AspectRatioMode.KeepAspectRatioByExpanding,
    #                         Qt.TransformationMode.SmoothTransformation)

    #     # 4) Bottom-align crop to exact camera size
    #     x = max(0, (scaled.width()  - out_w) // 2)   # center horizontally
    #     y = max(0, scaled.height() - out_h)          # align bottoms
    #     final_qimg = scaled.copy(x, y, out_w, out_h)

    #     return self._qimage_to_rgb(final_qimg)


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

    def _wrap_release(self, original_release, card: Card):
        def handler(ev):
            original_release(ev)
            if ev.button() == Qt.LeftButton:
                self.scene.drop_released(card)
                self._on_card_action()
        return handler

    @Slot(str)
    def _on_import_requested(self, payload: str) -> None:
        accepted = self.spawner.handle_import_signal(payload)
        if accepted:
            self._position_loading_spinner()
            self.loading_spinner.start()
        else:
            self.loading_spinner.finish(immediate=True)

    def _handle_spawned_cards(self, cards: list[Card]) -> None:
        if not cards:
            return
        cols = 5
        spacing = QPointF(150, 210)
        start = QPointF(40, 40)
        existing_cards = sum(1 for item in self.scene.items() if isinstance(item, Card))
        for offset, card in enumerate(cards):
            idx = existing_cards + offset
            col = idx % cols
            row = idx // cols
            pos = QPointF(
                start.x() + col * spacing.x(),
                start.y() + row * spacing.y(),
            )
            self.scene.add_card(card, pos)
        self._on_card_action()

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
