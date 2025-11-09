from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QPropertyAnimation, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class LoadingSpinner(QWidget):
    """Simple brass-eye spinner that rotates until dismissed."""

    def __init__(self, icon_path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background-color: transparent;")

        pixmap = self._load_icon_pixmap(icon_path)
        if pixmap is None or pixmap.isNull():
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

        self._fade_delay = QTimer(self)
        self._fade_delay.setSingleShot(True)
        self._fade_delay.timeout.connect(self._begin_fade_out)

        self.hide()

    def start(self):
        self._fade.stop()
        self._fade_delay.stop()
        self._effect.setOpacity(1.0)
        self._angle = 0.0
        self._timer.start()
        self.show()
        self.raise_()
        self.update()

    def finish(self, immediate: bool = False):
        if not self.isVisible():
            return
        self._timer.stop()
        if immediate:
            self._fade_delay.stop()
            self._fade.stop()
            self.hide()
            self._effect.setOpacity(0.0)
            return
        self._fade_delay.stop()
        self._fade_delay.start(500)

    def _begin_fade_out(self):
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

    def _load_icon_pixmap(self, icon_path: Path) -> QPixmap | None:
        try:
            if icon_path.suffix.lower() == ".svg" and icon_path.exists():
                renderer = QSvgRenderer(str(icon_path))
                if renderer.isValid():
                    target_size = 128
                    pixmap = QPixmap(target_size, target_size)
                    pixmap.fill(Qt.transparent)
                    painter = QPainter(pixmap)
                    renderer.render(painter, QRectF(0, 0, target_size, target_size))
                    painter.end()
                    return pixmap
        except Exception as exc:  # pragma: no cover
            print(f"[LoadingSpinner] Failed to render SVG: {exc}")
        pixmap = QPixmap(str(icon_path))
        return pixmap if not pixmap.isNull() else None
