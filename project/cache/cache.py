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
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage
from spawner.token_descriptor import TokenDescriptor


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
        base = os.path.join(self._cards_root(), set_code.upper(), collector_number)
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

    def _tokens_root(self) -> str:
        return os.path.join(self.root, "tokens")

    def _cards_root(self) -> str:
        """
        Ensure cards are stored under a dedicated /cards subdirectory.
        If the provided root already ends with 'cards', use it directly.
        """
        base = self.root
        if os.path.basename(base.rstrip(os.sep)) != "cards":
            base = os.path.join(base, "cards")
        os.makedirs(base, exist_ok=True)
        return base

    def _tokens_cache_root(self) -> str:
        return os.path.join(self._tokens_root(), "cache")

    @staticmethod
    def _is_image_file(name: str) -> bool:
        ext = os.path.splitext(name)[1].lower()
        return ext in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}

    def _read_disk(self, set_code: str, collector_number: str) -> List[ImageEntry]:
        base, candidates = self._disk_paths(set_code, collector_number)
        entries: List[ImageEntry] = []
        for face, path in candidates.items():
            if os.path.exists(path):
                entries.append(ImageEntry(face=face, path=path))
        return entries

    def _token_cache_paths(self, set_code: Optional[str], collector_number: Optional[str], card_id: str) -> Tuple[str, str, Dict[str, str]]:
        """
        Determine token cache id and paths.
        Preferred key: {SET}/{collector_number}; fallback: {card_id}.
        """
        if set_code and collector_number:
            key = f"{str(set_code).upper()}/{collector_number}"
        else:
            key = card_id
        base = os.path.join(self._tokens_cache_root(), key)
        candidates = {
            "front": os.path.join(base, "front.png"),
            "back": os.path.join(base, "back.png"),
        }
        for i in range(6):
            candidates[f"face{i}"] = os.path.join(base, f"face{i}.png")
        return key, base, candidates

    def _read_token_disk(self, set_code: Optional[str], collector_number: Optional[str], card_id: str) -> Tuple[str, str, List[ImageEntry]]:
        cache_id, base, candidates = self._token_cache_paths(set_code, collector_number, card_id)
        entries: List[ImageEntry] = []
        for face, path in candidates.items():
            if os.path.exists(path):
                entries.append(ImageEntry(face=face, path=path))
        return cache_id, base, entries

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

    def _download_image(self, url: str, *, require_png_signature: bool = False) -> Tuple[Optional[bytes], Optional[str]]:
        """
        Download arbitrary binary image data. Optionally enforce PNG signature.
        Returns (bytes, error_code) with error_code in {"api_unreachable", "unusable_image"}.
        """
        try:
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
            if require_png_signature and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                return None, "unusable_image"
            return data, None
        except requests.RequestException:
            return None, "api_unreachable"

    def _download_png(self, url: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Retained for clarity where PNG validation is required."""
        return self._download_image(url, require_png_signature=True)

    @staticmethod
    def _guess_image_format(data: bytes) -> str:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "PNG"
        return "JPG"

    def _crop_thumbnail_top_half(self, data: bytes) -> Optional[bytes]:
        """
        Use QImage to crop the upper 50% of the supplied image bytes.
        Returns the cropped bytes encoded in the original format, or None on failure.
        """
        image = QImage.fromData(data)
        if image.isNull():
            return None
        height = image.height()
        width = image.width()
        if height <= 0 or width <= 0:
            return None
        cropped_height = max(1, height // 2)
        cropped = image.copy(0, 0, width, cropped_height)
        if cropped.isNull():
            return None

        fmt = self._guess_image_format(data)
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        if not buffer.open(QIODevice.WriteOnly):
            return None
        try:
            if not cropped.save(buffer, fmt):
                return None
            return bytes(byte_array.data())
        finally:
            buffer.close()

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
                    uri = image_uris.get("art_crop")
                    if uri:
                        return uri
            return None
        image_uris = card.get("image_uris")
        if isinstance(image_uris, dict):
            return image_uris.get("art_crop")
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
        data, dl_err = self._download_image(uri, require_png_signature=False)
        if dl_err or data is None:
            return None
        cropped = self._crop_thumbnail_top_half(data)
        if cropped:
            data = cropped
        self._write_file(thumb_path, data)
        return thumb_path

    @staticmethod
    def _split_cache_id(cache_id: str) -> Tuple[str, str]:
        if "/" not in cache_id:
            raise ValueError(f"invalid cache id '{cache_id}'")
        set_code, collector_number = cache_id.split("/", 1)
        return set_code, collector_number

    @staticmethod
    def _discover_faces(card: dict) -> List[Tuple[str, str]]:
        """Return list of (face_label, png_url) for the given card JSON."""
        pngs: List[Tuple[str, str]] = []
        if not isinstance(card, dict):
            return pngs
        try:
            if "card_faces" in card and isinstance(card["card_faces"], list) and card["card_faces"]:
                for idx, face in enumerate(card["card_faces"]):
                    face_label = "front" if idx == 0 else ("back" if idx == 1 else f"face{idx}")
                    uri = (face.get("image_uris") or {}).get("png") if isinstance(face, dict) else None
                    if not uri:
                        card_id = card.get("id", "")
                        if face_label == "back":
                            uri = f"{ScryfallImageCache.API_BASE}/cards/{card_id}?format=image&version=png&face=back"
                        else:
                            uri = f"{ScryfallImageCache.API_BASE}/cards/{card_id}?format=image&version=png"
                    pngs.append((face_label, uri))
            else:
                uri = (card.get("image_uris") or {}).get("png")
                if not uri:
                    card_id = card.get("id", "")
                    uri = f"{ScryfallImageCache.API_BASE}/cards/{card_id}?format=image&version=png"
                pngs.append(("front", uri))
        except Exception:
            return []
        return pngs

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

        # Cache related tokens (if any) and embed references.
        linked_tokens = self._cache_related_tokens(card)
        if linked_tokens:
            try:
                card["linked_tokens"] = linked_tokens
            except Exception:
                pass

        # Choose PNG URLs
        pngs = self._discover_faces(card)
        if not pngs:
            return CacheResult(
                ok=False,
                id=cid,
                images=[],
                from_cache=False,
                error="unusable_image",
                detail="image discovery failed",
            )

        # 4) Download and store
        if card is not None:
            data_path = os.path.join(base, "data.txt")
            try:
                payload = json.dumps(card)
                self._write_file(data_path, payload.encode("utf-8"))
            except Exception:
                pass
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

    def get_token_images_by_cache_id(self, cache_id: str) -> CacheResult:
        """
        Resolve a token cache id ("tokens/cache/<...>") to images, fetching from Scryfall if missing.
        cache_id may be "tokens/cache/<SET>/<number>" or "tokens/cache/<id>".
        """
        token_id = cache_id
        if token_id.startswith("tokens/cache/"):
            token_id = token_id[len("tokens/cache/") :]
        set_code: Optional[str] = None
        collector_number: Optional[str] = None
        if "/" in token_id:
            set_code, collector_number = token_id.split("/", 1)

        cache_key, base, disk_entries = self._read_token_disk(set_code, collector_number, token_id)
        if disk_entries:
            data = self._read_card_data(base)
            self._maybe_cache_thumbnail(base, data)
            self.mem.put(f"tokens/cache/{cache_key}", disk_entries)
            return CacheResult(
                ok=True,
                id=f"tokens/cache/{cache_key}",
                images=disk_entries,
                from_cache=True,
                data=data,
            )

        # Try memory (tokens share the same LRU but distinct keys)
        mem_hit = self.mem.get(f"tokens/cache/{cache_key}")
        if mem_hit:
            return CacheResult(
                ok=True,
                id=f"tokens/cache/{cache_key}",
                images=mem_hit,
                from_cache=True,
                data=self._read_card_data(base),
            )

        # Fetch from Scryfall using either set/number or card id
        if set_code and collector_number:
            url = f"{self.API_BASE}/cards/{set_code.lower()}/{collector_number}"
        else:
            url = f"{self.API_BASE}/cards/{token_id}"
        token_json, token_json_text, err, detail = self._get_json(url)
        if err or not token_json:
            return CacheResult(
                ok=False,
                id=f"tokens/cache/{cache_key}",
                images=[],
                from_cache=False,
                error=err or "unusable_image",
                detail=detail,
            )

        pngs = self._discover_faces(token_json)
        if not pngs:
            return CacheResult(
                ok=False,
                id=f"tokens/cache/{cache_key}",
                images=[],
                from_cache=False,
                error="unusable_image",
                detail="image discovery failed",
            )

        if token_json_text is not None:
            data_path = os.path.join(base, "data.txt")
            try:
                self._write_file(data_path, token_json_text.encode("utf-8"))
            except Exception:
                pass

        results: List[ImageEntry] = []
        for face, uri in pngs:
            data, dl_err = self._download_png(uri)
            if dl_err or data is None:
                return CacheResult(
                    ok=False,
                    id=f"tokens/cache/{cache_key}",
                    images=[],
                    from_cache=False,
                    error=dl_err or "unusable_image",
                    detail=f"failed face={face}",
                )
            path = os.path.join(base, f"{face}.png")
            self._write_file(path, data)
            results.append(ImageEntry(face=face, path=path))

        self._maybe_cache_thumbnail(base, token_json)
        self.mem.put(f"tokens/cache/{cache_key}", results)
        return CacheResult(
            ok=True,
            id=f"tokens/cache/{cache_key}",
            images=results,
            from_cache=False,
            data=token_json,
        )

    def _cache_related_tokens(self, card: Optional[dict]) -> List[str]:
        """
        Discover related token cards in all_parts, download and cache their images,
        and return a list of token cache ids ("tokens/cache/<...>").
        """
        if not isinstance(card, dict):
            return []
        parts = card.get("all_parts")
        if not isinstance(parts, list):
            return []

        linked: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("component") != "token":
                continue
            uri = part.get("uri")
            if not uri:
                continue
            token_json, token_json_text, err, detail = self._get_json(uri)
            if err or not token_json:
                continue
            token_id = token_json.get("id") or ""
            set_code = token_json.get("set")
            collector_number = token_json.get("collector_number")
            cache_id, base, existing = self._read_token_disk(set_code, collector_number, token_id)
            if not existing:
                pngs = self._discover_faces(token_json)
                if not pngs:
                    continue
                if token_json_text is not None:
                    data_path = os.path.join(base, "data.txt")
                    try:
                        self._write_file(data_path, token_json_text.encode("utf-8"))
                    except Exception:
                        pass
                for face, face_uri in pngs:
                    data, dl_err = self._download_png(face_uri)
                    if dl_err or data is None:
                        existing = []
                        break
                    path = os.path.join(base, f"{face}.png")
                    self._write_file(path, data)
                    existing.append(ImageEntry(face=face, path=path))
                self._maybe_cache_thumbnail(base, token_json)
            if existing:
                linked.append(f"tokens/cache/{cache_id}")
        return linked

    def list_tokens(self, category: str) -> list[TokenDescriptor]:
        """
        Discover token images under {root}/tokens/{category}.
        Filenames (sans extension) become id/name; loose images only.
        """
        tokens_dir = os.path.join(self._tokens_root(), category)
        if not os.path.isdir(tokens_dir):
            return []

        descriptors: list[TokenDescriptor] = []
        try:
            with os.scandir(tokens_dir) as it:
                for entry in it:
                    if not entry.is_file():
                        continue
                    if not self._is_image_file(entry.name):
                        continue
                    token_id, _ = os.path.splitext(entry.name)
                    descriptors.append(
                        TokenDescriptor(
                            id=token_id,
                            name=token_id,
                            category=category,
                            image_path=os.path.abspath(entry.path),
                        )
                    )
        except OSError:
            return []
        return descriptors
