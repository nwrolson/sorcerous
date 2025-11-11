# scryfall_image_cache.py
from __future__ import annotations

import os
import time
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
from collections import OrderedDict
import json

import requests


@dataclass(frozen=True)
class ImageEntry:
    face: str  # "front", "back", or "face{n}"
    path: str  # absolute path on disk


@dataclass
class CacheResult:
    ok: bool
    id: str                   # "{SET}/{number}"
    images: List[ImageEntry]  # empty on failure
    from_cache: bool          # True if satisfied from memory or disk
    error: Optional[str] = None  # "api_unreachable", "unusable_image", etc.
    detail: Optional[str] = None  # human-readable technical detail
    data: Optional[dict] = None   # Raw card JSON payload when available


class _LRU:
    """Simple LRU by item count."""
    def __init__(self, capacity: int):
        self.capacity = max(1, capacity)
        self._lock = threading.Lock()
        self._d: OrderedDict[str, List[ImageEntry]] = OrderedDict()

    def get(self, key: str) -> Optional[List[ImageEntry]]:
        with self._lock:
            if key not in self._d:
                return None
            self._d.move_to_end(key)
            return self._d[key]

    def put(self, key: str, value: List[ImageEntry]) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.capacity:
                self._d.popitem(last=False)


class _RateLimiter:
    """
    Enforces ~10 rps. Inserts >=100ms between API calls across threads.
    Honors Retry-After on 429.
    """
    def __init__(self, min_delay_sec: float = 0.1):
        self._min_delay = min_delay_sec
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self, retry_after: Optional[float] = None):
        if retry_after is not None and retry_after > 0:
            time.sleep(retry_after)
        with self._lock:
            now = time.time()
            wait = self._min_delay - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()


