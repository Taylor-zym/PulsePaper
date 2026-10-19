import argparse
import html
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from uuid import uuid4

try:
    from config import get_text
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from config import get_text

API_KEY = get_text("providers", "elsevier_api_key", default=os.getenv("ELSEVIER_API_KEY", "")) or ""

SEARCH_ENDPOINT = "https://api.elsevier.com/content/search/sciencedirect"
RETRIEVAL_DOI_ENDPOINT = "https://api.elsevier.com/content/article/doi/{identifier}"
REQUEST_COUNT_OPTIONS = (10, 25, 50, 100)
LATEST_SORT = "coverDate"
RETRIEVAL_VIEW = "META_ABS"

LOGGER = logging.getLogger("elsevier")


@dataclass
class Article:
    """Hold the minimal fields used by the CLI."""

    title: str | None
    authors: list[str]
    journal: str | None
    year: str | None
    abstract: str | None
    doi: str | None
    published_date: str | None = None


def configure_logging(debug: bool = False) -> None:
    """Set the log level for command-line runs."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def clean_text(value: str | None) -> str | None:
    """Strip HTML and normalize whitespace."""
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def clean_secret(value: str | None) -> str | None:
    """Trim copied credentials and normalize empty strings."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def build_headers(api_key: str) -> dict[str, str]:
    """Build request headers for Elsevier API calls."""
    cleaned_api_key = clean_secret(api_key)
    if not cleaned_api_key:
        raise ValueError("API key is empty.")
    return {
        "Accept": "application/json",
        "X-ELS-APIKey": cleaned_api_key,
        "X-ELS-ReqId": str(uuid4()),
    }


def keywords_to_query(keywords: str) -> str:
    """Convert plain keywords into a valid Search API query string."""
    cleaned = keywords.strip()
    if not cleaned:
        raise ValueError("Keywords cannot be empty.")
    if re.search(r"\b(AND|OR|NOT)\b|[(){}]", cleaned):
        return cleaned
    if " " in cleaned:
        return f'"{cleaned}"'
    return cleaned


def resolve_request_count(desired_count: int) -> int:
    """Round the requested count up to the nearest supported value."""
    if desired_count < 1:
        raise ValueError("--count must be >= 1.")
    if desired_count > 100:
        raise ValueError("--count must be <= 100.")
    for allowed in REQUEST_COUNT_OPTIONS:
        if desired_count <= allowed:
            return allowed
    return 100


def build_search_url(query: str, count: int, start: int = 0) -> str:
    """Build a latest-first ScienceDirect search URL."""
    params = {
        "query": query,
        "count": str(count),
        "start": str(start),
        "view": "STANDARD",
        "sort": LATEST_SORT,
        "suppressNavLinks": "true",
    }
    return f"{SEARCH_ENDPOINT}?{parse.urlencode(params)}"


def build_retrieval_url(doi: str) -> str:
    """Build a DOI-based retrieval URL for abstract fallback."""
    params = {
        "view": RETRIEVAL_VIEW,
        "httpAccept": "application/json",
        "xml-decode": "true",
    }
    endpoint = RETRIEVAL_DOI_ENDPOINT.format(identifier=parse.quote(doi, safe=""))
    return f"{endpoint}?{parse.urlencode(params)}"


def decode_error_body(body: bytes) -> str:
    """Decode an HTTP error body into readable text."""
    text = body.decode("utf-8", errors="replace").strip()
    return text or "<empty response body>"


def is_auth_error(message: str) -> bool:
    """Detect authorization failures returned by Elsevier APIs."""
    normalized = message.upper()
    return "HTTP 401" in normalized or "HTTP 403" in normalized or "AUTHORIZATION_ERROR" in normalized


def add_auth_hint(message: str) -> str:
    """Append a concise hint to authorization failures."""
    if not is_auth_error(message):
        return message
    return f"{message} This API key is not authorized for the current ScienceDirect request."


