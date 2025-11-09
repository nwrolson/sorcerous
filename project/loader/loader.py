"""
Deck loader utilities.

This module can take either an exported deck list (currently the same layout
produced by Moxfield/Archidekt) or a deck URL and turn it into a normalized list
of card printings in ``(set_code, collector_number, quantity, name)`` form.

The loader does not talk to any Sorcerous UI code directly – it is purely a
utility that other layers can consume (for instance, the load menu can pass the
clipboard contents to :func:`load_entries` and then feed the resulting
``(SET, NUMBER)`` requests into :class:`project.cache.cache.ScryfallImageCache`).

The module also exposes a small CLI for manual experimentation::

    python -m loader.loader --deck-url https://www.moxfield.com/decks/XXXXX

    python -m loader.loader --deck-file ./mylist.txt --json
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests

SCRYFALL_API = "https://api.scryfall.com"
MOXFIELD_API = "https://api.moxfield.com/v2/decks/all"
ARCHIDEKT_API = "https://www.archidekt.com/api/decks"


class DeckLoaderError(RuntimeError):
    """Raised when we cannot interpret a deck input."""


@dataclass(frozen=True)
class CardPrint:
    """Canonical description of a single printed card entry."""

    name: str
    set_code: str
    collector_number: str
    quantity: int = 1

    def normalized_key(self) -> Tuple[str, str]:
        return self.set_code.upper(), self.collector_number


class DeckLoader:
    """
    Handles loading decks from text or URLs (Moxfield + Archidekt today).

    Parameters
    ----------
    include_sideboard:
        If ``True`` include sideboard entries when present.
    include_maybeboard:
        If ``True`` include maybeboard/wishboard style entries.
    session:
        Optional :class:`requests.Session` to reuse (handy for tests).
    """

    SECTION_HEADER_RE = re.compile(r"^[A-Za-z ][A-Za-z ':-]*\(\d+\)$")
    SIDEBOARD_PREFIX_RE = re.compile(r"^(?:SB:|SIDEBOARD:)\s*", re.IGNORECASE)
    MOXFIELD_ID_RE = re.compile(r"/decks/([^/?#]+)")
    ARCHIDEKT_ID_RE = re.compile(r"/decks/(\d+)")

    TEXT_PATTERNS: Tuple[re.Pattern[str], ...] = (
        # 1 [SET:123] Card Name
        re.compile(
            r"^\s*(\d+)\s*x?\s*\[\s*(?P<set>[A-Za-z0-9]{2,5})\s*:"
            r"\s*(?P<num>[A-Za-z0-9]+)\s*\]\s+(?P<name>.+?)\s*$"
        ),
        # 2 Card Name (SET) 123
        re.compile(
            r"^\s*(\d+)\s*x?\s+(?P<name>.+?)\s+"
            r"\((?P<set>[A-Za-z0-9]{2,5})\)\s+(?P<num>[A-Za-z0-9]+)\s*$"
        ),
        # 3 Fallback: no explicit set/number – we'll resolve via Scryfall
        re.compile(r"^\s*(\d+)\s*x?\s+(?P<name>.+?)\s*$"),
    )

    def __init__(
        self,
        *,
        include_sideboard: bool = True,
        include_maybeboard: bool = False,
        session: Optional[requests.Session] = None,
        user_agent: str = "SorcerousDeckLoader/0.1 (+https://github.com/nwrolson)",
        timeout: Tuple[float, float] = (5.0, 15.0),
    ) -> None:
        self.include_sideboard = include_sideboard
        self.include_maybeboard = include_maybeboard
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", user_agent)

    # ------------------------------------------------------------------ public
    def load(
        self,
        deck_input: str,
        *,
        assume_url: Optional[bool] = None,
    ) -> List[CardPrint]:
        """
        Load a deck from an arbitrary string.

        If ``assume_url`` is ``None`` we autodetect: multi-line strings are deck
        text, while single-line values that look like ``scheme://`` URIs are
        treated as URLs.
        """
        if not deck_input or not deck_input.strip():
            raise DeckLoaderError("Deck input is empty.")

        normalized = deck_input.strip()
        looks_like_url = (
            assume_url
            if assume_url is not None
            else ("\n" not in normalized and self._looks_like_url(normalized))
        )

        if looks_like_url:
            return self._load_from_url(normalized)
        return self.load_from_text(normalized)

    def load_from_text(self, deck_text: str) -> List[CardPrint]:
        """
        Parse an exported deck list.

        Supports the text exports from Moxfield or Archidekt (which both follow
        the ``qty Card Name (SET) NUMBER`` convention) and tries to be liberal in
        what it accepts.
        """
        entries = []
        for raw_line in deck_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            if self.SECTION_HEADER_RE.match(line):
                continue
            line = self.SIDEBOARD_PREFIX_RE.sub("", line, count=1)
            if not line or not line[0].isdigit():
                continue
            parsed = self._parse_text_line(line)
            if parsed:
                entries.append(parsed)

        if not entries:
            raise DeckLoaderError("No card lines detected in deck text.")

        return self._coalesce(entries)

    def load_from_url(self, url: str) -> List[CardPrint]:
        """Fetch and parse a deck list from Moxfield or Archidekt."""
        provider = self._detect_provider(url)
        if provider == "moxfield":
            return self._load_from_moxfield(url)
        if provider == "archidekt":
            return self._load_from_archidekt(url)
        raise DeckLoaderError(
            "Unsupported deck URL. Expected moxfield.com or archidekt.com."
        )

    @staticmethod
    def as_cache_requests(
        entries: Sequence[CardPrint],
        *,
        unique_only: bool = False,
    ) -> List[Tuple[str, str]]:
        """Return ``[(SET, NUMBER), ...]`` in either unique or repeated form."""
        requests: List[Tuple[str, str]] = []
        seen = set()
        for entry in entries:
            key = entry.normalized_key()
            if unique_only:
                if key in seen:
                    continue
                seen.add(key)
                requests.append(key)
            else:
                requests.extend(key for _ in range(entry.quantity))
        return requests

    # -------------------------------------------------------------- text parse
    def _parse_text_line(self, line: str) -> CardPrint | None:
        for pattern in self.TEXT_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            qty = int(match.group(1))
            name = match.groupdict().get("name") or ""
            set_code = match.groupdict().get("set")
            collector = match.groupdict().get("num")
            if set_code and collector:
                return CardPrint(
                    name=name.strip(),
                    set_code=set_code.strip(),
                    collector_number=str(collector).strip(),
                    quantity=qty,
                )
            resolved = self._resolve_print(
                name=name.strip(),
                set_code=set_code,
                collector_number=collector,
                scryfall_id=None,
            )
            return CardPrint(
                name=resolved[2],
                set_code=resolved[0],
                collector_number=resolved[1],
                quantity=qty,
            )
        return None

    # -------------------------------------------------------------- providers
    def _load_from_moxfield(self, url: str) -> List[CardPrint]:
        match = self.MOXFIELD_ID_RE.search(url)
        if not match:
            raise DeckLoaderError("Unable to find deck id inside Moxfield URL.")
        deck_id = match.group(1)
        payload = self._http_get_json(f"{MOXFIELD_API}/{deck_id}")
        zones = [
            "mainboard",
            "commanders",
            "companions",
        ]
        if self.include_sideboard:
            zones.append("sideboard")
        if self.include_maybeboard:
            zones.append("maybeboard")

        entries: List[CardPrint] = []
        for zone in zones:
            section = payload.get(zone)
            if not section:
                continue
            entries.extend(self._normalize_moxfield_zone(section))

        if not entries:
            raise DeckLoaderError("Moxfield response did not contain any cards.")
        return self._coalesce(entries)

    def _normalize_moxfield_zone(self, section: object) -> Iterator[CardPrint]:
        raw_cards: Iterable[object]
        if isinstance(section, dict):
            if isinstance(section.get("cards"), dict):
                raw_cards = section["cards"].values()
            elif isinstance(section.get("cards"), list):
                raw_cards = section["cards"]
            else:
                raw_cards = section.values()
        elif isinstance(section, list):
            raw_cards = section
        else:
            return

        for item in raw_cards:
            if not isinstance(item, dict):
                continue
            card_payload = item.get("card") or item
            quantity = int(item.get("quantity") or item.get("qty") or 1)
            yield self._card_from_payload(card_payload, quantity)

    def _load_from_archidekt(self, url: str) -> List[CardPrint]:
        match = self.ARCHIDEKT_ID_RE.search(url)
        if not match:
            raise DeckLoaderError("Unable to find deck id inside Archidekt URL.")
        deck_id = match.group(1)
        payload = self._http_get_json(f"{ARCHIDEKT_API}/{deck_id}/")

        entries: List[CardPrint] = []
        cards_section = payload.get("cards") or []
        entries.extend(self._normalize_archidekt_cards(cards_section))

        if self.include_maybeboard:
            entries.extend(
                self._normalize_archidekt_cards(payload.get("maybeboard") or [])
            )

        if not entries:
            raise DeckLoaderError("Archidekt response did not contain any cards.")
        return self._coalesce(entries)

    def _normalize_archidekt_cards(self, sections: Iterable[object]) -> Iterator[CardPrint]:
        for entry in sections:
            if not isinstance(entry, dict):
                continue
            categories = entry.get("categories") or []
            category_names = {
                (c.get("category") or {}).get("name", "").lower()
                for c in categories
                if isinstance(c, dict)
            }
            if (
                not self.include_maybeboard
                and {"maybeboard", "maybe board"} & category_names
            ):
                continue
            if not self.include_sideboard and "sideboard" in category_names:
                continue
            quantity = int(entry.get("quantity") or 1)
            card_payload = entry.get("cardDigest") or entry.get("card") or entry
            yield self._card_from_payload(card_payload, quantity)

    # --------------------------------------------------------------- utilities
    def _card_from_payload(self, card_payload: object, quantity: int) -> CardPrint:
        if not isinstance(card_payload, dict):
            raise DeckLoaderError("Card payload missing expected structure.")

        name = (
            card_payload.get("name")
            or card_payload.get("cardName")
            or (card_payload.get("card") or {}).get("name")
            or (card_payload.get("oracleCard") or {}).get("name")
            or "Unknown Card"
        )

        edition_block = card_payload.get("edition") or {}
        default_printing = card_payload.get("defaultPrinting") or {}
        default_printing_edition = default_printing.get("edition") or {}
        identifier_block = card_payload.get("identifier") or {}

        set_code = (
            card_payload.get("setCode")
            or card_payload.get("set")
            or edition_block.get("code")
            or default_printing_edition.get("code")
            or identifier_block.get("setCode")
        )

        collector_number = (
            card_payload.get("collectorNumber")
            or card_payload.get("number")
            or default_printing.get("collectorNumber")
            or edition_block.get("collectorNumber")
            or identifier_block.get("number")
        )

        scryfall_id = (
            card_payload.get("scryfallId")
            or card_payload.get("scryfall_id")
            or identifier_block.get("scryfallId")
            or (card_payload.get("oracleCard") or {}).get("scryfallId")
        )

        set_code, collector_number, resolved_name = self._resolve_print(
            name=name,
            set_code=set_code,
            collector_number=collector_number,
            scryfall_id=scryfall_id,
        )

        return CardPrint(
            name=resolved_name,
            set_code=set_code,
            collector_number=collector_number,
            quantity=max(1, quantity),
        )

    def _resolve_print(
        self,
        *,
        name: str,
        set_code: Optional[str],
        collector_number: Optional[str],
        scryfall_id: Optional[str],
    ) -> Tuple[str, str, str]:
        if set_code and collector_number:
            return set_code.upper(), str(collector_number), name

        if scryfall_id:
            card = self._http_get_json(f"{SCRYFALL_API}/cards/{scryfall_id}")
            return card["set"].upper(), card["collector_number"], card.get("name", name)

        if set_code and collector_number:
            card = self._http_get_json(
                f"{SCRYFALL_API}/cards/{set_code.lower()}/{collector_number}"
            )
            return card["set"].upper(), card["collector_number"], card.get("name", name)

        if name:
            card = self._http_get_json(
                f"{SCRYFALL_API}/cards/named", params={"fuzzy": name}
            )
            return card["set"].upper(), card["collector_number"], card.get("name", name)

        raise DeckLoaderError("Unable to resolve card printing; insufficient data.")

    def _coalesce(self, entries: Iterable[CardPrint]) -> List[CardPrint]:
        merged: dict[Tuple[str, str], CardPrint] = {}
        for entry in entries:
            key = entry.normalized_key()
            if key in merged:
                prev = merged[key]
                merged[key] = CardPrint(
                    name=entry.name or prev.name,
                    set_code=prev.set_code,
                    collector_number=prev.collector_number,
                    quantity=prev.quantity + entry.quantity,
                )
            else:
                merged[key] = entry
        return list(merged.values())

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        parsed = urlparse(value)
        return bool(parsed.scheme and parsed.netloc)

    @staticmethod
    def _detect_provider(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if "moxfield" in host:
            return "moxfield"
        if "archidekt" in host:
            return "archidekt"
        return ""

    def _http_get_json(self, url: str, params: Optional[dict] = None) -> dict:
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise DeckLoaderError(f"Failed to reach {url}: {exc}") from exc
        if resp.status_code != 200:
            raise DeckLoaderError(
                f"HTTP {resp.status_code} while requesting {url}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise DeckLoaderError("Received non-JSON response.") from exc


# ---------------------------------------------------------------- CLI helpers
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load Sorcerous decks from a URL or exported text."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--deck-url", help="Moxfield or Archidekt deck URL.")
    group.add_argument("--deck-text", help="Deck text passed inline.")
    group.add_argument("--deck-file", help="Path to deck text (UTF-8).")
    group.add_argument(
        "--stdin",
        action="store_true",
        help="Read deck text from stdin (mutually exclusive).",
    )
    parser.add_argument(
        "--unique",
        action="store_true",
        help="Only emit unique (SET, NUMBER) pairs for cache requests.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of pretty text.",
    )
    return parser


def _read_deck_text_from_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise DeckLoaderError(f"Unable to read deck file '{path}': {exc}") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    loader = DeckLoader()
    try:
        if args.deck_url:
            entries = loader.load_from_url(args.deck_url)
        elif args.deck_text:
            entries = loader.load_from_text(args.deck_text)
        elif args.deck_file:
            entries = loader.load_from_text(_read_deck_text_from_file(args.deck_file))
        elif args.stdin:
            entries = loader.load_from_text(sys.stdin.read())
        else:
            parser.error("No deck source provided.")
            return 2
    except DeckLoaderError as exc:
        print(f"[loader] {exc}", file=sys.stderr)
        return 1

    cache_requests = DeckLoader.as_cache_requests(entries, unique_only=args.unique)

    if args.json:
        payload = {
            "cards": [card.__dict__ for card in entries],
            "cache_requests": cache_requests,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"Cards loaded: {len(entries)}")
        for card in entries:
            print(
                f"{card.quantity}x {card.name} "
                f"({card.set_code.upper()}) {card.collector_number}"
            )
        print("\nCache requests:")
        for set_code, number in cache_requests:
            print(f"- {set_code}/{number}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
