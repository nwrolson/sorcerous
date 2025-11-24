import weakref

from PySide6.QtCore import (
    Qt, QRectF, QPointF, Signal, QVariantAnimation, QEasingCurve,
    QSequentialAnimationGroup, QPropertyAnimation, Property, QEvent
)
# No TYPE_CHECKING import needed; we only access scene attributes dynamically.
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
HAND_HOVER_Z_VALUE = 50.0
DRAG_Z_VALUE = 10000.0


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
                 face_down: bool = False,
                 id: int = 0,
                 back_image_path: str | None = None,
                 thumbnail_path: str | None = None,
                 card_data: dict | None = None,
                 print_id: str | None = None):
        super().__init__()
        self.card_id = card_id
        self.print_id = print_id or card_id
        self.w = w
        self.h = h
        self.color = color
        self.face_down = face_down
        self._zone_hidden = False
        self.id = id
        self.card_data = card_data
        self.is_token: bool = False  # flag for token cards
        self.token: bool = False     # alias for compatibility
        self._back_image_path = back_image_path
        self._thumbnail_path = thumbnail_path
        self._clamp_to_scene = True
        self._hand_hover_active = False
        self._hand_hover_cancel_on_leave = False
        self._skip_default_hover_leave = False
        self._suppress_default_hover_enter = False
        self._hover_offset_anim = None
        self._hand_hover_dragged_out = False
        self._hand_hover_prev_z: float | None = None
        self._drag_prev_z: float | None = None
        self._hidden_viewports: weakref.WeakSet = weakref.WeakSet()

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

        # Thumbnail image (kept at source size; callers can scale as needed)
        self._thumbnail: QPixmap | None = None
        if thumbnail_path:
            thumb = QPixmap(thumbnail_path)
            if not thumb.isNull():
                self._thumbnail = thumb

        self.setFlags(
            QGraphicsObject.ItemIsMovable
            | QGraphicsObject.ItemIsSelectable
            | QGraphicsObject.ItemSendsGeometryChanges
        )
        # Disable item caching so every paint uses the card's latest art.
        self.setCacheMode(QGraphicsObject.NoCache)

        self._press_pos: QPointF | None = None
        self._press_item_pos: QPointF | None = None
        self._selection_offsets: list[tuple["Card", QPointF]] = []
        self.setAcceptHoverEvents(True)
        self.setTransformOriginPoint(self.w / 2, self.h / 2)

        self._hover_scale = 1.0
        self._hover_offset = 0.0
        self._hover_shadow_enabled = False
        self._tapped = False
        self._dragged_by_user = False

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

    @property
    def thumbnail(self) -> QPixmap | None:
        """Raw thumbnail pixmap, if supplied."""
        return self._thumbnail

    @property
    def thumbnail_path(self) -> str | None:
        """Source path for the thumbnail on disk."""
        return self._thumbnail_path

    def set_card_back(self, image_path: str | None):
        """Optional: set/replace the back art and clear cache for this size."""
        print("Setting card back to: " + str(image_path))
        self._back_image_path = image_path
        key = (int(self.w), int(self.h))
        if key in self._back_cache:
            del self._back_cache[key]
        # trigger redraws when toggling between passes
        self.update()

    def set_zone_hidden(self, hidden: bool):
        if self._zone_hidden == hidden:
            return
        self._zone_hidden = hidden
        self._update_hidden_visibility()
        if not hidden:
            self._invalidate_item_cache()
        self.update()

    def _update_hidden_visibility(self):
        should_show = (not self._zone_hidden) or bool(self._hidden_viewports)
        self.setVisible(should_show)

    def add_hidden_viewport(self, viewport):
        if viewport is None:
            return
        try:
            self._hidden_viewports.add(viewport)
        except TypeError:
            return
        if self._zone_hidden:
            self._update_hidden_visibility()
            self.update()

    def remove_hidden_viewport(self, viewport):
        if viewport is None:
            return
        try:
            self._hidden_viewports.discard(viewport)
        except TypeError:
            return
        if self._zone_hidden and not self._hidden_viewports:
            self._update_hidden_visibility()
            self.update()

    def mark_as_token(self):
        """Tag this card as a token for zone filtering."""
        self.is_token = True
        self.token = True
        try:
            if isinstance(self.card_data, dict):
                self.card_data["token"] = True
            else:
                self.card_data = {"token": True}
        except Exception:
            pass

    def set_clamp_to_scene(self, clamp: bool):
        self._clamp_to_scene = bool(clamp)

    def _invalidate_item_cache(self):
        """Force an immediate repaint using the live pixmap."""
        self.update()

    def set_face_down(self, face_down: bool):
        if self.face_down == face_down:
            return
        self.face_down = face_down
        self._invalidate_item_cache()
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
        if self._zone_hidden:
            if widget is None:
                return
            try:
                allowed = widget in self._hidden_viewports
            except TypeError:
                allowed = False
            if not allowed:
                return

        face_down = (Card.render_target == "camera" and self.face_down)

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
            painter.drawText(
                base.adjusted(6, 22, -6, -6),
                Qt.AlignLeft | Qt.AlignTop,
                self.print_id,
            )

        # Hide selection chrome in camera pass
        if self.isSelected() and Card.render_target != "camera":
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(0, 120, 215), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(base.adjusted(1, 1, -1, -1), 8, 8)

    def sceneEvent(self, event):
        if self._zone_hidden and self._should_block_scene_event(event):
            return False
        return super().sceneEvent(event)

    def _should_block_scene_event(self, event) -> bool:
        event_type = event.type()
        blocked = {
            QEvent.GraphicsSceneMousePress,
            QEvent.GraphicsSceneMouseRelease,
            QEvent.GraphicsSceneMouseMove,
            QEvent.GraphicsSceneMouseDoubleClick,
            QEvent.GraphicsSceneHoverEnter,
            QEvent.GraphicsSceneHoverMove,
            QEvent.GraphicsSceneHoverLeave,
            QEvent.GraphicsSceneWheel,
            QEvent.GraphicsSceneDragEnter,
            QEvent.GraphicsSceneDragMove,
            QEvent.GraphicsSceneDragLeave,
            QEvent.GraphicsSceneDrop,
            QEvent.GraphicsSceneContextMenu,
        }
        if event_type not in blocked:
            return False
        return not self._allow_hidden_event(event)

    def _allow_hidden_event(self, event) -> bool:
        if not self._hidden_viewports:
            return False
        widget_getter = getattr(event, "widget", None)
        viewport = widget_getter() if callable(widget_getter) else None
        if viewport is None:
            return False
        try:
            return viewport in self._hidden_viewports
        except TypeError:
            return False

    # ------- Interaction -------

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.scenePos()
            self._press_item_pos = QPointF(self.pos())
            self._dragged_by_user = False
            self._drag_prev_z = self.zValue()
            self.setZValue(DRAG_Z_VALUE)

            # New: we're starting a new interaction, so reset this
            self._hand_hover_dragged_out = False

            items = [it for it in self.scene().selectedItems() if isinstance(it, Card)]
            if self not in items:
                self.scene().clearSelection()
                self.setSelected(True)
                items = [self]
            self._selection_offsets = [(it, it.pos() - self.pos()) for it in items]
            self.setZValue(max(self.zValue(), 10))
        super().mousePressEvent(ev)


    def mouseMoveEvent(self, ev):
        if self._press_pos is not None:
            if self._press_item_pos is None:
                self._press_item_pos = QPointF(self.pos())

            delta = ev.scenePos() - self._press_pos

            # First time we recognize a drag this press
            if not self._dragged_by_user:
                self._dragged_by_user = True
                self._notify_manual_drag()

                # If this drag started from the hand, we don't want the
                # hand hover anymore for this interaction. But we must
                # kill it without visually snapping the card.
                if self._should_use_hand_hover() and not self._hand_hover_dragged_out:
                    self._hand_hover_dragged_out = True

                    # Capture the scene position of the card's top-left BEFORE
                    # we reset the hover transform.
                    scene = self.scene()
                    before_tl = self.mapToScene(QPointF(0, 0)) if scene is not None else None

                    # Reset the hover transform state
                    if self._hand_hover_active or self._hover_offset != 0.0 or self._hover_scale != 1.0:
                        self._cancel_hand_hover_for_drag()

                    # After neutralizing transforms, adjust pos() so that the
                    # same point stays under the cursor / on screen.
                    if before_tl is not None:
                        after_tl = self.mapToScene(QPointF(0, 0))
                        offset_delta = before_tl - after_tl
                        self.setPos(self.pos() + offset_delta)

                        # Re-anchor the drag origin to the new neutral state
                        self._press_pos = ev.scenePos()
                        self._press_item_pos = QPointF(self.pos())
                        self._refresh_selection_offsets()

            # Standard drag motion using (press_pos, press_item_pos)
            self.setPos(self._press_item_pos + delta)

            for (it, off) in self._selection_offsets:
                if it is self:
                    continue
                it.setPos(self.pos() + off)

            return
        else:
            super().mouseMoveEvent(ev)



    def mouseReleaseEvent(self, ev):
        if self._drag_prev_z is not None:
            self.setZValue(self._drag_prev_z)
        else:
            self.setZValue(0)
        self._drag_prev_z = None
        self._press_pos = None
        self._press_item_pos = None
        self._selection_offsets.clear()

        if self._hand_hover_active and self._dragged_by_user:
            self._cancel_hand_hover_immediate()
        else:
            self._hand_hover_cancel_on_leave = False

        self._dragged_by_user = False
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._can_toggle_tap():
            self.set_tapped(not self._tapped)
            self.update()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def hoverEnterEvent(self, ev):
        # Only use hand hover if:
        # - the model says this card is a hand card, AND
        # - we have not already dragged it out of the hand during this interaction
        if self._should_use_hand_hover() and not self._hand_hover_dragged_out:
            self._begin_hand_hover()
        else:
            if self._suppress_default_hover_enter:
                self._suppress_default_hover_enter = False
                return
            self._skip_default_hover_leave = False
            self._begin_default_hover()

        scene = self.scene()
        if scene and hasattr(scene, "notify_hover_enter"):
            scene.notify_hover_enter(self)
        super().hoverEnterEvent(ev)


    def hoverLeaveEvent(self, ev):
        if self._hand_hover_active:
            self._end_hand_hover()
        else:
            if self._skip_default_hover_leave:
                return
            self._end_default_hover()

        self._hand_hover_cancel_on_leave = False
        scene = self.scene()
        if scene and hasattr(scene, "notify_hover_leave"):
            scene.notify_hover_leave(self)
        super().hoverLeaveEvent(ev)


    def _begin_default_hover(self):
        self._hand_hover_active = False
        self._start_hover_animation(HOVER_SCALE_FACTOR)
        self._hover_shadow_enabled = True
        # ensure transform changes are visible while animating
        self.setCacheMode(QGraphicsObject.NoCache)
        self._hover_group.start()
        self.update()

    def _end_default_hover(self):
        self._start_hover_animation(1.0)
        self._hover_shadow_enabled = False
        self._hover_group.stop()
        # ease back to y=0 in case we stopped mid-cycle
        self._animate_hover_offset(self._hover_offset, 0.0)
        self.update()

    def _begin_hand_hover(self):
        self._hover_animation.stop()
        self._hover_group.stop()
        self._hover_scale = 1.0
        self._hover_shadow_enabled = True
        self._hand_hover_active = True
        self._apply_hand_hover_zboost()
        self.setCacheMode(QGraphicsObject.NoCache)
        current = self._hover_offset
        target = self._hand_hover_target_offset()
        if current != target:
            self._animate_hover_offset(current, target)
        else:
            self.setHoverOffset(target)
        self.update()

    def _end_hand_hover(self):
        self._hover_animation.stop()
        self._hover_group.stop()
        self._hover_scale = 1.0
        self._hover_shadow_enabled = False
        target = self._hover_offset
        if target != 0.0:
            self._animate_hover_offset(target, 0.0)
        else:
            self.setHoverOffset(0.0)
        self._hand_hover_active = False
        self._restore_hand_hover_zboost()
        self.update()

    def _cancel_hand_hover_for_drag(self):
        """Stop hand-hover for drag start, but keep visual position continuous.

        Caller is responsible for re-anchoring self.pos() after this so that
        the card does not visually 'snap' when transforms reset.
        """
        # Stop animations
        self._hover_animation.stop()
        self._hover_group.stop()
        if self._hover_offset_anim is not None:
            try:
                self._hover_offset_anim.stop()
            except RuntimeError:
                pass
            self._hover_offset_anim.deleteLater()
            self._hover_offset_anim = None

        # Reset hover transform state to neutral
        self._hover_scale = 1.0
        self._hover_shadow_enabled = False
        # setHoverOffset will rebuild the transform to neutral
        self.setHoverOffset(0.0)

        # We're no longer in the special hand-hover mode
        self._hand_hover_active = False
        self._restore_hand_hover_zboost()
        # Do NOT touch _skip_default_hover_leave / _suppress_default_hover_enter here


    def _cancel_hand_hover_immediate(self):
        self._hover_animation.stop()
        self._hover_group.stop()
        if self._hover_offset_anim is not None:
            try:
                self._hover_offset_anim.stop()
            except RuntimeError:
                pass
            self._hover_offset_anim.deleteLater()
            self._hover_offset_anim = None
        self._hover_scale = 1.0
        self._hover_shadow_enabled = False
        self.setHoverOffset(0.0)
        self._hand_hover_active = False
        self._hand_hover_cancel_on_leave = False
        self._skip_default_hover_leave = True
        self._suppress_default_hover_enter = True
        self._restore_hand_hover_zboost()
        self.update()

    def _cancel_hand_hover_for_rezone(self):
        """Cancel the hand-hover state when the card leaves the hand zone.

        This fully resets the hover transform and shadow, but does NOT touch
        the skip/suppress flags that are used by the hoverEnter/hoverLeave
        handshake for normal transitions.
        """
        self._hover_animation.stop()
        self._hover_group.stop()
        if self._hover_offset_anim is not None:
            try:
                self._hover_offset_anim.stop()
            except RuntimeError:
                pass
            self._hover_offset_anim.deleteLater()
            self._hover_offset_anim = None
        self._hover_scale = 1.0
        self._hover_shadow_enabled = False
        self.setHoverOffset(0.0)
        self._restore_hand_hover_zboost()
        self._hand_hover_active = False
        self.update()

    def _apply_hand_hover_zboost(self):
        if self._hand_hover_prev_z is None:
            self._hand_hover_prev_z = self.zValue()
        if self.zValue() < HAND_HOVER_Z_VALUE:
            self.setZValue(HAND_HOVER_Z_VALUE)

    def _restore_hand_hover_zboost(self):
        if self._hand_hover_prev_z is None:
            return
        if self.zValue() >= HAND_HOVER_Z_VALUE:
            self.setZValue(self._hand_hover_prev_z)
        self._hand_hover_prev_z = None

    def _hand_hover_target_offset(self) -> float:
        return -float(self.h) / 2.0

    def _should_use_hand_hover(self) -> bool:
        return self._current_container_id() == "hand"

    def _current_container_id(self) -> str:
        scene = self.scene()
        if scene and hasattr(scene, "model"):
            return scene.model.containers.get(self.card_id, "table")
        return "table"

    def _animate_hover_offset(self, start: float, end: float):
        if self._hover_offset_anim is not None:
            try:
                self._hover_offset_anim.stop()
            except RuntimeError:
                pass
            self._hover_offset_anim.deleteLater()
            self._hover_offset_anim = None
        anim = QPropertyAnimation(self, b"hoverOffset")
        anim.setDuration(HOVER_ANIMATION_DURATION_MS)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.InOutSine)
        anim.finished.connect(self._clear_hover_offset_anim)
        anim.start()
        self._hover_offset_anim = anim

    def _clear_hover_offset_anim(self):
        if self._hover_offset_anim is not None:
            self._hover_offset_anim.deleteLater()
        self._hover_offset_anim = None

    def itemChange(self, change, value):
        # Clamp while moving unless explicitly disabled (e.g., when confined inside a zone)
        if (
            self._clamp_to_scene
            and change == QGraphicsObject.GraphicsItemChange.ItemPositionChange
            and self.scene()
        ):
            new_pos = QPointF(value)  # proposed pos in scene coords
            rect = self.scene().sceneRect()
            x = max(rect.left(),  min(new_pos.x(), rect.right()  - self.w))
            y = max(rect.top(),   min(new_pos.y(), rect.bottom() - self.h))
            return QPointF(x, y)

        # Notify after move
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionHasChanged:
            self.moved.emit(self.pos())

            # Track container transitions to switch hover modes cleanly.
            # We want to drop the special hand-hover as soon as the card
            # actually leaves the hand zone, so the next hover uses the
            # table/default behavior.
            prev_container = getattr(self, "_last_container_id", self._current_container_id())
            current_container = self._current_container_id()
            if prev_container == "hand" and current_container != "hand":
                if self._hand_hover_active:
                    self._cancel_hand_hover_for_rezone()
            self._last_container_id = current_container

        return super().itemChange(change, value)

    # ------- Tap State -------

    def is_tapped(self) -> bool:
        return self._tapped

    def set_tapped(self, tapped: bool):
        tapped = bool(tapped)
        if self._tapped == tapped:
            return
        self._tapped = tapped
        self.setRotation(90.0 if tapped else 0.0)
        scene = self.scene()
        if scene and hasattr(scene, "model"):
            entry = scene.model.cards.setdefault(self.card_id, {})
            entry["tapped"] = tapped

    def _can_toggle_tap(self) -> bool:
        scene = self.scene()
        if not scene or not hasattr(scene, "model"):
            return True
        container = scene.model.containers.get(self.card_id, "table")
        return container == "table"

    # ------- Selection Helpers -------

    def reanchor_drag(self, cursor_scene_pos: QPointF):
        if self._press_pos is None:
            return
        self._press_pos = QPointF(cursor_scene_pos)
        self._press_item_pos = QPointF(self.pos())
        self._refresh_selection_offsets()
        self._dragged_by_user = False

    def _refresh_selection_offsets(self):
        if self._press_pos is None:
            return
        scene = self.scene()
        if scene is None:
            return
        items = [it for it in scene.selectedItems() if isinstance(it, Card)]
        if self not in items:
            items.insert(0, self)
        self._selection_offsets = [(it, it.pos() - self.pos()) for it in items]

    def consume_user_drag(self) -> bool:
        dragged = self._dragged_by_user
        self._dragged_by_user = False
        return dragged

    def _notify_manual_drag(self):
        scene = self.scene()
        if scene and hasattr(scene, "notify_manual_drag"):
            scene.notify_manual_drag(self)

    def reset_user_drag_state(self):
        self._dragged_by_user = False

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