class ScryfallImageCache:
    """
    Card image cache keyed by {SET}/{collector_number}.
    - Memory LRU cache.
    - Disk cache at root: {root}/{SET}/{collector_number}/[front|back|faceN].png plus data.txt
      with the raw JSON response for future reuse.
    - Fetches via /cards/:code/:number JSON, then follows image_uris links.
    """
    API_BASE = "https://api.scryfall.com"
    SESSION_TIMEOUT = (5, 20)  # (connect, read) seconds

    def __init__(
        self,
        root_dir: str,
        memory_items: int = 256,
        user_agent: str = "VirtualCardPlayer/1.0 (+contact@example.com)"
    ):
        self.root = os.path.abspath(root_dir)
        os.makedirs(self.root, exist_ok=True)

        self.mem = _LRU(memory_items)
        self.rate = _RateLimiter(min_delay_sec=0.1)
        self._http = requests.Session()
        # Required headers per Scryfall
        self._http.headers.update({
            "User-Agent": user_agent,      # must be real and specific
            "Accept": "application/json"   # we fetch JSON first
        })
        self._disk_lock = threading.Lock()

    @staticmethod
    def _key(set_code: str, collector_number: str) -> str:
        return f"{set_code.upper()}/{collector_number}"

    def _disk_paths(self, set_code: str, collector_number: str) -> Tuple[str, Dict[str, str]]:
        base = os.path.join(self.root, set_code.upper(), collector_number)
        # Map of face->path. We will discover which exist.
        candidates = {
            "front": os.path.join(base, "front.png"),
            "back": os.path.join(base, "back.png"),
        }
        # Also allow numbered faces for modal/transform cards
        for i in range(6):  # defensive upper bound
            candidates[f"face{i}"] = os.path.join(base, f"face{i}.png")
        return base, candidates

    def _thumbnail_path(self, base: str) -> str:
        return os.path.join(base, "thumbnail.png")

    def _read_disk(self, set_code: str, collector_number: str) -> List[ImageEntry]:
        base, candidates = self._disk_paths(set_code, collector_number)
        entries: List[ImageEntry] = []
        for face, path in candidates.items():
            if os.path.exists(path):
                entries.append(ImageEntry(face=face, path=path))
        return entries

    def _write_file(self, path: str, content: bytes) -> None:
        with self._disk_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "wb") as f:
                f.write(content)
            os.replace(tmp, path)

    def _read_card_data(self, base: str) -> Optional[dict]:
        """Load cached JSON metadata stored alongside card art, if any."""
        data_path = os.path.join(base, "data.txt")
        if not os.path.exists(data_path):
            return None
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _get_json(self, url: str) -> Tuple[Optional[dict], Optional[str], Optional[str], Optional[str]]:
        """
        Returns (json, raw_text, error_code, detail).
        error_code in {"api_unreachable", "api_error"} or None.
        """
        try:
            self.rate.wait()
            r = self._http.get(url, timeout=self.SESSION_TIMEOUT)
            if r.status_code == 429:
                # obey Retry-After, then one retry
                retry_after = float(r.headers.get("Retry-After", "1"))
                self.rate.wait(retry_after=retry_after)
                r = self._http.get(url, timeout=self.SESSION_TIMEOUT)

            if r.status_code != 200:
                return None, None, "api_error", f"HTTP {r.status_code}: {r.text[:200]}"
            return r.json(), r.text, None, None
        except requests.RequestException as e:
            return None, None, "api_unreachable", str(e)

    def _download_png(self, url: str) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Returns (bytes, error_code) where error_code in {"api_unreachable", "unusable_image"}.
        Follows redirects handled by requests.
        """
        try:
            # For image fetches, Accept anything
            headers = {"Accept": "*/*"}
            self.rate.wait()
            r = self._http.get(url, headers=headers, timeout=self.SESSION_TIMEOUT, stream=True)
            if r.status_code == 429:
                retry_after = float(r.headers.get("Retry-After", "1"))
                self.rate.wait(retry_after=retry_after)
                r = self._http.get(url, headers=headers, timeout=self.SESSION_TIMEOUT, stream=True)

            if r.status_code != 200:
                return None, "unusable_image"
            data = r.content
            # Minimal validation
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                return None, "unusable_image"
            return data, None
        except requests.RequestException:
            return None, "api_unreachable"

    def _extract_thumbnail_uri(self, card: Optional[dict]) -> Optional[str]:
        if not card:
            return None
        faces = card.get("card_faces")
        if isinstance(faces, list):
            for face in faces:
                if not isinstance(face, dict):
                    continue
                image_uris = face.get("image_uris")
                if isinstance(image_uris, dict):
                    uri = image_uris.get("thumbnail")
                    if uri:
                        return uri
            return None
        image_uris = card.get("image_uris")
        if isinstance(image_uris, dict):
            return image_uris.get("thumbnail")
        return None

    def _maybe_cache_thumbnail(self, base: str, card_data: Optional[dict]) -> Optional[str]:
        thumb_path = self._thumbnail_path(base)
        if os.path.exists(thumb_path):
            return thumb_path
        if not card_data:
            return None
        uri = self._extract_thumbnail_uri(card_data)
        if not uri:
            return None
        data, dl_err = self._download_png(uri)
        if dl_err:
            return None
        self._write_file(thumb_path, data)
        return thumb_path

    @staticmethod
    def _split_cache_id(cache_id: str) -> Tuple[str, str]:
        if "/" not in cache_id:
            raise ValueError(f"invalid cache id '{cache_id}'")
        set_code, collector_number = cache_id.split("/", 1)
        return set_code, collector_number

    def get_images(self, set_code: str, collector_number: str) -> CacheResult:
        cid = self._key(set_code, collector_number)
        base, _ = self._disk_paths(set_code, collector_number)

        # 1) Memory
        mem_hit = self.mem.get(cid)
        if mem_hit:
            card_data = self._read_card_data(base)
            self._maybe_cache_thumbnail(base, card_data)
            return CacheResult(
                ok=True, id=cid, images=mem_hit, from_cache=True, data=card_data
            )

        # 2) Disk
        disk_entries = self._read_disk(set_code, collector_number)
        if disk_entries:
            self.mem.put(cid, disk_entries)
            card_data = self._read_card_data(base)
            self._maybe_cache_thumbnail(base, card_data)
            return CacheResult(
                ok=True, id=cid, images=disk_entries, from_cache=True, data=card_data
            )

        # 3) API JSON (cards/:code/:number)
        url = f"{self.API_BASE}/cards/{set_code.lower()}/{collector_number}"
        card, card_json_text, err, detail = self._get_json(url)
        if err:
            return CacheResult(ok=False, id=cid, images=[], from_cache=False, error=err, detail=detail)

        # Choose PNG URLs
        pngs: List[Tuple[str, str]] = []  # (face, url)
        try:
            if "card_faces" in card and isinstance(card["card_faces"], list) and card["card_faces"]:
                for idx, face in enumerate(card["card_faces"]):
                    face_label = "front" if idx == 0 else ("back" if idx == 1 else f"face{idx}")
                    uri = (face.get("image_uris") or {}).get("png")
                    if not uri:
                        # Fallback using format=image with face parameter for back if needed
                        # Requires the card id
                        card_id = card["id"]
                        if face_label == "back":
                            uri = f"{self.API_BASE}/cards/{card_id}?format=image&version=png&face=back"
                        else:
                            uri = f"{self.API_BASE}/cards/{card_id}?format=image&version=png"
                    pngs.append((face_label, uri))
            else:
                # Single-face
                uri = (card.get("image_uris") or {}).get("png")
                if not uri:
                    card_id = card["id"]
                    uri = f"{self.API_BASE}/cards/{card_id}?format=image&version=png"
                pngs.append(("front", uri))
        except Exception as e:
            return CacheResult(ok=False, id=cid, images=[], from_cache=False,
                               error="unusable_image", detail=f"image discovery failed: {e}")

        # 4) Download and store
        if card_json_text is not None:
            data_path = os.path.join(base, "data.txt")
            self._write_file(data_path, card_json_text.encode("utf-8"))
        results: List[ImageEntry] = []
        for face, uri in pngs:
            data, dl_err = self._download_png(uri)
            if dl_err:
                return CacheResult(ok=False, id=cid, images=[], from_cache=False,
                                   error=dl_err, detail=f"failed face={face}")
            path = os.path.join(base, f"{face}.png")
            self._write_file(path, data)
            results.append(ImageEntry(face=face, path=path))

        # Thumbnail is optional and should not fail the entire request
        self._maybe_cache_thumbnail(base, card)

        # 5) Update memory
        self.mem.put(cid, results)
        return CacheResult(ok=True, id=cid, images=results, from_cache=False, data=card)

    def fetch_thumbnail(self, cache_id: str) -> Optional[str]:
        """
        Ensure thumbnail.png exists for the given cache id and return its absolute path.
        cache_id must be of the form "{SET}/{collector_number}".
        """
        set_code, collector_number = self._split_cache_id(cache_id)
        base, _ = self._disk_paths(set_code, collector_number)
        card_data = self._read_card_data(base)
        thumb = self._maybe_cache_thumbnail(base, card_data)
        if thumb:
            return thumb

        # If we do not have enough metadata, fall back to fetching the images (which also caches JSON).
        result = self.get_images(set_code, collector_number)
        if not result.ok:
            return None
        return self._maybe_cache_thumbnail(base, result.data or self._read_card_data(base))
