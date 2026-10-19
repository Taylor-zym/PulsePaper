from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import arxiv
import feedparser

LOGGER = logging.getLogger("arxiv_source")
ARXIV_ID_BATCH_SIZE = 50
ARXIV_PAGE_SIZE = 100


@dataclass(slots=True)
class ArxivRecord:
    title: str
    authors: list[str]
    journal: str
    published_date: str | None
    abstract: str
    doi: str | None
    url: str | None


def _get_pdf_url_patch(links) -> str | None:
    pdf_urls = [link.href for link in links if "pdf" in link.href]
    if not pdf_urls:
        return None
    return pdf_urls[0]


def _patch_arxiv_result() -> None:
    if getattr(arxiv.Result, "_get_pdf_url", None) is _get_pdf_url_patch:
        return
    arxiv.Result._get_pdf_url = _get_pdf_url_patch


def fetch_arxiv_papers(query: str, debug: bool = False) -> list[ArxivRecord]:
    _patch_arxiv_result()
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("arxiv_query is required.")

    feed = feedparser.parse(f"https://rss.arxiv.org/atom/{cleaned_query}")
    feed_title = getattr(getattr(feed, "feed", None), "title", "") or ""
    if "Feed error for query" in feed_title:
        raise RuntimeError(f"Invalid ARXIV_QUERY: {cleaned_query}.")

    client = arxiv.Client(
        num_retries=10,
        delay_seconds=3,
        page_size=ARXIV_PAGE_SIZE,
    )
    if debug:
        LOGGER.debug("Retrieve 5 arXiv papers regardless of the date.")
        search = arxiv.Search(
            query="cat:cs.AI",
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )
        papers: list[ArxivRecord] = []
        for result in client.results(search):
            papers.append(_build_record(result))
            if len(papers) == 5:
                break
        return papers

    paper_ids = [
        entry.id.removeprefix("oai:arXiv.org:")
        for entry in getattr(feed, "entries", [])
        if getattr(entry, "arxiv_announce_type", None) == "new"
    ]
    papers: list[ArxivRecord] = []
    for start in range(0, len(paper_ids), ARXIV_ID_BATCH_SIZE):
        batch_ids = paper_ids[start : start + ARXIV_ID_BATCH_SIZE]
        if not batch_ids:
            continue
        search = arxiv.Search(id_list=batch_ids)
        papers.extend(_build_record(result) for result in client.results(search))
    return papers


def _build_record(result: arxiv.Result) -> ArxivRecord:
    title = (result.title or "").strip()
    abstract = (result.summary or "").strip()
    authors = [
        author.name.strip()
        for author in result.authors
        if getattr(author, "name", None) and author.name.strip()
    ]
    journal = (result.journal_ref or "").strip() or f"arXiv / {result.primary_category}"
    published_date = _format_date(result.published)
    url = (result.entry_id or "").strip() or (result.pdf_url or "").strip() or None
    doi = (result.doi or "").strip() or None
    return ArxivRecord(
        title=title,
        authors=authors,
        journal=journal,
        published_date=published_date,
        abstract=abstract,
        doi=doi,
        url=url,
    )


def _format_date(value: datetime | str | None) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None
