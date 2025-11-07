import queue
import numpy as np
import sys
import time

from PySide6.QtCore import (
    Qt, QPointF, QTimer, QRectF, QPoint
)
from PySide6.QtGui import QImage, QUndoStack, QPainter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFrame
)

from scene.board import BoardScene, BoardView
from model.board import BoardModel
from camera.camera import VirtualCamThread
from zones.zone import Zone
from card.card import Card

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BACK_IMAGE = BASE_DIR / "resources" / "Magic_card_back.jpg"


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
        self.view.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Freeze window size after initial layout
        QTimer.singleShot(0, self._sync_scene_rect_to_viewport)

        # Virtual cam setup
        self.frame_queue: queue.Queue = queue.Queue(maxsize=1)
        self.vcam_thread = VirtualCamThread(self.frame_queue)
        self.vcam_thread.start()
        self.streaming_enabled = True

        self._last_capture_time = 0.0
        self._capture_interval = 1.0 / 60.0  # seconds

        # Populate items
        self.hand_zone = Zone("hand", slot_h=160, padding=16,
                              orientation="horizontal", hide_cards=True)
        self.scene.add_zone(self.hand_zone, QPointF(0, 0))

        cols = 4
        spacing = QPointF(150, 120)
        start = QPointF(40, 40)
        for i in range(2):
            c = Card(f"Card {i+1}", image_path=r'scryfall-cache\\9ED\\100\\front.png', w=745*0.25, h= 1040*0.25, visible=True)
            c.set_card_back(BACK_IMAGE)
            pos = start + QPointF((i % cols)*spacing.x(),
                                   (i // cols)*spacing.y())
            self.scene.add_card(c, pos)

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

    def _sync_scene_rect_to_viewport(self):
        view_src = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        margin = 12.0
        self.scene.setSceneRect(view_src.adjusted(-margin, -margin, margin, margin))
        self._layout_hand_zone()

    def _layout_hand_zone(self):
        if not hasattr(self, "hand_zone"):
            return
        view_rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        if view_rect.isNull() or view_rect.width() <= 0:
            return
        self.hand_zone.set_width(view_rect.width())
        self.hand_zone.set_bottom_anchor(view_rect.left(), view_rect.bottom())

    def _wrap_release(self, original_release, card: Card):
        def handler(ev):
            original_release(ev)
            if ev.button() == Qt.LeftButton:
                self.scene.drop_released(card)
                self._on_card_action()
        return handler

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
