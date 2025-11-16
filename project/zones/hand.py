from PySide6.QtCore import QPointF, QRectF, Qt

from .zone import Zone


class HandZone(Zone):
    SCROLL_STEP_MULTIPLIER = 0.5

    def __init__(self, zone_id: str = "hand", slot_h: float = 160, padding: float = 8, card_gap: float | None = None):
        super().__init__(
            zone_id=zone_id,
            slot_h=slot_h,
            padding=padding,
            orientation="horizontal",
            hide_cards=True,
        )
        self._card_gap = card_gap if card_gap is not None else max(4.0, padding * 0.5)
        self._scroll_offset_px = 0.0
        self._content_width = self.width
        self._max_scroll_px = 0.0

    def layout_to_view(self, view_rect: QRectF):
        """Stretch along the provided view bounds and hug the bottom edge."""
        if view_rect.isNull() or view_rect.width() <= 0:
            return
        self.set_width(view_rect.width())
        self.set_bottom_anchor(view_rect.left(), view_rect.bottom())

    def boundingRect(self) -> QRectF:
        base = super().boundingRect()
        width = max(self.width, self._content_width)
        return QRectF(0, 0, width, base.height())

    def reflow_cards(self):
        self._refresh_layout_metrics()
        super().reflow_cards()

    def pos_for(self, index: int) -> QPointF:
        x = self.padding - self._scroll_offset_px
        for card in self.cards[:index]:
            x += card.boundingRect().width() + self._card_gap
        return self.mapToScene(QPointF(x, self.padding))

    def index_at(self, scene_pos: QPointF) -> int:
        local = self.mapFromScene(scene_pos)
        x = local.x() + self._scroll_offset_px - self.padding
        if x <= 0:
            return 0
        pos = self.padding
        for i, card in enumerate(self.cards):
            width = card.boundingRect().width()
            midpoint = pos + width / 2.0
            if x < midpoint:
                return i
            pos += width + self._card_gap
        return len(self.cards)

    def wheelEvent(self, event):
        if self._max_scroll_px <= 0:
            event.ignore()
            return
        step = self._extract_wheel_step(event)
        if step == 0:
            event.ignore()
            return
        offset = self._scroll_offset_px - step * self.SCROLL_STEP_MULTIPLIER
        offset = max(0.0, min(offset, self._max_scroll_px))
        if offset == self._scroll_offset_px:
            event.ignore()
            return
        self._scroll_offset_px = offset
        self.reflow_cards()
        event.accept()

    def _extract_wheel_step(self, event) -> float:
        step = 0.0
        pixel_delta = getattr(event, "pixelDelta", None)
        if callable(pixel_delta):
            delta = pixel_delta()
            if hasattr(delta, "isNull") and not delta.isNull():
                step = float(delta.y())
        if step == 0.0:
            angle_delta = getattr(event, "angleDelta", None)
            if callable(angle_delta):
                delta = angle_delta()
                step = float(delta.y())
        if step == 0.0:
            delta_attr = getattr(event, "delta", None)
            if callable(delta_attr):
                step = float(delta_attr())
        orientation_attr = getattr(event, "orientation", None)
        if callable(orientation_attr):
            orientation = orientation_attr()
            if orientation != Qt.Vertical:
                return 0.0
        return step

    def _refresh_layout_metrics(self):
        content_width = self._calculate_content_width()
        old_effective = max(self.width, self._content_width)
        new_effective = max(self.width, content_width)
        if abs(new_effective - old_effective) > 0.5:
            self.prepareGeometryChange()
        self._content_width = content_width
        self._max_scroll_px = max(0.0, self._content_width - self.width)
        if self._max_scroll_px == 0.0:
            self._scroll_offset_px = 0.0
        else:
            self._scroll_offset_px = max(0.0, min(self._scroll_offset_px, self._max_scroll_px))

    def _calculate_content_width(self) -> float:
        if not self.cards:
            return self.padding * 2
        total = sum(card.boundingRect().width() for card in self.cards)
        gaps = self._card_gap * (len(self.cards) - 1)
        return total + gaps + self.padding * 2
