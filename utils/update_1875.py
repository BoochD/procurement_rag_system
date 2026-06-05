#!/usr/bin/env python3
"""Download the latest HTML for PP RF No. 1875 and save it locally.

Usage:
    python utils/update_1875.py
    python utils/update_1875.py --url https://www.garant.ru/products/ipo/prime/doc/411097447/

This script is intentionally simple so it can be run from cron/systemd once a day.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

DEFAULT_URL = "https://www.garant.ru/products/ipo/prime/doc/411097447/"
DEFAULT_OUTPUT = Path("data/raw_1875.html")
DEFAULT_META = Path("data/raw_1875.meta.json")
TIMEOUT = 60


LOGGER = logging.getLogger("update_1875")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


DEFAULT_HEADERS = {
    # тут поиграть с агентами, чтобы с сервера тоже работал запрос.
    "User-Agent": (
        "Mozilla/5.0"
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def fetch_html(url: str, timeout: int = 30) -> requests.Response:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    # Иногда помогает сначала открыть главную страницу домена
    # чтобы получить cookies
    root = "https://www.garant.ru/"
    try:
        session.get(root, timeout=timeout)
        time.sleep(1.0)
    except requests.RequestException:
        pass

    response = session.get(
        url,
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response


def detect_encoding(response: requests.Response) -> str:
    if response.encoding:
        return response.encoding
    apparent = response.apparent_encoding
    return apparent or "utf-8"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: str) -> None:
    write_text(path, payload)


def build_meta(url: str, response: requests.Response, html_bytes: bytes) -> str:
    fetched_at = datetime.now(timezone.utc).isoformat()
    content_hash = sha256_bytes(html_bytes)
    status_code = response.status_code
    final_url = response.url
    content_type = response.headers.get("Content-Type", "")
    etag = response.headers.get("ETag")
    last_modified = response.headers.get("Last-Modified")

    import json

    return json.dumps(
        {
            "source_url": url,
            "final_url": final_url,
            "status_code": status_code,
            "content_type": content_type,
            "etag": etag,
            "last_modified": last_modified,
            "fetched_at_utc": fetched_at,
            "sha256": content_hash,
            "size_bytes": len(html_bytes),
        },
        ensure_ascii=False,
        indent=2,
    )

def validate_html(html_text: str) -> None:
    required_markers = [
        "Постановление Правительства Российской Федерации",
        "1875",
    ]
    missing = [marker for marker in required_markers if marker not in html_text]
    if missing:
        raise ValueError(f"Downloaded HTML does not look like the target page. Missing markers: {missing}")


def run(url: str, output_path: Path, meta_path: Optional[Path]) -> None:
    response = fetch_html(url)
    encoding = detect_encoding(response)
    response.encoding = encoding
    html_text = response.text
    html_bytes = html_text.encode("utf-8")

    write_text(output_path, html_text)
    LOGGER.info("Saved HTML to %s (%s bytes)", output_path, len(html_bytes))

    if meta_path is not None:
        write_json(meta_path, build_meta(url, response, html_bytes))
        LOGGER.info("Saved metadata to %s", meta_path)
    validate_html(html_text)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download PP 1875 HTML page")
    parser.add_argument("--url", default=DEFAULT_URL, help="Source page URL")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to raw HTML file")
    parser.add_argument(
        "--meta",
        default=str(DEFAULT_META),
        help="Path to metadata JSON file. Use empty string to disable.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args() 
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    meta_path = Path(args.meta) if args.meta else None
    run(url=args.url, output_path=Path(args.output), meta_path=meta_path)
