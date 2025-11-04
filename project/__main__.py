import queue
import numpy as np
import sys
import time

from PySide6.QtCore import (
    Qt, QPointF, QTimer, QRectF
)
from PySide6.QtGui import QImage, QUndoStack
from PySide6.QtWidgets import (
    QApplication, QMainWindow
)

from scene.board import BoardScene, BoardView
from model.board import BoardModel
from camera.camera import VirtualCamThread
from zones.zone import Zone
from card.card import Card

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
        zone = Zone("zone_A")
        self.scene.add_zone(zone, QPointF(760, 40))

        cols = 4
        spacing = QPointF(150, 120)
        start = QPointF(40, 40)
        for i in range(2):
            c = Card(f"Card {i+1}", image_path=r'scryfall-cache\\9ED\\100\\front.png', w=745*0.5, h= 1040*0.5)
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

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._sync_scene_rect_to_viewport()

    def _sync_scene_rect_to_viewport(self):
        vp = self.view.viewport()
        w, h = vp.width(), vp.height()
        self.scene.setSceneRect(QRectF(0, 0, w, h))

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

    def capture_and_queue_frame(self):
        pixmap = self.view.grab()
        qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)

        target_w = self.vcam_thread.width
        target_h = self.vcam_thread.height

        if qimg.width() != target_w or qimg.height() != target_h:
            qimg = qimg.scaled(
                target_w, target_h, Qt.IgnoreAspectRatio, Qt.FastTransformation
            )

        w = qimg.width()
        h = qimg.height()
        buf = qimg.bits().tobytes()
        arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, qimg.bytesPerLine()))[:, : (w * 3)]
        arr = arr.reshape((h, w, 3))

        try:
            self.frame_queue.put_nowait(arr)
        except queue.Full:
            # drop if queue full
            pass

    def set_stream_params(self, width: int, height: int, fps: float):
        self.vcam_thread.update_params(width, height, fps)

    def closeEvent(self, ev):
        self.vcam_thread.stop()
        self.vcam_thread.join(timeout=2.0)
        super().closeEvent(ev)

# -------- Entry Point --------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())