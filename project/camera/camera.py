import threading
import queue
import time

import numpy as np

import pyvirtualcam
from pyvirtualcam import PixelFormat

class VirtualCamThread(threading.Thread):
    def __init__(self,
                 frame_queue: queue.Queue,
                 width: int = 1920,
                 height: int = 1080,
                 fps: float = 60.0):
        super().__init__(daemon=True)
        self.frame_queue = frame_queue
        self.width = width
        self.height = height
        self.fps = fps
        self._stop_event = threading.Event()
        self._restart_event = threading.Event()
        self._cam: pyvirtualcam.Camera | None = None

    def _open_camera(self):
        try:
            print(f"[VirtualCamThread] Attempt open: width={self.width}, height={self.height}, fps={self.fps}")
            self._cam = pyvirtualcam.Camera(
                width=self.width,
                height=self.height,
                fps=self.fps,
                fmt=PixelFormat.RGB,
                backend='obs',
                device='OBS Virtual Camera'
            )
            print(f"[VirtualCamThread] Device name: {self._cam.device}, backend: {self._cam.backend}")
            return True
        except Exception as e:
            print(f"[VirtualCamThread] Failed to open virtual camera: {e}")
            return False

    def _close_camera(self):
        if self._cam:
            try:
                print("[VirtualCamThread] Closing camera")
                self._cam.close()
            except Exception as e:
                print(f"[VirtualCamThread] Error closing camera: {e}")
        self._cam = None

    def run(self):
        last_sent_time = 0.0
        min_interval = 1.0 / self.fps  # target min interval
        while not self._stop_event.is_set():
            if self._cam is None or self._restart_event.is_set():
                self._close_camera()
                time.sleep(0.5)
                if not self._open_camera():
                    time.sleep(1.0)
                    continue
                last_sent_time = time.time()

            try:
                frame = self.frame_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            now = time.time()
            if now - last_sent_time < min_interval:
                # skip this frame because too soon
                continue

            if frame is None or not isinstance(frame, np.ndarray):
                continue
            h0, w0 = frame.shape[:2]
            if h0 == 0 or w0 == 0:
                continue
            if frame.dtype != np.uint8:
                frame = frame.astype(np.uint8)

            try:
                self._cam.send(frame)
            except Exception as e:
                print(f"[VirtualCamThread] Camera send failed: {e}")
                self._restart_event.set()
                continue

            try:
                self._cam.sleep_until_next_frame()
            except Exception as e:
                print(f"[VirtualCamThread] sleep_until_next_frame failed: {e}")
                self._restart_event.set()

            last_sent_time = time.time()

        self._close_camera()
        print("[VirtualCamThread] Thread exiting")

    def stop(self):
        self._stop_event.set()

    def update_params(self, width: int, height: int, fps: float):
        if width != self.width or height != self.height or fps != self.fps:
            self.width = width
            self.height = height
            self.fps = fps
            print(f"[VirtualCamThread] Params updated to: width={width}, height={height}, fps={fps}")
            self._restart_event.set()