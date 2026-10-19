import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

try:
    from config import get_text
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from config import get_text

API_KEY = get_text("providers", "springer_api_key", default=os.getenv("SPRINGER_API_KEY", "")) or ""
SEARCH_ENDPOINT = "https://api.springernature.com/meta/v2/json"
MAX_PAGE_LENGTH = 20

LOGGER = logging.getLogger("springer")


@dataclass
class Record:
    title: str | None
    authors: list[str]
    journal: str | None
    published_date: str | None
    abstract: str | None
    doi: str | None
    url: str | None


def configure_logging(debug: bool = False) -> None:
    """Set the log level for command-line runs."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def clean_secret(value: str | None) -> str | None:
    """Trim API keys copied from docs or web pages."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def build_query(keywords: str) -> str:
    """Convert plain keywords into the Springer keyword query syntax."""
    cleaned = keywords.strip()
    if not cleaned:
        raise ValueError("Keywords cannot be empty.")
    return f"keyword: {cleaned}"


def build_search_url(api_key: str, keywords: str, start: int = 1, page_length: int = 10) -> str:
    """Build the Springer metadata search URL."""
    cleaned_api_key = clean_secret(api_key)
    if not cleaned_api_key:
        raise ValueError("API key is empty.")
    params = {
        "api_key": cleaned_api_key,
        "q": build_query(keywords),
        "s": str(start),
        "p": str(page_length),
    }
    return f"{SEARCH_ENDPOINT}?{parse.urlencode(params)}"


def decode_error_body(body: bytes) -> str:
    """Decode an error response body for logging."""
    text = body.decode("utf-8", errors="replace").strip()
    return text or "<empty response body>"


def _build_opener(proxy: str | None = None):
    if proxy:
        return request.build_opener(
            request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return request.build_opener(request.ProxyHandler({}))


def fetch_json(url: str, timeout: int = 30, proxy: str | None = None) -> dict[str, Any]:
    """Send a GET request and parse the JSON response."""
    req = request.Request(url=url, method="GET")
    try:
        opener = _build_opener(proxy)
        with opener.open(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = decode_error_body(exc.read())
        raise RuntimeError(f"Springer API request failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Springer API request failed: {exc.reason}") from exc


def _is_xdmp_argtype_error(message: str) -> bool:
    normalized = message.upper()
    return "HTTP 500" in normalized and "XDMP-ARGTYPE" in normalized


def parse_authors(record: dict[str, Any]) -> list[str]:
    """Extract all creators from a Springer record."""
    creators = record.get("creators", [])
    if not isinstance(creators, list):
        return []
    authors: list[str] = []
    for creator in creators:
        if not isinstance(creator, dict):
            continue
        name = creator.get("creator")
        if isinstance(name, str) and name.strip():
            authors.append(name.strip())
    return authors


def first_url(record: dict[str, Any]) -> str | None:
    """Return the most useful record URL."""
    urls = record.get("url", [])
    if not isinstance(urls, list):
        return None
    for item in urls:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_records(payload: dict[str, Any]) -> list[Record]:
    """Convert raw API records into simplified Record objects."""
    raw_records = payload.get("records", [])
    if not isinstance(raw_records, list):
        return []

    records: list[Record] = []
    for record in raw_records:
        if not isinstance(record, dict):
            continue
        records.append(
            Record(
                title=record.get("title"),
                authors=parse_authors(record),
                journal=record.get("publicationName"),
                published_date=record.get("publicationDate"),
                abstract=record.get("abstract"),
                doi=record.get("doi"),
                url=first_url(record),
            )
        )
    return records


def _record_key(record: Record) -> str:
    if record.doi:
        return record.doi.lower()
    return " ".join((record.title or "").lower().split())


def search_records(
    api_key: str,
    keywords: str,
    start: int = 1,
    page_length: int = 10,
    timeout: int = 30,
    proxy: str | None = None,
) -> list[Record]:
    """Search Springer by keyword and return simplified records."""
    records: list[Record] = []
    seen_keys: set[str] = set()
    next_start = start
    argtype_skip_count = 0
    max_argtype_skips = max(page_length * 5, 20)
    while len(records) < page_length:
        batch_size = min(page_length - len(records), MAX_PAGE_LENGTH)
        current_batch_size = batch_size
        payload: dict[str, Any] | None = None
        while True:
            try:
                payload = fetch_json(
                    build_search_url(
                        api_key,
                        keywords,
                        start=next_start,
                        page_length=current_batch_size,
                    ),
                    timeout=timeout,
                    proxy=proxy,
                )
                argtype_skip_count = 0
                break
            except RuntimeError as exc:
                if not _is_xdmp_argtype_error(str(exc)):
                    raise
                if current_batch_size > 1:
                    next_batch_size = max(1, current_batch_size // 2)
                    LOGGER.warning(
                        "Springer returned XDMP-ARGTYPE at start=%s, reducing page length %s -> %s and retrying.",
                        next_start,
                        current_batch_size,
                        next_batch_size,
                    )
                    current_batch_size = next_batch_size
                    continue
                argtype_skip_count += 1
                LOGGER.warning(
                    "Springer returned XDMP-ARGTYPE at start=%s with page length 1, skipping this offset.",
                    next_start,
                )
                next_start += 1
                if argtype_skip_count >= max_argtype_skips:
                    LOGGER.warning(
                        "Stopped Springer pagination after %s consecutive XDMP-ARGTYPE skips.",
                        argtype_skip_count,
                    )
                    return records
                break
        if payload is None:
            continue
        batch = parse_records(payload)
        if not batch:
            break
        raw_batch_count = len(batch)
        appended = 0
        for record in batch:
            key = _record_key(record)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            records.append(record)
            appended += 1
            if len(records) >= page_length:
                break
        if raw_batch_count < batch_size or appended == 0:
            break
        next_start += raw_batch_count
    return records


def format_authors(authors: list[str]) -> str:
    """Render all authors in a single line."""
    if not authors:
        return "Unknown"
    return ", ".join(authors)


def print_records(records: list[Record]) -> None:
    """Print the final record list for terminal use."""
    if not records:
        print("No results.")
        return

    for index, record in enumerate(records, start=1):
        print(f"[{index}] {record.title or 'Untitled'}")
        print(f"    Authors: {format_authors(record.authors)}")
        print(f"    Journal: {record.journal or 'Unknown'}")
        print(f"    Date: {record.published_date or 'Unknown'}")
        print(f"    Abstract: {record.abstract or 'N/A'}")
        print("")


def parse_args() -> argparse.Namespace:
    """Define the command-line interface."""
    parser = argparse.ArgumentParser(description="Search Springer by keyword and print title, authors, journal, date, abstract.")
    parser.add_argument("--keywords", required=True, help="Keyword phrase to search.")
    parser.add_argument("--start", type=int, default=1, help="Start index, matching the Springer s parameter.")
    parser.add_argument("--count", type=int, default=10, help="Page length, matching the Springer p parameter.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--api-key", default=API_KEY, help="Springer API key.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> int:
    """Run the CLI entrypoint."""
    args = parse_args()
    configure_logging(args.debug)
    try:
        records = search_records(
            api_key=args.api_key,
            keywords=args.keywords,
            start=args.start,
            page_length=args.count,
            timeout=args.timeout,
        )
    except Exception as exc:
        LOGGER.error("%s", exc)
        return 1

    print_records(records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
