#!/usr/bin/env python3
"""Utility script to download the entire Scryfall symbology as SVG assets."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
from urllib import error, request
from urllib.parse import quote

API_URL = "https://api.scryfall.com/symbology"
USER_AGENT = "SorcerousSymbolDownloader/1.0 (+https://scryfall.com/docs/api)"


@dataclass(frozen=True)
class DownloadStats:
    saved: int = 0
    skipped: int = 0
    failed: int = 0

    def __add__(self, other: "DownloadStats") -> "DownloadStats":
        return DownloadStats(
            saved=self.saved + other.saved,
            skipped=self.skipped + other.skipped,
            failed=self.failed + other.failed,
        )


def symbol_to_filename(symbol: str) -> str:
    """Percent-encode filesystem-unsafe characters while keeping braces readable."""
    safe_chars = "{}ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    encoded = quote(symbol, safe=safe_chars)
    return encoded or "symbol"


def fetch_symbology(api_url: str) -> Sequence[dict]:
    """Fetch all symbology entries from Scryfall, following pagination if needed."""
    url = api_url
    symbols: list[dict] = []
    while url:
        payload = fetch_json(url)
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise RuntimeError("Unexpected symbology payload: 'data' is not a list.")
        symbols.extend(data)
        url = payload.get("next_page") if payload.get("has_more") else None
    return symbols


def fetch_json(url: str) -> dict:
    req = request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Request to {url} returned status {resp.status}.")
            body = resp.read()
    except error.URLError as exc:
        raise RuntimeError(f"Failed to reach {url}: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {url}") from exc


def download_data(url: str) -> bytes:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Download from {url} failed with {resp.status}.")
            return resp.read()
    except error.URLError as exc:
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc


def ensure_output_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_symbols(
    entries: Iterable[dict],
    output_dir: Path,
    skip_existing: bool,
) -> DownloadStats:
    stats = DownloadStats()
    for entry in entries:
        symbol = entry.get("symbol")
        svg_uri = entry.get("svg_uri")
        if not symbol or not svg_uri:
            continue
        filename = symbol_to_filename(symbol) + ".svg"
        destination = output_dir / filename
        if skip_existing and destination.exists():
            print(f"Skipping existing {destination.name}")
            stats = stats + DownloadStats(skipped=1)
            continue
        try:
            svg_bytes = download_data(svg_uri)
        except RuntimeError as exc:
            print(f"Failed to download {symbol}: {exc}", file=sys.stderr)
            stats = stats + DownloadStats(failed=1)
            continue
        destination.write_bytes(svg_bytes)
        print(f"Saved {destination.name}")
        stats = stats + DownloadStats(saved=1)
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download all Scryfall symbol SVGs into the project resources."
    )
    default_output = Path(__file__).resolve().parents[1] / "resources" / "symbols"
    parser.add_argument(
        "--api-url",
        default=API_URL,
        help=f"Symbology endpoint to query (default: {API_URL}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_output,
        help=f"Destination directory (default: {default_output}).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip downloads for SVGs that already exist locally.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = ensure_output_dir(args.output_dir)
    try:
        entries = fetch_symbology(args.api_url)
    except RuntimeError as exc:
        print(f"Failed to fetch symbology list: {exc}", file=sys.stderr)
        return 1
    stats = download_symbols(entries, output_dir, args.skip_existing)
    print(
        f"Completed: {stats.saved} saved, {stats.skipped} skipped, {stats.failed} failed."
    )
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
