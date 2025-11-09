from PySide6.QtCore import (
    Qt, QRectF, QPointF, Signal, QVariantAnimation, QEasingCurve,
    QSequentialAnimationGroup, QPropertyAnimation, Property
)
from PySide6.QtWidgets import QGraphicsObject
from PySide6.QtGui import QBrush, QColor, QPen, QPixmap, QPainter, QTransform

HOVER_SCALE_FACTOR = 1.1
HOVER_ANIMATION_DURATION_MS = 100
HOVER_OSCILLATION_OFFSET = 8
HOVER_OSCILLATION_DURATION_MS = 1200
HOVER_SHADOW_COLOR = QColor(0, 0, 0, 120)
HOVER_SHADOW_OFFSET_X = 8
HOVER_SHADOW_OFFSET_Y = 8
HOVER_SHADOW_EXPANSION = 4


class Card(QGraphicsObject):
    # Render target for all Card paints: "screen" or "camera"
    render_target = "screen"
    # Shared card back cache (pre-scaled per (w,h) on demand)
    _back_cache: dict[tuple[int, int], QPixmap] = {}

    moved = Signal(QPointF)
    zone = ""

    def __init__(self, card_id: str, image_path: str = None,
                 w: float = 120, h: float = 80,
                 color: QColor = QColor(240, 240, 240),
                 visible: bool = True,
                 back_image_path: str | None = None):
        super().__init__()
        self.card_id = card_id
        self.w = w
        self.h = h
        self.color = color
        self.visible = visible
        self._back_image_path = back_image_path

        # Front image
        self.pixmap: QPixmap | None = None
        if image_path:
            src = QPixmap(image_path)
            if not src.isNull():
                self.pixmap = src.scaled(
                    int(w), int(h),
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation
                )

        self.setFlags(
            QGraphicsObject.ItemIsMovable
            | QGraphicsObject.ItemIsSelectable
            | QGraphicsObject.ItemSendsGeometryChanges
        )
        # Default cache; switched to NoCache during hover scale/offset animation
        self.setCacheMode(QGraphicsObject.ItemCoordinateCache)

        self._press_pos: QPointF | None = None
        self._selection_offsets: list[tuple["Card", QPointF]] = []
        self.setAcceptHoverEvents(True)
        self.setTransformOriginPoint(self.w / 2, self.h / 2)

        self._hover_scale = 1.0
        self._hover_offset = 0.0
        self._hover_shadow_enabled = False

        # Scale animation
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(HOVER_ANIMATION_DURATION_MS)
        self._hover_animation.valueChanged.connect(self._apply_hover_scale)

        # Vertical wobble: property animations in a ping-pong group
        self._hover_group = QSequentialAnimationGroup(self)

        self._hover_up = QPropertyAnimation(self, b"hoverOffset")
        self._hover_up.setDuration(HOVER_OSCILLATION_DURATION_MS // 2)
        self._hover_up.setStartValue(0.0)
        self._hover_up.setEndValue(-float(HOVER_OSCILLATION_OFFSET))
        self._hover_up.setEasingCurve(QEasingCurve.InOutSine)

        self._hover_down = QPropertyAnimation(self, b"hoverOffset")
        self._hover_down.setDuration(HOVER_OSCILLATION_DURATION_MS // 2)
        self._hover_down.setStartValue(-float(HOVER_OSCILLATION_OFFSET))
        self._hover_down.setEndValue(0.0)
        self._hover_down.setEasingCurve(QEasingCurve.InOutSine)

        self._hover_group.addAnimation(self._hover_up)
        self._hover_group.addAnimation(self._hover_down)
        self._hover_group.setLoopCount(-1)

    # ------- Assets -------

    def _back_pixmap(self) -> QPixmap | None:
        """Return a pre-scaled back pixmap for (w,h). Cache per size."""
        key = (int(self.w), int(self.h))
        pm = self._back_cache.get(key)
        if pm is None:
            src: QPixmap | None = None
            if self._back_image_path:
                src = QPixmap(self._back_image_path)
            # Fallback: simple gray back if no file
            if not src or src.isNull():
                tmp = QPixmap(int(self.w), int(self.h))
                tmp.fill(QColor(60, 60, 60))
                p = QPainter(tmp)
                p.setPen(QPen(QColor(200, 200, 200), 2))
                p.drawRoundedRect(QRectF(1, 1, self.w - 2, self.h - 2), 8, 8)
                p.end()
                pm = tmp
            else:
                pm = src.scaled(int(self.w), int(self.h),
                                Qt.IgnoreAspectRatio,
                                Qt.SmoothTransformation)
            self._back_cache[key] = pm
        return pm

    def set_card_back(self, image_path: str | None):
        """Optional: set/replace the back art and clear cache for this size."""
        print("Setting card back to: " + str(image_path))
        self._back_image_path = image_path
        key = (int(self.w), int(self.h))
        if key in self._back_cache:
            del self._back_cache[key]
        # trigger redraws when toggling between passes
        self.update()

    # ------- Painting -------

    def boundingRect(self) -> QRectF:
        """Union of card and its potential shadow footprint."""
        base = QRectF(0, 0, self.w, self.h)
        shadow = base.adjusted(
            -HOVER_SHADOW_EXPANSION,
            -HOVER_SHADOW_EXPANSION,
            HOVER_SHADOW_EXPANSION,
            HOVER_SHADOW_EXPANSION,
        ).translated(HOVER_SHADOW_OFFSET_X, HOVER_SHADOW_OFFSET_Y)
        return base.united(shadow)

    def paint(self, painter: QPainter, option, widget=None):
        # Always build geometry from the base rect so painting stays inside boundingRect()
        base = QRectF(0, 0, self.w, self.h)

        # Choose face based on pass
        face_down = (Card.render_target == "camera" and not self.visible)

        if self._hover_shadow_enabled:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(HOVER_SHADOW_COLOR))
            shadow_rect = base.adjusted(
                -HOVER_SHADOW_EXPANSION,
                -HOVER_SHADOW_EXPANSION,
                HOVER_SHADOW_EXPANSION,
                HOVER_SHADOW_EXPANSION,
            ).translated(HOVER_SHADOW_OFFSET_X, HOVER_SHADOW_OFFSET_Y)
            painter.drawRoundedRect(shadow_rect, 8, 8)
            painter.restore()

        if face_down:
            back = self._back_pixmap()
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawPixmap(0, 0, back)
        elif self.pixmap and not self.pixmap.isNull():
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawPixmap(0, 0, self.pixmap)
        else:
            painter.setPen(QPen(Qt.black, 1))
            painter.setBrush(QBrush(self.color))
            painter.drawRoundedRect(base, 8, 8)
            painter.setBrush(QBrush(QColor(210, 210, 210)))
            painter.drawRoundedRect(QRectF(0, 0, self.w, 20), 8, 8)
            painter.drawText(base.adjusted(6, 22, -6, -6),
                             Qt.AlignLeft | Qt.AlignTop, self.card_id)

        # Hide selection chrome in camera pass
        if self.isSelected() and Card.render_target != "camera":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(0, 120, 215), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(base.adjusted(1, 1, -1, -1), 8, 8)

    # ------- Interaction -------

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.scenePos()
            items = [it for it in self.scene().selectedItems() if isinstance(it, Card)]
            if self not in items:
                self.scene().clearSelection()
                self.setSelected(True)
                items = [self]
            self._selection_offsets = [(it, it.pos() - self.pos()) for it in items]
            self.setZValue(10)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._press_pos is not None:
            super().mouseMoveEvent(ev)
            for (it, off) in self._selection_offsets:
                if it is self:
                    continue
                it.setPos(self.pos() + off)
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self.setZValue(0)
        self._press_pos = None
        self._selection_offsets.clear()
        super().mouseReleaseEvent(ev)

    def hoverEnterEvent(self, ev):
        self._start_hover_animation(HOVER_SCALE_FACTOR)
        self._hover_shadow_enabled = True
        # ensure transform changes are visible while animating
        self.setCacheMode(QGraphicsObject.NoCache)
        self._hover_group.start()
        self.update()
        super().hoverEnterEvent(ev)

    def hoverLeaveEvent(self, ev):
        self._start_hover_animation(1.0)
        self._hover_shadow_enabled = False
        self._hover_group.stop()
        # ease back to y=0 in case we stopped mid-cycle
        ret = QPropertyAnimation(self, b"hoverOffset")
        ret.setDuration(HOVER_ANIMATION_DURATION_MS)
        ret.setStartValue(self._hover_offset)
        ret.setEndValue(0.0)
        ret.setEasingCurve(QEasingCurve.InOutSine)
        ret.finished.connect(lambda: self.setCacheMode(QGraphicsObject.ItemCoordinateCache))
        ret.start(QPropertyAnimation.DeleteWhenStopped)
        self.update()
        super().hoverLeaveEvent(ev)

    def itemChange(self, change, value):
        # Clamp while moving
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionChange and self.scene():
            new_pos = QPointF(value)  # proposed pos in scene coords
            rect = self.scene().sceneRect()
            x = max(rect.left(),  min(new_pos.x(), rect.right()  - self.w))
            y = max(rect.top(),   min(new_pos.y(), rect.bottom() - self.h))
            return QPointF(x, y)

        # Notify after move
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionHasChanged:
            self.moved.emit(self.pos())

        return super().itemChange(change, value)

    # ------- Animations -------

    def _start_hover_animation(self, target_scale: float):
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_scale)
        self._hover_animation.setEndValue(target_scale)
        self._hover_animation.start()

    def _apply_hover_scale(self, value: float):
        self._hover_scale = float(value)
        self._update_hover_transform()

    # Property used by QPropertyAnimation
    def getHoverOffset(self) -> float:
        return self._hover_offset

    def setHoverOffset(self, v: float):
        self._hover_offset = float(v)
        self._update_hover_transform()
        self.update()

    hoverOffset = Property(float, fget=getHoverOffset, fset=setHoverOffset)

    def _update_hover_transform(self):
        origin = self.transformOriginPoint()
        t = QTransform()
        # translate first (local space), then scale about center
        if self._hover_offset:
            t.translate(0, self._hover_offset)
        t.translate(origin.x(), origin.y())
        t.scale(self._hover_scale, self._hover_scale)
        t.translate(-origin.x(), -origin.y())
        self.setTransform(t)
