from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Dict, Mapping, Optional

from cache.cache import CacheResult, ImageEntry, ScryfallImageCache
from card.card import Card


class CardSpawner:
    """
    Helper that fetches card assets and instantiates `Card` objects while
    tracking every spawn within the current session.
    """

    def __init__(
        self,
        cache: ScryfallImageCache,
        *,
        default_width: float = 120.0,
        default_height: float = 80.0,
        default_back_image_path: Optional[str] = None,
    ) -> None:
        self._cache = cache
        self._default_width = default_width
        self._default_height = default_height
        if default_back_image_path is None:
            default_back_image_path = str(
                Path(__file__).resolve().parent.parent
                / "resources"
                / "Magic_card_back.jpg"
            )
        self._default_back_image_path = default_back_image_path
        self._spawned: Dict[str, CacheResult] = {}

    @property
    def spawned(self) -> Mapping[str, CacheResult]:
        """Read-only view over the cached spawn metadata."""
        return MappingProxyType(self._spawned)

    def spawn_card(
        self,
        set_code: str,
        collector_number: str,
        *,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ) -> Card:
        """
        Retrieve art/data for (set_code, collector_number) and build a Card.
        Adds the raw cache result into the internal hashmap keyed by cache id.
        """
        normalized_set = (set_code or "").strip()
        normalized_number = (collector_number or "").strip()
        if not normalized_set or not normalized_number:
            raise ValueError("Both set_code and collector_number are required.")

        result = self._cache.get_images(normalized_set, normalized_number)
        if not result.ok or not result.images:
            detail = result.detail or "no images returned"
            error = result.error or "cache_error"
            raise RuntimeError(
                f"Unable to spawn card {normalized_set}/{normalized_number}: "
                f"{error} ({detail})"
            )

        front_image = self._pick_image(result.images, preferred_face="front")
        if not front_image:
            raise RuntimeError(
                f"Cache returned no usable front image for card {result.id}"
            )

        back_image = self._pick_image(result.images, preferred_face="back")
        if not back_image:
            back_image = self._default_back_image_path

        card = Card(
            card_id=result.id,
            image_path=front_image,
            back_image_path=back_image,
            w=width if width is not None else self._default_width,
            h=height if height is not None else self._default_height,
        )

        # Keep track of every successful spawn for the remainder of the session.
        self._spawned[result.id] = result
        return card

    @staticmethod
    def _pick_image(
        images: list[ImageEntry],
        preferred_face: str,
    ) -> Optional[str]:
        """Return the file path for the requested face, falling back to first image."""
        for entry in images:
            if entry.face == preferred_face:
                return entry.path
        return images[0].path if images else None