def _build_opener(proxy: str | None = None):
    if proxy:
        return request.build_opener(
            request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return request.build_opener(request.ProxyHandler({}))


def fetch_json(
    url: str,
    headers: dict[str, str],
    timeout: int = 30,
    proxy: str | None = None,
) -> dict[str, Any]:
    """Send a GET request and parse the JSON response."""
    req = request.Request(url=url, headers=headers, method="GET")
    try:
        opener = _build_opener(proxy)
        with opener.open(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = decode_error_body(exc.read())
        message = f"Elsevier API request failed with HTTP {exc.code}: {detail}"
        raise RuntimeError(add_auth_hint(message)) from exc
    except error.URLError as exc:
        raise RuntimeError(f"Elsevier API request failed: {exc.reason}") from exc


def first_value(entry: dict[str, Any], *keys: str) -> Any:
    """Return the first non-empty value among candidate keys."""
    for key in keys:
        value = entry.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_doi(value: str | None) -> str | None:
    """Remove a DOI: prefix when the API returns one."""
    if not value:
        return None
    if value.upper().startswith("DOI:"):
        return value.split(":", 1)[1]
    return value


def extract_year(value: str | None) -> str | None:
    """Extract a 4-digit year from a cover date string."""
    if not value:
        return None
    match = re.search(r"\b(\d{4})\b", value)
    if match:
        return match.group(1)
    return None


def parse_authors(entry: dict[str, Any]) -> list[str]:
    """Extract author names from the search payload."""
    authors_container = entry.get("authors")
    authors = None
    if isinstance(authors_container, dict):
        authors = authors_container.get("author")
    if isinstance(authors, dict):
        authors = [authors]
    if isinstance(authors, list):
        names: list[str] = []
        for author in authors:
            if not isinstance(author, dict):
                continue
            preferred_name = author.get("preferred-name", {})
            name = preferred_name.get("ce:indexed-name") or author.get("ce:indexed-name") or author.get("$")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        if names:
            return names

    creator = first_value(entry, "dc:creator", "creator")
    if isinstance(creator, str) and creator.strip():
        return [creator.strip()]
    return []


def parse_articles(payload: dict[str, Any]) -> list[Article]:
    """Convert search results into simplified article records."""
    if not isinstance(payload, dict):
        LOGGER.debug("Unexpected Elsevier payload type: %s", type(payload).__name__)
        return []

    search_results = payload.get("search-results")
    if not isinstance(search_results, dict):
        LOGGER.debug("Elsevier search-results is not an object: %r", search_results)
        return []

    raw_entries = search_results.get("entry", [])
    if isinstance(raw_entries, dict):
        raw_entries = [raw_entries]

    articles: list[Article] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        cover_date = first_value(entry, "prism:coverDate", "coverDate")
        articles.append(
            Article(
                title=first_value(entry, "dc:title", "title"),
                authors=parse_authors(entry),
                journal=first_value(entry, "prism:publicationName", "publicationName"),
                year=extract_year(cover_date),
                published_date=cover_date,
                abstract=clean_text(first_value(entry, "dc:description", "description", "prism:teaser")),
                doi=first_value(entry, "prism:doi", "doi") or normalize_doi(entry.get("dc:identifier")),
            )
        )
    return articles


def find_first_text(node: Any) -> str | None:
    """Recursively find the first abstract-like text field in a retrieval payload."""
    if isinstance(node, dict):
        for key in ("dc:description", "description", "ce:abstract", "abstract"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return clean_text(value)
        for value in node.values():
            found = find_first_text(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_first_text(item)
            if found:
                return found
    return None


def fill_missing_abstract(
    article: Article,
    headers: dict[str, str],
    timeout: int = 30,
    proxy: str | None = None,
) -> Article:
    """Use DOI retrieval to backfill the abstract when search metadata is empty."""
    if article.abstract or not article.doi:
        return article
    try:
        payload = fetch_json(
            build_retrieval_url(article.doi),
            headers=headers,
            timeout=timeout,
            proxy=proxy,
        )
    except RuntimeError as exc:
        if is_auth_error(str(exc)):
            LOGGER.debug("Skipping abstract retrieval for %s because the API key lacks access.", article.doi)
            return article
        raise
    article.abstract = find_first_text(payload)
    return article


def search_articles(
    api_key: str,
    keywords: str,
    count: int,
    timeout: int = 30,
    proxy: str | None = None,
) -> list[Article]:
    """Search ScienceDirect and backfill missing abstracts when possible."""
    headers = build_headers(api_key)
    collected: list[Article] = []
    start = 0
    while len(collected) < count:
        batch_size = resolve_request_count(min(count - len(collected), 100))
        url = build_search_url(
            keywords_to_query(keywords),
            batch_size,
            start=start,
        )
        payload = fetch_json(url, headers=headers, timeout=timeout, proxy=proxy)
        articles = parse_articles(payload)
        if not articles:
            break
        collected.extend(articles[: count - len(collected)])
        if len(articles) < batch_size:
            break
        start += len(articles)
    return [
        fill_missing_abstract(article, headers=headers, timeout=timeout, proxy=proxy)
        for article in collected
    ]


def format_authors(authors: list[str]) -> str:
    """Format all author names for terminal output."""
    if not authors:
        return "Unknown"
    return ", ".join(authors)


def print_articles(articles: list[Article]) -> None:
    """Print title, authors, and abstract for each result."""
    if not articles:
        print("No results.")
        return
    for index, article in enumerate(articles, start=1):
        published_date = article.published_date or article.year or "Unknown"
        print(f"[{index}] {article.title or 'Untitled'}")
        print(f"    Authors: {format_authors(article.authors)}")
        print(f"    Journal: {article.journal or 'Unknown'}")
        print(f"    Date: {published_date}")
        print(f"    Abstract: {article.abstract or 'N/A'}")
        print("")


def parse_args() -> argparse.Namespace:
    """Define the minimal command-line interface."""
    parser = argparse.ArgumentParser(description="Search ScienceDirect by keyword and print title, authors, abstract.")
    parser.add_argument("--keywords", required=True, help="Keyword phrase or boolean query.")
    parser.add_argument("--count", type=int, default=5, help="Number of results to print.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--api-key", default=API_KEY, help="Elsevier API key.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> int:
    """Run the simplified CLI."""
    args = parse_args()
    configure_logging(args.debug)
    try:
        print_articles(search_articles(args.api_key, args.keywords, args.count, args.timeout))
    except Exception as exc:
        LOGGER.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
