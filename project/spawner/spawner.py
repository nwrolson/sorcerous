from __future__ import annotations

import threading
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Dict, Mapping, Optional

from PySide6.QtCore import QObject, Signal

from cache.cache import CacheResult, ImageEntry, ScryfallImageCache
from card.card import Card
from loader.loader import DeckLoader, DeckLoaderError


class CardSpawner(QObject):
    spawnProgress = Signal(str)
    spawnFailed = Signal(str)
    spawnCompleted = Signal(list)
    jobsReady = Signal(list)
    """
    Helper that fetches card assets and instantiates `Card` objects while
    tracking every spawn within the current session.
    """

    def __init__(
        self,
        cache: ScryfallImageCache,
        parent: Optional[QObject] = None,
        *,
        default_width: float = 120.0,
        default_height: float = 80.0,
        default_back_image_path: Optional[str] = None,
        deck_loader: Optional[DeckLoader] = None,
        on_cards_spawned: Optional[Callable[[list[Card]], None]] = None,
    ) -> None:
        super().__init__(parent)
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
        self._deck_loader = deck_loader or DeckLoader()
        self._thread_lock = threading.Lock()
        self._active_thread: Optional[threading.Thread] = None
        self.jobsReady.connect(self._on_jobs_ready)
        if on_cards_spawned:
            self.spawnCompleted.connect(on_cards_spawned)

    @property
    def spawned(self) -> Mapping[str, CacheResult]:
        """Read-only view over the cached spawn metadata."""
        return MappingProxyType(self._spawned)

    def _report_progress(self, message: str) -> None:
        text = f"[CardSpawner] {message}"
        print(text)
        self.spawnProgress.emit(message)

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

        return self._build_card_from_result(result, width=width, height=height)

    def _build_card_from_result(
        self,
        result: CacheResult,
        *,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ) -> Card:
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

    def _prepare_spawn_jobs(
        self,
        deck_input: str,
        progress: Optional[Callable[[str], None]] = None,
    ) -> list[CacheResult]:
        reporter = progress or self._report_progress
        normalized = (deck_input or "").strip()
        if not normalized:
            reporter("Ignoring empty deck input.")
            return []

        reporter("Parsing deck input...")
        entries = self._deck_loader.load(normalized)
        if not entries:
            reporter("Deck input produced no entries.")
            return []

        total_cards = sum(entry.quantity for entry in entries)
        reporter(f"Preparing {total_cards} card(s) across {len(entries)} entries...")

        prepared_results: list[CacheResult] = []
        prepared = 0
        for entry in entries:
            for _ in range(entry.quantity):
                result = self._cache.get_images(entry.set_code, entry.collector_number)
                if not result.ok or not result.images:
                    detail = result.detail or "no images returned"
                    error = result.error or "cache_error"
                    reporter(
                        f"Skipping {entry.set_code}/{entry.collector_number}: "
                        f"{error} ({detail})"
                    )
                    continue
                prepared_results.append(result)
                prepared += 1
                if total_cards <= 10 or prepared % 5 == 0 or prepared == total_cards:
                    reporter(f"Fetched assets for {prepared}/{total_cards} cards...")

        reporter("Finished preparing card assets.")
        return prepared_results

    def _instantiate_cards(
        self,
        results: list[CacheResult],
        *,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ) -> list[Card]:
        cards: list[Card] = []
        for result in results:
            try:
                card = self._build_card_from_result(
                    result,
                    width=width,
                    height=height,
                )
            except RuntimeError as exc:
                self._report_progress(f"Failed to instantiate card {result.id}: {exc}")
                continue
            cards.append(card)
        return cards

    def spawn_from_deck_input(self, deck_input: str) -> list[Card]:
        """
        Use DeckLoader to interpret arbitrary deck text/URLs and spawn cards.
        Returns the successfully created Card instances.
        """
        try:
            results = self._prepare_spawn_jobs(deck_input, self._report_progress)
        except DeckLoaderError as exc:
            self._report_progress(f"Deck load failed: {exc}")
            return []
        return self._instantiate_cards(results)

    def handle_import_signal(self, payload: str) -> bool:
        """
        Slot-friendly wrapper that loads + spawns cards, then notifies a callback.
        Returns True if the request was accepted and work enqueued.
        """
        with self._thread_lock:
            if self._active_thread and self._active_thread.is_alive():
                self._report_progress(
                    "Spawn already in progress; ignoring additional request."
                )
                return False
            worker = threading.Thread(
                target=self._run_spawn_job,
                args=(payload,),
                daemon=True,
                name="CardSpawnerWorker",
            )
            self._active_thread = worker
        worker.start()
        return True

    def _run_spawn_job(self, payload: str) -> None:
        self._report_progress("Starting background spawn job.")
        try:
            results = self._prepare_spawn_jobs(payload, self._report_progress)
        except DeckLoaderError as exc:
            self._report_progress(f"Deck load failed: {exc}")
            self.spawnFailed.emit(str(exc))
            results = []
        except Exception as exc:  # pragma: no cover - unexpected
            self._report_progress(f"Unexpected error while spawning: {exc}")
            self.spawnFailed.emit(str(exc))
            results = []
        else:
            self._report_progress(
                f"Prepared {len(results)} card asset bundle(s); dispatching to UI thread."
            )
            self.jobsReady.emit(results)
        finally:
            with self._thread_lock:
                self._active_thread = None

    def _on_jobs_ready(self, results: list[CacheResult]) -> None:
        cards = self._instantiate_cards(results)
        self._report_progress(f"Instantiated {len(cards)} card(s) on UI thread.")
        self.spawnCompleted.emit(cards)

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
