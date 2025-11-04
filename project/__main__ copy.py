import queue
import numpy as np
import sys

from PySide6.QtCore import (
    Qt, QPointF, QTimer
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

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PySide6 Cards + Virtual Camera Skeleton")
        self.resize(1000, 700)

        self.model = BoardModel()
        self.undo = QUndoStack(self)
        self.scene = BoardScene(self.model, self.undo)
        self.view = BoardView(self.scene)
        self.setCentralWidget(self.view)

        # Virtual cam setup
        self.frame_queue: queue.Queue = queue.Queue(maxsize=1)
        self.vcam_thread = VirtualCamThread(self.frame_queue)
        self.vcam_thread.start()

        # Start with streaming disabled to test if it's causing the freeze
        # Set to True to enable virtual camera streaming
        self.streaming_enabled = True

        # Timer-based frame capture at 15 FPS (low frequency for continuous streaming)
        # This is supplemented by event-driven captures for smooth interaction feedback
        # Only runs when streaming_enabled is True
        self._capture_interval_ms = int(1000 / 15)  # 66ms - much less demanding
        self.capture_timer = QTimer(self)
        self.capture_timer.setSingleShot(False)  # Regular interval timer
        self.capture_timer.timeout.connect(self.capture_and_queue_frame)

        # Start the capture loop
        self.capture_timer.start(self._capture_interval_ms)

        # Populate items
        zone = Zone("zone_A")
        self.scene.add_zone(zone, QPointF(760, 40))

        cols = 4
        spacing = QPointF(150, 120)
        start = QPointF(40, 40)
        for i in range(2):
            # Test without images first to isolate the freezeissue
            c = Card(f"Card {i+1}", image_path=r'scryfall-cache\\9ED\\100\\front.png', w=745, h= 1040)
            #c = Card(f"Card {i+1}")  # No image for now
            pos = start + QPointF((i % cols)*spacing.x(),
                                   (i // cols)*spacing.y())
            self.scene.add_card(c, pos)

        # Hook release events
        for item in self.scene.items():
            if isinstance(item, Card):
                orig = item.mouseReleaseEvent
                item.mouseReleaseEvent = self._wrap_release(orig, item)

    def _wrap_release(self, original_release, card: Card):
        def handler(ev):
            original_release(ev)
            if ev.button() == Qt.LeftButton:
                self.scene.drop_released(card)
        return handler

    def capture_and_queue_frame(self):
        # Skip capture if streaming is disabled or view isn't ready
        if not self.streaming_enabled:
            return
        if not self.view or not self.view.isVisible():
            return

        try:
            pixmap = self.view.grab()
            if pixmap.isNull():
                return

            qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)

            target_w = self.vcam_thread.width
            target_h = self.vcam_thread.height

            if qimg.width() != target_w or qimg.height() != target_h:
                qimg = qimg.scaled(target_w,
                                    target_h,
                                    Qt.IgnoreAspectRatio,
                                    Qt.FastTransformation)

            # w = qimg.width()
            # h = qimg.height()
            # ptr = qimg.constBits()
            # arr = np.frombuffer(ptr, dtype=np.uint8, count=h * qimg.bytesPerLine())
            # arr = arr.reshape((h, qimg.bytesPerLine()))
            # arr = arr[:, : (w * 3)]
            # arr = arr.reshape((h, w, 3))

            w = qimg.width()
            h = qimg.height()
            # Deep-copy bytes so the background thread does not read freed memory.
            ptr = qimg.constBits()
            ptr.setsize(h * qimg.bytesPerLine())
            buf = bytes(ptr)  # owns its memory
            arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, qimg.bytesPerLine()))[:, : (w * 3)]
            arr = arr.reshape((h, w, 3))

            try:
                self.frame_queue.put_nowait(arr)
            except queue.Full:
                # drop if queue full
                pass
        except Exception:
            # Silently ignore capture errors to prevent UI freeze
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