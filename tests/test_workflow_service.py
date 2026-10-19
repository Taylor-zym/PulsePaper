from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from IEEE.main import IeeeArticle
import workflow_service
from Springer.main import Record
from workflow_service import (
    RecommendationCandidate,
    WorkflowRequest,
    _extract_first_success_key,
    _merge_keywords,
    _parse_ieee_publication_date,
    _write_recommendations_to_zotero,
    run_daily_recommendation_workflow,
)


class FakeZoteroClient:
    def __init__(self, fail_full: bool = False, fail_all: bool = False) -> None:
        self.fail_full = fail_full
        self.fail_all = fail_all
        self.collection_key = "COLL123"
        self.item_counter = 0
        self.create_item_payloads: list[dict[str, Any]] = []

    def everything(self, value: Any) -> Any:
        return value

    def collections(self) -> list[dict[str, Any]]:
        return []

    def create_collections(self, _payload: list[dict[str, Any]]) -> dict[str, Any]:
        return {"success": {"0": self.collection_key}}

    def item_template(self, item_type: str) -> dict[str, Any]:
        return {
            "itemType": item_type,
            "title": "",
            "creators": [],
            "abstractNote": "",
            "publicationTitle": "",
            "repository": "",
            "date": "",
            "DOI": "",
            "url": "",
            "extra": "",
            "tags": [],
            "collections": [],
            "relations": {},
        }

    def create_items(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        payload = payloads[0]
        self.create_item_payloads.append(payload)

        has_enrichment = bool(
            payload.get("abstractNote")
            or payload.get("publicationTitle")
            or payload.get("repository")
            or payload.get("extra")
            or payload.get("tags")
        )

        if self.fail_full and has_enrichment:
            raise RuntimeError("full payload rejected")
        if self.fail_all:
            raise RuntimeError("fallback payload rejected")

        self.item_counter += 1
        return {"success": {"0": f"ITEM{self.item_counter}"}}


def _build_request() -> WorkflowRequest:
    return WorkflowRequest(zotero_id="test-id", zotero_key="test-key")


def _build_candidate() -> RecommendationCandidate:
    return RecommendationCandidate(
        source="arXiv",
        title="PulsePaper Workflow Test",
        authors=["Taylor Debug"],
        journal="arXiv / cs.AI",
        published_date="2026-03-26",
        summary="workflow import test summary",
        url="https://example.com/paper",
        doi="",
        matched_keywords=["debug"],
        score=0.5,
    )


def test_extract_first_success_key_supports_successful_payload() -> None:
    response = {
        "successful": {
            "0": {
                "key": "ABC123",
            }
        }
    }

    assert _extract_first_success_key(response) == "ABC123"


def test_parse_ieee_publication_date_supports_multiple_formats() -> None:
    assert _parse_ieee_publication_date("2026-03-26") > _parse_ieee_publication_date("2025")
    assert _parse_ieee_publication_date("20-22 Feb. 2023") > _parse_ieee_publication_date(
        "5-7 Dec. 2022"
    )


def test_build_ieee_candidates_filters_conference_articles() -> None:
    candidates = workflow_service._build_ieee_candidates(
        [
            IeeeArticle(
                title="Conference Paper",
                authors=["A"],
                journal="Proceedings of Something",
                published_date="2025",
                abstract="Conference abstract",
                doi="10.1/conf",
                url="https://example.com/conf",
                content_type="Conferences",
                conference_location="Shanghai",
                conference_dates="1-3 Jan. 2025",
            ),
            IeeeArticle(
                title="Journal Paper",
                authors=["B"],
                journal="IEEE Transactions on Pattern Analysis and Machine Intelligence",
                published_date="2026-01-01",
                abstract="Journal abstract",
                doi="10.1/journal",
                url="https://example.com/journal",
                content_type="Journals",
            ),
            IeeeArticle(
                title="Neural Workshop Paper",
                authors=["C"],
                journal="International Workshop on Neural Systems",
                published_date="2026-02-01",
                abstract="Workshop abstract",
                doi="10.1/workshop",
                url="https://example.com/workshop",
                content_type="Journals",
            ),
        ],
        "machine learning",
    )

    assert len(candidates) == 1
    assert candidates[0].title == "Journal Paper"
    assert candidates[0].source == "IEEE"


def test_write_recommendations_to_zotero_falls_back_to_minimal_payload(monkeypatch) -> None:
    client = FakeZoteroClient(fail_full=True)
    monkeypatch.setattr(workflow_service.zotero, "Zotero", lambda *_args: client)

    summary = _write_recommendations_to_zotero(_build_request(), [_build_candidate()])

    assert summary["created_count"] == 1
    assert summary["fallback_created_count"] == 1
    assert summary["failed_count"] == 0
    assert len(client.create_item_payloads) == 2
    assert client.create_item_payloads[0]["tags"]
    assert client.create_item_payloads[0]["abstractNote"] == "workflow import test summary"
    assert client.create_item_payloads[1]["tags"] == []
    assert client.create_item_payloads[1]["abstractNote"] == ""


def test_write_recommendations_to_zotero_reports_failure_details(monkeypatch) -> None:
    client = FakeZoteroClient(fail_full=True, fail_all=True)
    monkeypatch.setattr(workflow_service.zotero, "Zotero", lambda *_args: client)

    summary = _write_recommendations_to_zotero(_build_request(), [_build_candidate()])

    assert summary["created_count"] == 0
    assert summary["fallback_created_count"] == 0
    assert summary["failed_count"] == 1
    assert summary["failed_titles"] == ["PulsePaper Workflow Test"]
    assert summary["failure_details"][0]["primary_error"] == "full payload rejected"
    assert summary["failure_details"][0]["fallback_error"] == "fallback payload rejected"
    assert "PulsePaper Workflow Test" in summary["first_failure"]


def test_merge_keywords_deduplicates_case_insensitively() -> None:
    merged = _merge_keywords(
        ["Graph Learning", "Vision Transformer"],
        ["graph learning", "Diffusion Models", ""],
    )

    assert merged == ["Graph Learning", "Vision Transformer", "Diffusion Models"]


def test_workflow_request_allows_empty_keyword_api_when_service_configured(monkeypatch) -> None:
    monkeypatch.setattr(workflow_service, "has_keyword_api_key_configured", lambda: True)
    request = WorkflowRequest(
        zotero_id="test-id",
        zotero_key="test-key",
        keyword_api_key=None,
        include_arxiv=False,
        include_springer=True,
        include_elsevier=False,
        include_ieee=False,
        springer_api_key="springer-key",
    )
    request.validate()


def test_workflow_request_requires_keyword_api_without_service_config(monkeypatch) -> None:
    monkeypatch.setattr(workflow_service, "has_keyword_api_key_configured", lambda: False)
    request = WorkflowRequest(
        zotero_id="test-id",
        zotero_key="test-key",
        keyword_api_key=None,
        include_arxiv=False,
        include_springer=True,
        include_elsevier=False,
        include_ieee=False,
        springer_api_key="springer-key",
    )
    try:
        request.validate()
    except ValueError as exc:
        assert "A keyword service API key is required" in str(exc)
    else:
        raise AssertionError("Expected ValueError when no Gemini key is configured.")


def test_run_daily_recommendation_workflow_includes_extra_keywords(monkeypatch) -> None:
    search_calls: list[tuple[str, int]] = []

    corpus = [
        {
            "data": {
                "title": "Graph Learning for Vision",
                "abstractNote": "Test abstract",
                "collections": [],
            },
            "paths": [],
        }
    ]

    def fake_search_records(
        api_key: str,
        keywords: str,
        start: int = 1,
        page_length: int = 10,
    ) -> list[Record]:
        search_calls.append((keywords, page_length))
        return [
            Record(
                title=f"{keywords} paper",
                authors=["Taylor Debug"],
                journal="Test Journal",
                published_date="2026-03-26",
                abstract=f"Summary for {keywords}",
                doi=f"10.1000/{keywords.replace(' ', '-')}",
                url="https://example.com",
            )
        ]

    monkeypatch.setattr(workflow_service, "_get_zotero_corpus", lambda *_args: corpus)
    monkeypatch.setattr(workflow_service, "sample_titles", lambda titles, sample_size=0.9: titles)
    monkeypatch.setattr(
        workflow_service,
        "generate_keywords_from_titles",
        lambda *args, **kwargs: "graph learning\nvision transformer",
    )
    monkeypatch.setattr(
        workflow_service,
        "generate_keywords_from_text",
        lambda *args, **kwargs: "diffusion models\ngraph learning\nremote sensing",
    )
    monkeypatch.setattr(workflow_service, "search_records", fake_search_records)
    monkeypatch.setattr(
        workflow_service,
        "rerank_paper",
        lambda candidates, _corpus, **_kwargs: [
            setattr(candidate, "score", float(len(candidates) - index))
            for index, candidate in enumerate(candidates)
        ],
    )
    monkeypatch.setattr(workflow_service, "_attach_journal_ranks", lambda *_args, **_kwargs: [])

    request = WorkflowRequest(
        zotero_id="test-id",
        zotero_key="test-key",
        keyword_api_key="keyword-key",
        springer_api_key="springer-key",
        include_arxiv=False,
        include_springer=True,
        include_elsevier=False,
        selected_keyword_count=2,
        extra_keyword_input="diffusion for remote sensing",
        extra_keyword_count=3,
        extra_candidate_pool_per_source=20,
        top_k=4,
    )

    result = run_daily_recommendation_workflow(request)

    assert result.keyword_snapshot == ["graph learning", "vision transformer"]
    assert result.zotero_selected_keywords == ["graph learning", "vision transformer"]
    assert result.selected_keywords == ["graph learning", "vision transformer"]
    assert result.extra_keyword_snapshot == [
        "diffusion models",
        "graph learning",
        "remote sensing",
    ]
    assert result.extra_selected_keywords == [
        "diffusion models",
        "graph learning",
        "remote sensing",
    ]
    assert result.retrieval_keywords == [
        "graph learning",
        "vision transformer",
        "diffusion models",
        "remote sensing",
    ]
    assert search_calls == [
        ("graph learning", 200),
        ("vision transformer", 200),
        ("diffusion models", 20),
        ("remote sensing", 20),
    ]
    assert len(result.recommendations) == 4


def test_run_daily_recommendation_workflow_supports_direct_sentence_search(monkeypatch) -> None:
    search_calls: list[tuple[str, int]] = []
    generated_calls: list[tuple[str, str, int]] = []

    corpus = [
        {
            "data": {
                "title": "Graph Learning for Vision",
                "abstractNote": "Test abstract",
                "collections": [],
            },
            "paths": [],
        }
    ]

    def fake_search_records(
        api_key: str,
        keywords: str,
        start: int = 1,
        page_length: int = 10,
    ) -> list[Record]:
        search_calls.append((keywords, page_length))
        return [
            Record(
                title=f"{keywords} paper",
                authors=["Taylor Debug"],
                journal="Test Journal",
                published_date="2026-03-26",
                abstract=f"Summary for {keywords}",
                doi=f"10.2000/{len(search_calls)}",
                url="https://example.com",
            )
        ]

    def fail_generate_titles(*_args, **_kwargs):
        raise AssertionError("title keyword generation should be skipped in direct sentence search mode")

    def fake_generate_from_text(
        text: str,
        api_key: str,
        count: int = 3,
        base_url: str = "",
        model: str = "",
    ) -> str:
        generated_calls.append((text, api_key, count))
        return "diffusion transformer\nremote sensing"

    monkeypatch.setattr(workflow_service, "_get_zotero_corpus", lambda *_args: corpus)
    monkeypatch.setattr(workflow_service, "generate_keywords_from_titles", fail_generate_titles)
    monkeypatch.setattr(workflow_service, "generate_keywords_from_text", fake_generate_from_text)
    monkeypatch.setattr(workflow_service, "search_records", fake_search_records)
    monkeypatch.setattr(
        workflow_service,
        "rerank_paper",
        lambda candidates, _corpus, **_kwargs: [
            setattr(candidate, "score", float(len(candidates) - index))
            for index, candidate in enumerate(candidates)
        ],
    )
    monkeypatch.setattr(workflow_service, "_attach_journal_ranks", lambda *_args, **_kwargs: [])

    request = WorkflowRequest(
        zotero_id="test-id",
        zotero_key="test-key",
        keyword_api_key="keyword-key",
        springer_api_key="springer-key",
        include_arxiv=False,
        include_springer=True,
        include_elsevier=False,
        direct_sentence_search=True,
        extra_keyword_input="diffusion transformer remote sensing",
        top_k=1,
    )

    result = run_daily_recommendation_workflow(request)

    assert result.direct_sentence_search is True
    assert result.keyword_snapshot == []
    assert result.zotero_selected_keywords == []
    assert result.selected_keywords == []
    assert result.extra_keyword_snapshot == ["diffusion transformer", "remote sensing"]
    assert result.extra_selected_keywords == ["diffusion transformer", "remote sensing"]
    assert result.retrieval_keywords == ["diffusion transformer", "remote sensing"]
    assert generated_calls == [("diffusion transformer remote sensing", "keyword-key", 3)]
    assert search_calls == [("diffusion transformer", 20), ("remote sensing", 20)]
    assert len(result.recommendations) == 1


def test_run_daily_recommendation_workflow_fetches_ieee_once_with_100_per_keyword(monkeypatch) -> None:
    ieee_calls: list[
        tuple[str, int, int, str | None, str | None, str | None, int | None, int | None]
    ] = []

    corpus = [
        {
            "data": {
                "title": "Graph Learning for Vision",
                "abstractNote": "Test abstract",
                "collections": [],
            },
            "paths": [],
        }
    ]

    def fake_search_ieee_articles(
        api_key: str,
        keywords: str,
        start_record: int = 1,
        max_records: int = 100,
        sort_field: str | None = None,
        sort_order: str | None = None,
        content_type: str | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
        timeout: int = 30,
        proxy: str | None = None,
    ) -> list[IeeeArticle]:
        ieee_calls.append(
            (
                keywords,
                start_record,
                max_records,
                sort_field,
                sort_order,
                content_type,
                start_year,
                end_year,
            )
        )
        return [
            IeeeArticle(
                title=f"{keywords} {start_record + offset}",
                authors=["Taylor Debug"],
                journal="IEEE Access",
                published_date="2026-03-26",
                abstract=f"Summary for {keywords} {start_record + offset}",
                doi=f"10.3000/{keywords.replace(' ', '-')}-{start_record + offset}",
                url="https://ieeexplore.ieee.org/document/1",
            )
            for offset in range(max_records)
        ]

    monkeypatch.setattr(workflow_service, "_get_zotero_corpus", lambda *_args: corpus)
    monkeypatch.setattr(workflow_service, "sample_titles", lambda titles, sample_size=0.9: titles)
    monkeypatch.setattr(
        workflow_service,
        "generate_keywords_from_titles",
        lambda *args, **kwargs: "graph learning",
    )
    monkeypatch.setattr(
        workflow_service,
        "generate_keywords_from_text",
        lambda *args, **kwargs: "edge ai",
    )
    monkeypatch.setattr(workflow_service, "search_ieee_articles", fake_search_ieee_articles)
    monkeypatch.setattr(
        workflow_service,
        "rerank_paper",
        lambda candidates, _corpus, **_kwargs: [
            setattr(candidate, "score", float(len(candidates) - index))
            for index, candidate in enumerate(candidates)
        ],
    )
    monkeypatch.setattr(workflow_service, "_attach_journal_ranks", lambda *_args, **_kwargs: [])

    request = WorkflowRequest(
        zotero_id="test-id",
        zotero_key="test-key",
        keyword_api_key="keyword-key",
        ieee_api_key="ieee-key",
        include_arxiv=False,
        include_springer=False,
        include_elsevier=False,
        include_ieee=True,
        selected_keyword_count=1,
        extra_keyword_input="edge ai",
        extra_keyword_count=1,
        top_k=4,
    )

    result = run_daily_recommendation_workflow(request)

    year = workflow_service.datetime.now().year
    expected_calls: list[
        tuple[str, int, int, str | None, str | None, str | None, int | None, int | None]
    ] = []
    for keyword in ("graph learning", "edge ai"):
        for content_type in workflow_service.IEEE_ALLOWED_CONTENT_TYPES:
            expected_calls.append(
                (
                    keyword,
                    1,
                    100,
                    "article_number",
                    "desc",
                    content_type,
                    year,
                    year,
                )
            )

    assert ieee_calls == [
        *expected_calls,
    ]
    assert result.source_counts["IEEE"] == 200
    assert len(result.recommendations) == 4


def test_run_daily_recommendation_workflow_prioritizes_recent_ieee_articles(monkeypatch) -> None:
    corpus = [
        {
            "data": {
                "title": "Graph Learning for Vision",
                "abstractNote": "Test abstract",
                "collections": [],
            },
            "paths": [],
        }
    ]

    def fake_search_ieee_articles(
        api_key: str,
        keywords: str,
        start_record: int = 1,
        max_records: int = 100,
        sort_field: str | None = None,
        sort_order: str | None = None,
        content_type: str | None = None,
        start_year: int | None = None,
        end_year: int | None = None,
        timeout: int = 30,
        proxy: str | None = None,
    ) -> list[IeeeArticle]:
        return [
            IeeeArticle(
                title="Old IEEE Paper",
                authors=["Author A"],
                journal="IEEE Journal",
                published_date="2019-01-01",
                abstract="old summary",
                doi="10.1000/old",
                url="https://ieeexplore.ieee.org/document/old",
                content_type="Journals",
            ),
            IeeeArticle(
                title="Newest IEEE Paper",
                authors=["Author B"],
                journal="IEEE Journal",
                published_date="2025-10-01",
                abstract="new summary",
                doi="10.1000/new",
                url="https://ieeexplore.ieee.org/document/new",
                content_type="Journals",
            ),
            IeeeArticle(
                title="Middle IEEE Paper",
                authors=["Author C"],
                journal="IEEE Journal",
                published_date="2023-06-15",
                abstract="mid summary",
                doi="10.1000/mid",
                url="https://ieeexplore.ieee.org/document/mid",
                content_type="Journals",
            ),
        ]

    def fake_rerank(
        candidates: list[RecommendationCandidate],
        _corpus: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> None:
        # Simulate semantic scoring that would normally prefer older papers.
        score_map = {
            "Old IEEE Paper": 0.99,
            "Middle IEEE Paper": 0.50,
            "Newest IEEE Paper": 0.20,
        }
        for candidate in candidates:
            candidate.score = score_map.get(candidate.title, 0.0)

    monkeypatch.setattr(workflow_service, "_get_zotero_corpus", lambda *_args: corpus)
    monkeypatch.setattr(workflow_service, "sample_titles", lambda titles, sample_size=0.9: titles)
    monkeypatch.setattr(
        workflow_service,
        "generate_keywords_from_titles",
        lambda *args, **kwargs: "graph learning",
    )
    monkeypatch.setattr(workflow_service, "search_ieee_articles", fake_search_ieee_articles)
    monkeypatch.setattr(workflow_service, "rerank_paper", fake_rerank)
    monkeypatch.setattr(workflow_service, "_attach_journal_ranks", lambda *_args, **_kwargs: [])

    request = WorkflowRequest(
        zotero_id="test-id",
        zotero_key="test-key",
        keyword_api_key="keyword-key",
        ieee_api_key="ieee-key",
        include_arxiv=False,
        include_springer=False,
        include_elsevier=False,
        include_ieee=True,
        selected_keyword_count=1,
        top_k=3,
    )

    result = run_daily_recommendation_workflow(request)
    assert [item.title for item in result.recommendations] == [
        "Newest IEEE Paper",
        "Middle IEEE Paper",
        "Old IEEE Paper",
    ]
