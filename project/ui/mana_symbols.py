from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_TOKEN_PATTERN = re.compile(r"\{[^}]+\}")
_SYMBOL_SAFE_CHARS = "{}ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


class ManaCostRenderer:
    """Loads and rasterizes mana-cost symbols from the bundled SVG assets."""

    def __init__(
        self,
        symbols_dir: str | Path | None = None,
        *,
        icon_size: QSize | None = None,
    ) -> None:
        base = Path(__file__).resolve().parents[1] / "resources" / "symbols"
        self._symbols_dir = Path(symbols_dir) if symbols_dir else base
        self._icon_size = icon_size or QSize(26, 26)
        self._cache: dict[str, QPixmap | None] = {}

    def render_costs(self, costs: Sequence[str] | None) -> list[list[QPixmap]]:
        """Return a list of pixmap sequences, one entry per mana-cost string."""
        rendered: list[list[QPixmap]] = []
        for cost in costs or []:
            icons = self.render_cost(cost)
            if icons:
                rendered.append(icons)
        return rendered

    def render_cost(self, cost: str | None) -> list[QPixmap]:
        """Rasterize a single mana-cost string into pixmaps."""
        if not cost:
            return []
        pixmaps: list[QPixmap] = []
        for token in _TOKEN_PATTERN.findall(cost):
            pixmap = self._symbol_pixmap(token)
            if pixmap:
                pixmaps.append(pixmap)
        return pixmaps

    def _symbol_pixmap(self, token: str) -> QPixmap | None:
        normalized = token.strip().upper()
        if not normalized:
            return None
        if normalized in self._cache:
            return self._cache[normalized]
        encoded = quote(normalized, safe=_SYMBOL_SAFE_CHARS)
        path = self._symbols_dir / f"{encoded}.svg"
        if not path.exists():
            self._cache[normalized] = None
            return None
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            self._cache[normalized] = None
            return None
        pixmap = self._draw_svg(renderer)
        self._cache[normalized] = pixmap if not pixmap.isNull() else None
        return self._cache[normalized]

    def _draw_svg(self, renderer: QSvgRenderer) -> QPixmap:
        pixmap = QPixmap(self._icon_size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            target = self._scaled_rect(renderer)
            renderer.render(painter, target)
        finally:
            painter.end()
        return pixmap

    def _scaled_rect(self, renderer: QSvgRenderer) -> QRectF:
        target_w = float(self._icon_size.width())
        target_h = float(self._icon_size.height())
        view = renderer.viewBoxF()
        width = view.width() or renderer.defaultSize().width() or target_w
        height = view.height() or renderer.defaultSize().height() or target_h
        scale = min(target_w / width if width else 1.0, target_h / height if height else 1.0)
        w = width * scale
        h = height * scale
        x = (target_w - w) / 2.0
        y = (target_h - h) / 2.0
        return QRectF(x, y, w, h)
