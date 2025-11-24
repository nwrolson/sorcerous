from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenDescriptor:
    """Lightweight description of a token image discovered on disk."""

    id: str
    name: str
    category: str
    image_path: str
