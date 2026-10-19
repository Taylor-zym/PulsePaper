from __future__ import annotations

import argparse
import json
import os
import re
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

API_KEY = get_text("providers", "ieee_api_key", default=os.getenv("IEEE_API_KEY", "")) or ""
SEARCH_ENDPOINT = "https://ieeexploreapi.ieee.org/api/v1/search/articles"


@dataclass
class IeeeArticle:
    title: str | None
    authors: list[str]
    journal: str | None
    published_date: str | None
    abstract: str | None
    doi: str | None
    url: str | None
    content_type: str | None = None
    conference_location: str | None = None
    conference_dates: str | None = None


def clean_secret(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def build_search_url(
    api_key: str,
    keywords: str,
    start_record: int = 1,
    max_records: int = 10,
    sort_field: str | None = None,
    sort_order: str | None = None,
    content_type: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
) -> str:
    cleaned_api_key = clean_secret(api_key)
    if not cleaned_api_key:
        raise ValueError("API key is empty.")

    cleaned_keywords = keywords.strip()
    if not cleaned_keywords:
        raise ValueError("Keywords cannot be empty.")

    if start_record < 1:
        raise ValueError("start_record must be >= 1.")
    if max_records < 1:
        raise ValueError("max_records must be >= 1.")

    params = {
        "apikey": cleaned_api_key,
        "querytext": cleaned_keywords,
        "start_record": str(start_record),
        "max_records": str(max_records),
        "format": "json",
    }
    cleaned_sort_field = _normalize_text(sort_field)
    if cleaned_sort_field:
        params["sort_field"] = cleaned_sort_field
    cleaned_sort_order = _normalize_text(sort_order)
    if cleaned_sort_order:
        params["sort_order"] = cleaned_sort_order.lower()
    cleaned_content_type = _normalize_text(content_type)
    if cleaned_content_type:
        params["content_type"] = cleaned_content_type
    if isinstance(start_year, int):
        params["start_year"] = str(start_year)
    if isinstance(end_year, int):
        params["end_year"] = str(end_year)
    return f"{SEARCH_ENDPOINT}?{parse.urlencode(params)}"


def decode_error_body(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    return text or "<empty response body>"


def _build_opener(proxy: str | None = None):
    if proxy:
        return request.build_opener(
            request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return request.build_opener(request.ProxyHandler({}))


def fetch_json(url: str, timeout: int = 30, proxy: str | None = None) -> dict[str, Any]:
    req = request.Request(url=url, method="GET")
    try:
        opener = _build_opener(proxy)
        with opener.open(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = decode_error_body(exc.read())
        raise RuntimeError(f"IEEE API request failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"IEEE API request failed: {exc.reason}") from exc


def _normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value.strip())
    return cleaned or None


def parse_authors(article: dict[str, Any]) -> list[str]:
    raw_authors = article.get("authors")
    if isinstance(raw_authors, dict):
        raw_authors = raw_authors.get("authors") or raw_authors.get("author")
    if isinstance(raw_authors, dict):
        raw_authors = [raw_authors]
    if not isinstance(raw_authors, list):
        return []

    authors: list[str] = []
    for item in raw_authors:
        if isinstance(item, str):
            name = _normalize_text(item)
        elif isinstance(item, dict):
            name = (
                _normalize_text(item.get("full_name"))
                or _normalize_text(item.get("name"))
                or _normalize_text(item.get("preferred_name"))
                or _normalize_text(item.get("authorName"))
            )
        else:
            name = None
        if name:
            authors.append(name)
    return authors


def first_url(article: dict[str, Any]) -> str | None:
    for key in ("html_url", "abstract_url", "pdf_url"):
        value = _normalize_text(article.get(key))
        if value:
            return value
    return None


def parse_articles(payload: dict[str, Any]) -> list[IeeeArticle]:
    raw_articles = payload.get("articles", [])
    if isinstance(raw_articles, dict):
        raw_articles = [raw_articles]
    if not isinstance(raw_articles, list):
        return []

    records: list[IeeeArticle] = []
    for article in raw_articles:
        if not isinstance(article, dict):
            continue
        published_date = _normalize_text(article.get("publication_date"))
        if not published_date:
            published_date = _normalize_text(article.get("publication_year"))
        records.append(
            IeeeArticle(
                title=_normalize_text(article.get("title")),
                authors=parse_authors(article),
                journal=(
                    _normalize_text(article.get("publication_title"))
                    or _normalize_text(article.get("publisher"))
                ),
                published_date=published_date,
                abstract=_normalize_text(article.get("abstract")),
                doi=_normalize_text(article.get("doi")),
                url=first_url(article),
                content_type=_normalize_text(article.get("content_type")),
                conference_location=_normalize_text(article.get("conference_location")),
                conference_dates=_normalize_text(article.get("conference_dates")),
            )
        )
    return records


def search_ieee_articles(
    api_key: str,
    keywords: str,
    start_record: int = 1,
    max_records: int = 10,
    sort_field: str | None = None,
    sort_order: str | None = None,
    content_type: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    timeout: int = 30,
    proxy: str | None = None,
) -> list[IeeeArticle]:
    payload = fetch_json(
        build_search_url(
            api_key=api_key,
            keywords=keywords,
            start_record=start_record,
            max_records=max_records,
            sort_field=sort_field,
            sort_order=sort_order,
            content_type=content_type,
            start_year=start_year,
            end_year=end_year,
        ),
        timeout=timeout,
        proxy=proxy,
    )
    return parse_articles(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search IEEE Xplore metadata by keyword.")
    parser.add_argument("--keywords", required=True, help="Keyword phrase to search.")
    parser.add_argument("--start-record", type=int, default=1, help="Start record index.")
    parser.add_argument("--max-records", type=int, default=10, help="Max records per request.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--api-key", default=API_KEY, help="IEEE API key.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        records = search_ieee_articles(
            api_key=args.api_key,
            keywords=args.keywords,
            start_record=args.start_record,
            max_records=args.max_records,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(exc)
        return 1

    if not records:
        print("No results.")
        return 0

    for index, article in enumerate(records, start=1):
        print(f"[{index}] {article.title or 'Untitled'}")
        print(f"    Authors: {', '.join(article.authors) if article.authors else 'Unknown'}")
        print(f"    Journal: {article.journal or 'Unknown'}")
        print(f"    Date: {article.published_date or 'Unknown'}")
        print(f"    Abstract: {article.abstract or 'N/A'}")
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
