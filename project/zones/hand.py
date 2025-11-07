from PySide6.QtCore import QRectF

from .zone import Zone

class HandZone(Zone):
    def __init__(self, zone_id: str = "hand", slot_h: float = 160, padding: float = 16):
        super().__init__(
            zone_id=zone_id,
            slot_h=slot_h,
            padding=padding,
            orientation="horizontal",
            hide_cards=True,
        )

    def layout_to_view(self, view_rect: QRectF):
        """Stretch along the provided view bounds and hug the bottom edge."""
        if view_rect.isNull() or view_rect.width() <= 0:
            return
        self.set_width(view_rect.width())
        self.set_bottom_anchor(view_rect.left(), view_rect.bottom())
