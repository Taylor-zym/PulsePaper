from __future__ import annotations

import os
import re,random
from fnmatch import fnmatch
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Mapping

from arxiv_source import ArxivRecord, fetch_arxiv_papers
from config import (
    get_bool as get_config_bool,
    get_float as get_config_float,
    get_int as get_config_int,
    get_text as get_config_text,
    has_text as has_config_text,
)
from pyzotero import zotero

from Elsevier.main import Article, search_articles
from IEEE.main import IeeeArticle, search_ieee_articles
from Springer.main import Record, search_records
from journal_rank import DEFAULT_DISPLAY_FIELDS as JOURNAL_RANK_FIELDS
from journal_rank import get_publication_rank_summary
from zotero_keyword_generator import (
    DEFAULT_EXTRA_KEYWORD_COUNT,
    SILICONFLOW_BASE_URL,
    SILICONFLOW_MODEL,
    generate_keywords_from_titles,
    generate_keywords_from_text,
    has_keyword_api_key_configured,
    sample_titles,
)

ProgressCallback = Callable[[str, str, dict[str, Any] | None], None]
DEFAULT_ARXIV_QUERY = "cs.AI+cs.CV+cs.LG+cs.CL"
PULSEPAPER_COLLECTION_ROOT = "PulsePaper"
IEEE_SINGLE_REQUEST_LIMIT = 100
IEEE_PER_KEYWORD_LIMIT = IEEE_SINGLE_REQUEST_LIMIT
IEEE_SORT_FIELD = "article_number"
IEEE_SORT_ORDER = "desc"
IEEE_ALLOWED_CONTENT_TYPES = (
    "Early Access",
    "Journals",
    "Journals,Magazines",
    "Magazines",
)
IEEE_MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(slots=True)
class WorkflowRequest:
    zotero_id: str
    zotero_key: str
    keyword_api_key: str | None = None
    zotero_ignore: str = ""
    sample_ratio: float = 0.9
    selected_keyword_count: int = 3
    candidate_pool_per_source: int = 200
    top_k: int = 10
    include_arxiv: bool = False
    include_springer: bool = True
    include_elsevier: bool = True
    include_ieee: bool = False
    write_to_zotero: bool = False
    arxiv_query: str | None = None
    springer_api_key: str | None = None
    elsevier_api_key: str | None = None
    ieee_api_key: str | None = None
    keyword_base_url: str = SILICONFLOW_BASE_URL
    keyword_model: str = SILICONFLOW_MODEL
    extra_keyword_input: str = ""
    direct_sentence_search: bool = False
    extra_keyword_count: int = DEFAULT_EXTRA_KEYWORD_COUNT
    extra_candidate_pool_per_source: int = 20
    embedding_device: str = "auto"
    embedding_proxy: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WorkflowRequest":
        if not isinstance(payload, Mapping):
            raise ValueError("Request body must be a JSON object.")

        default_embedding_proxy = (
            get_config_text("workflow", "embedding_proxy", default="")
            or os.getenv("EMBEDDING_PROXY")
            or os.getenv("HF_PROXY")
            or os.getenv("HUGGINGFACE_PROXY")
        )
        request = cls(
            zotero_id=_require_text(
                payload,
                "zotero_id",
                get_config_text("zotero", "library_id", default=""),
            ),
            zotero_key=_require_text(
                payload,
                "zotero_key",
                get_config_text("zotero", "api_key", default=""),
            ),
            keyword_api_key=_optional_text(
                payload.get("keyword_api_key"),
                get_config_text(
                    "providers",
                    "gemini_api_key",
                    default=(
                        get_config_text("providers", "keyword_api_key", default="")
                        or os.getenv("GEMINI_API_KEY")
                        or os.getenv("SILICONFLOW_API_KEY")
                    ),
                ),
            ),
            zotero_ignore=_optional_text(
                payload.get("zotero_ignore"),
                get_config_text("workflow", "zotero_ignore", default="") or "",
            ),
            sample_ratio=_as_float(
                payload.get(
                    "sample_ratio",
                    get_config_float("workflow", "sample_ratio", default=0.9),
                )
            ),
            selected_keyword_count=_as_int(
                payload.get(
                    "selected_keyword_count",
                    get_config_int("workflow", "selected_keyword_count", default=3),
                )
            ),
            candidate_pool_per_source=_as_int(
                payload.get(
                    "candidate_pool_per_source",
                    get_config_int("workflow", "candidate_pool_per_source", default=200),
                )
            ),
            top_k=_as_int(
                payload.get("top_k", get_config_int("workflow", "top_k", default=10))
            ),
            include_arxiv=_as_bool(
                payload.get("include_arxiv"),
                default=get_config_bool("workflow", "include_arxiv", default=True),
            ),
            include_springer=_as_bool(
                payload.get("include_springer"),
                default=get_config_bool("workflow", "include_springer", default=True),
            ),
            include_elsevier=_as_bool(
                payload.get("include_elsevier"),
                default=get_config_bool("workflow", "include_elsevier", default=True),
            ),
            include_ieee=_as_bool(
                payload.get("include_ieee"),
                default=get_config_bool("workflow", "include_ieee", default=True),
            ),
            write_to_zotero=_as_bool(
                payload.get("write_to_zotero"),
                default=get_config_bool("workflow", "write_to_zotero", default=False),
            ),
            arxiv_query=_optional_text(
                payload.get("arxiv_query"),
                get_config_text(
                    "workflow",
                    "arxiv_query",
                    default=os.getenv("ARXIV_QUERY") or DEFAULT_ARXIV_QUERY,
                ),
            ),
            springer_api_key=_optional_secret(
                payload.get("springer_api_key"),
                get_config_text(
                    "providers",
                    "springer_api_key",
                    default=os.getenv("SPRINGER_API_KEY"),
                ),
            ),
            elsevier_api_key=_optional_secret(
                payload.get("elsevier_api_key"),
                get_config_text(
                    "providers",
                    "elsevier_api_key",
                    default=os.getenv("ELSEVIER_API_KEY"),
                ),
            ),
            ieee_api_key=_optional_secret(
                payload.get("ieee_api_key"),
                get_config_text(
                    "providers",
                    "ieee_api_key",
                    default=os.getenv("IEEE_API_KEY"),
                ),
            ),
            keyword_base_url=_optional_text(
                payload.get("keyword_base_url"),
                get_config_text("keyword_service", "base_url", default=SILICONFLOW_BASE_URL),
            ),
            keyword_model=_optional_text(
                payload.get("keyword_model"),
                get_config_text("keyword_service", "model", default=SILICONFLOW_MODEL),
            ),
            extra_keyword_input=_optional_text(
                payload.get("extra_keyword_input"),
                "",
            )
            or "",
            direct_sentence_search=_as_bool(
                payload.get("direct_sentence_search"),
                default=False,
            ),
            extra_keyword_count=_as_int(
                payload.get(
                    "extra_keyword_count",
                    get_config_int(
                        "workflow",
                        "extra_keyword_count",
                        default=DEFAULT_EXTRA_KEYWORD_COUNT,
                    ),
                )
            ),
            extra_candidate_pool_per_source=_as_int(
                payload.get(
                    "extra_candidate_pool_per_source",
                    get_config_int(
                        "workflow",
                        "extra_candidate_pool_per_source",
                        default=20,
                    ),
                )
            ),
            embedding_device=_optional_text(
                payload.get("embedding_device"),
                get_config_text("workflow", "embedding_device", default="auto"),
            ),
            embedding_proxy=_optional_text(
                payload.get("embedding_proxy"),
                default_embedding_proxy,
            ),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if not 0 < self.sample_ratio <= 1:
            raise ValueError("sample_ratio must be between 0 and 1.")
        if self.selected_keyword_count < 1:
            raise ValueError("selected_keyword_count must be >= 1.")
        if self.selected_keyword_count > 5:
            raise ValueError("selected_keyword_count must be <= 5.")
        if self.extra_keyword_count < 1:
            raise ValueError("extra_keyword_count must be >= 1.")
        if self.extra_keyword_count > 5:
            raise ValueError("extra_keyword_count must be <= 5.")
        if self.extra_candidate_pool_per_source < 1:
            raise ValueError("extra_candidate_pool_per_source must be >= 1.")
        if self.extra_candidate_pool_per_source > 200:
            raise ValueError("extra_candidate_pool_per_source must be <= 200.")
        if self.candidate_pool_per_source < 1:
            raise ValueError("candidate_pool_per_source must be >= 1.")
        if self.candidate_pool_per_source > 200:
            raise ValueError("candidate_pool_per_source must be <= 200.")
        if self.top_k < 1:
            raise ValueError("top_k must be >= 1.")
        if (
            not self.include_arxiv
            and not self.include_springer
            and not self.include_elsevier
            and not self.include_ieee
        ):
            raise ValueError("At least one search source must be enabled.")
        if self.direct_sentence_search and not _optional_text(self.extra_keyword_input):
            raise ValueError("extra_keyword_input is required when direct_sentence_search is enabled.")
        if (
            _uses_keyword_sources(self)
            and not _optional_text(self.keyword_api_key)
            and not has_keyword_api_key_configured()
        ):
            raise ValueError(
                "A keyword service API key is required when non-arXiv sources are enabled."
            )
        if self.include_arxiv and not _optional_text(self.arxiv_query):
            raise ValueError("arxiv_query is required when arXiv search is enabled.")
        if self.include_springer and not _optional_text(self.springer_api_key):
            raise ValueError("springer_api_key is required when Springer search is enabled.")
        if self.include_elsevier and not _optional_text(self.elsevier_api_key):
            raise ValueError("elsevier_api_key is required when Elsevier search is enabled.")
        if self.include_ieee and not _optional_text(self.ieee_api_key):
            raise ValueError("ieee_api_key is required when IEEE search is enabled.")

    def enabled_sources(self) -> list[str]:
        sources: list[str] = []
        if self.include_arxiv:
            sources.append("arXiv")
        if self.include_springer:
            sources.append("Springer")
        if self.include_elsevier:
            sources.append("Elsevier")
        if self.include_ieee:
            sources.append("IEEE")
        return sources

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "zotero_id": self.zotero_id,
            "sample_ratio": self.sample_ratio,
            "selected_keyword_count": self.selected_keyword_count,
            "candidate_pool_per_source": self.candidate_pool_per_source,
            "top_k": self.top_k,
            "enabled_sources": self.enabled_sources(),
            "write_to_zotero": self.write_to_zotero,
            "arxiv_query": self.arxiv_query,
            "keyword_model": self.keyword_model,
            "keyword_base_url": self.keyword_base_url,
            "extra_keyword_input_present": bool(self.extra_keyword_input.strip()),
            "direct_sentence_search": self.direct_sentence_search,
            "extra_keyword_count": self.extra_keyword_count,
            "extra_candidate_pool_per_source": self.extra_candidate_pool_per_source,
            "embedding_device": self.embedding_device,
            "embedding_proxy": bool(self.embedding_proxy),
            "zotero_ignore_rules": len(
                [line for line in self.zotero_ignore.splitlines() if line.strip()]
            ),
            "key_fingerprints": {
                "keyword_api_key": _redact_secret(self.keyword_api_key),
                "springer_api_key": _redact_secret(self.springer_api_key),
                "elsevier_api_key": _redact_secret(self.elsevier_api_key),
                "ieee_api_key": _redact_secret(self.ieee_api_key),
            },
        }


@dataclass(slots=True)
class RecommendationCandidate:
    source: str
    title: str
    authors: list[str]
    journal: str | None
    published_date: str | None
    summary: str
    url: str | None
    doi: str | None
    journal_rank: dict[str, str | None] | None = None
    matched_keywords: list[str] = field(default_factory=list)
    score: float = 0.0

    def add_keyword(self, keyword: str) -> None:
        if keyword not in self.matched_keywords:
            self.matched_keywords.append(keyword)

    def merge_from(self, other: "RecommendationCandidate") -> None:
        for keyword in other.matched_keywords:
            self.add_keyword(keyword)
        if not self.summary and other.summary:
            self.summary = other.summary
        if not self.url and other.url:
            self.url = other.url
        if not self.doi and other.doi:
            self.doi = other.doi
        if not self.journal and other.journal:
            self.journal = other.journal
        if not self.published_date and other.published_date:
            self.published_date = other.published_date
        if not self.journal_rank and other.journal_rank:
            self.journal_rank = dict(other.journal_rank)
        if len(other.authors) > len(self.authors):
            self.authors = list(other.authors)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["year"] = _extract_year(self.published_date)
        return data


@dataclass(slots=True)
class WorkflowResult:
    run_date: str
    corpus_count: int
    sampled_title_count: int
    keyword_snapshot: list[str]
    extra_keyword_snapshot: list[str]
    zotero_selected_keywords: list[str]
    extra_selected_keywords: list[str]
    retrieval_keywords: list[str]
    direct_sentence_search: bool
    selected_keywords: list[str]
    candidate_count: int
    source_counts: dict[str, int]
    warnings: list[str]
    recommendations: list[RecommendationCandidate]
    zotero_write: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_date": self.run_date,
            "corpus_count": self.corpus_count,
            "sampled_title_count": self.sampled_title_count,
            "keyword_snapshot": list(self.keyword_snapshot),
            "extra_keyword_snapshot": list(self.extra_keyword_snapshot),
            "zotero_selected_keywords": list(self.zotero_selected_keywords),
            "extra_selected_keywords": list(self.extra_selected_keywords),
            "retrieval_keywords": list(self.retrieval_keywords),
            "direct_sentence_search": self.direct_sentence_search,
            "selected_keywords": list(self.selected_keywords),
            "candidate_count": self.candidate_count,
            "source_counts": dict(self.source_counts),
            "warnings": list(self.warnings),
            "recommendations": [candidate.to_dict() for candidate in self.recommendations],
            "zotero_write": dict(self.zotero_write) if self.zotero_write else None,
        }


def rerank_paper(
    candidates: list[RecommendationCandidate],
    corpus: list[dict],
    model: str | None = None,
    device: str | None = None,
    proxy: str | None = None,
):
    from recommender import rerank_paper as _rerank_paper

    kwargs: dict[str, Any] = {}
    if model is not None:
        kwargs["model"] = model
    if device is not None:
        kwargs["device"] = device
    if proxy is not None:
        kwargs["proxy"] = proxy
    return _rerank_paper(candidates, corpus, **kwargs)


def run_daily_recommendation_workflow(
    request: WorkflowRequest,
    progress: ProgressCallback | None = None,
) -> WorkflowResult:
    request.validate()
    _emit_progress(
        progress,
        "starting",
        "开始执行今日论文推荐工作流。",
        {"request": request.to_safe_dict()},
    )
    _emit_progress(progress, "reading_corpus", "读取 Zotero 文献语料。")
    try:
        corpus = _get_zotero_corpus(request.zotero_id, request.zotero_key)
    except Exception as exc:
        raise RuntimeError(_normalize_error("Zotero 语料读取", exc)) from exc
    if request.zotero_ignore.strip():
        corpus = _filter_corpus(corpus, request.zotero_ignore)

    if not corpus:
        raise RuntimeError("No Zotero papers with abstracts were found after filtering.")

    sampled_titles: list[str] = []
    keyword_snapshot: list[str] = []
    extra_keyword_snapshot: list[str] = []
    zotero_selected_keywords: list[str] = []
    extra_selected_keywords: list[str] = []
    extra_retrieval_keywords: list[str] = []
    retrieval_keywords: list[str] = []
    selected_keywords: list[str] = []
    if _uses_keyword_sources(request):
        if request.direct_sentence_search and request.extra_keyword_input.strip():
            _emit_progress(
                progress,
                "direct_sentence_search",
                "已启用一句话直查，跳过 Zotero 关键词生成并提炼输入检索词。",
                {
                    "direct_sentence_search": True,
                    "extra_keyword_input_present": True,
                    "extra_keyword_count": request.extra_keyword_count,
                },
            )
            try:
                raw_extra_keywords = generate_keywords_from_text(
                    request.extra_keyword_input,
                    api_key=request.keyword_api_key or "",
                    count=request.extra_keyword_count,
                    base_url=request.keyword_base_url,
                    model=request.keyword_model,
                )
            except Exception as exc:
                raise RuntimeError(
                    _normalize_error(
                        "一句话检索关键词生成",
                        exc,
                        auth_hint="请检查关键词服务配置中的 API Key、模型名称、base_url，以及网关是否支持 Anthropic /v1/messages 协议。",
                    )
                ) from exc
            extra_keyword_snapshot = _parse_keywords(raw_extra_keywords)[
                : request.extra_keyword_count
            ]
            if not extra_keyword_snapshot:
                extra_keyword_snapshot = [request.extra_keyword_input.strip()]
            extra_selected_keywords = _merge_keywords(extra_keyword_snapshot)
            extra_retrieval_keywords = list(extra_selected_keywords)
            retrieval_keywords = list(extra_retrieval_keywords)
            _emit_progress(
                progress,
                "keywords_ready",
                "一句话检索关键词已就绪，开始跨来源检索。",
                {
                    "keyword_snapshot": keyword_snapshot,
                    "extra_keyword_snapshot": extra_keyword_snapshot,
                    "zotero_selected_keywords": zotero_selected_keywords,
                    "extra_selected_keywords": extra_selected_keywords,
                    "extra_retrieval_keywords": extra_retrieval_keywords,
                    "retrieval_keywords": retrieval_keywords,
                    "selected_keywords": selected_keywords,
                    "direct_sentence_search": True,
                },
            )
        else:
            titles = _collect_titles(corpus)
            sampled_titles = sample_titles(titles, sample_size=request.sample_ratio)
            _emit_progress(
                progress,
                "generating_keywords",
                "根据 Zotero 标题生成研究关键词快照。",
                {
                    "corpus_count": len(corpus),
                    "sampled_title_count": len(sampled_titles),
                },
            )
            try:
                raw_keywords = generate_keywords_from_titles(
                    sampled_titles,
                    api_key=request.keyword_api_key,
                    base_url=request.keyword_base_url,
                    model=request.keyword_model,
                )
            except Exception as exc:
                raise RuntimeError(
                    _normalize_error(
                        "关键词快照生成",
                        exc,
                        auth_hint="请检查关键词服务配置中的 API Key、模型名称、base_url，以及网关是否支持 Anthropic /v1/messages 协议。",
                    )
                ) from exc
            keyword_snapshot = _parse_keywords(raw_keywords)
            k = min(request.selected_keyword_count, len(keyword_snapshot))
            zotero_selected_keywords = (
                list(keyword_snapshot) if k == len(keyword_snapshot) else random.sample(keyword_snapshot, k)
            )

            if request.extra_keyword_input.strip():
                _emit_progress(
                    progress,
                    "summarizing_extra_keywords",
                    "根据前端输入补充额外检索关键词。",
                    {
                        "extra_keyword_input_present": True,
                        "extra_keyword_count": request.extra_keyword_count,
                    },
                )
                try:
                    raw_extra_keywords = generate_keywords_from_text(
                        request.extra_keyword_input,
                        api_key=request.keyword_api_key or "",
                        count=request.extra_keyword_count,
                        base_url=request.keyword_base_url,
                        model=request.keyword_model,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        _normalize_error(
                            "额外关键词生成",
                            exc,
                            auth_hint="请检查关键词服务配置中的 API Key、模型名称、base_url，以及网关是否支持 Anthropic /v1/messages 协议。",
                        )
                    ) from exc
                extra_keyword_snapshot = _parse_keywords(raw_extra_keywords)[
                    : request.extra_keyword_count
                ]

            selected_keywords = list(zotero_selected_keywords)
            extra_selected_keywords = _merge_keywords(extra_keyword_snapshot)
            extra_retrieval_keywords = _subtract_keywords(
                extra_selected_keywords,
                selected_keywords,
            )
            retrieval_keywords = _merge_keywords(
                selected_keywords,
                extra_retrieval_keywords,
            )
            if not retrieval_keywords:
                raise RuntimeError("The keyword generator returned no usable keywords.")

            _emit_progress(
                progress,
                "keywords_ready",
                "关键词快照已生成，开始跨来源检索。",
                {
                    "keyword_snapshot": keyword_snapshot,
                    "extra_keyword_snapshot": extra_keyword_snapshot,
                    "zotero_selected_keywords": zotero_selected_keywords,
                    "extra_selected_keywords": extra_selected_keywords,
                    "extra_retrieval_keywords": extra_retrieval_keywords,
                    "retrieval_keywords": retrieval_keywords,
                    "selected_keywords": selected_keywords,
                    "direct_sentence_search": False,
                },
            )
        if not retrieval_keywords:
            raise RuntimeError("No usable retrieval keywords were produced.")
    else:
        _emit_progress(
            progress,
            "keywords_skipped",
            "已跳过关键词生成，使用原始 arXiv 推荐逻辑。",
        )

    warnings: list[str] = []
    candidates: list[RecommendationCandidate] = []
    source_counts = {source: 0 for source in request.enabled_sources()}

    if request.include_arxiv:
        _emit_progress(
            progress,
            "searching_sources",
            f"正在 arXiv 拉取每日新论文：{request.arxiv_query}",
            {"active_source": "arXiv", "arxiv_query": request.arxiv_query},
        )
        try:
            arxiv_papers = fetch_arxiv_papers(request.arxiv_query or "")
            arxiv_candidates = _build_arxiv_candidates(arxiv_papers)
            source_counts["arXiv"] += len(arxiv_candidates)
            candidates.extend(arxiv_candidates)
        except Exception as exc:
            warning = _normalize_error("arXiv 拉取", exc)
            warnings.append(warning)
            _emit_progress(
                progress,
                "source_warning",
                warning,
                {"active_source": "arXiv", "arxiv_query": request.arxiv_query},
            )

    for keyword in selected_keywords:
        if request.include_springer:
            _emit_progress(
                progress,
                "searching_sources",
                f"正在 Springer 检索：{keyword}",
                {
                    "active_keyword": keyword,
                    "active_source": "Springer",
                    "keyword_group": "zotero",
                    "candidate_pool_per_source": request.candidate_pool_per_source,
                },
            )
            try:
                springer_records = search_records(
                    api_key=request.springer_api_key or "",
                    keywords=keyword,
                    start=1,
                    page_length=request.candidate_pool_per_source,
                )
                springer_candidates = _build_springer_candidates(springer_records, keyword)
                source_counts["Springer"] += len(springer_candidates)
                candidates.extend(springer_candidates)
            except Exception as exc:
                warning = _normalize_error(
                    f"Springer 检索失败（关键词：{keyword}）",
                    exc,
                    auth_hint="请检查 Springer API Key 是否可用。",
                )
                warnings.append(warning)
                _emit_progress(
                    progress,
                    "source_warning",
                    warning,
                    {
                        "active_keyword": keyword,
                        "active_source": "Springer",
                        "keyword_group": "zotero",
                    },
                )

        if request.include_elsevier:
            _emit_progress(
                progress,
                "searching_sources",
                f"正在 Elsevier 检索：{keyword}",
                {
                    "active_keyword": keyword,
                    "active_source": "Elsevier",
                    "keyword_group": "zotero",
                    "candidate_pool_per_source": request.candidate_pool_per_source,
                },
            )
            try:
                elsevier_articles = search_articles(
                    request.elsevier_api_key or "",
                    keyword,
                    request.candidate_pool_per_source,
                )
                elsevier_candidates = _build_elsevier_candidates(elsevier_articles, keyword)
                source_counts["Elsevier"] += len(elsevier_candidates)
                candidates.extend(elsevier_candidates)
            except Exception as exc:
                warning = _normalize_error(
                    f"Elsevier 检索失败（关键词：{keyword}）",
                    exc,
                    auth_hint="请检查 Elsevier API Key 是否可用。",
                )
                warnings.append(warning)
                _emit_progress(
                    progress,
                    "source_warning",
                    warning,
                    {
                        "active_keyword": keyword,
                        "active_source": "Elsevier",
                        "keyword_group": "zotero",
                    },
                )

        if request.include_ieee:
            _emit_progress(
                progress,
                "searching_sources",
                f"正在 IEEE 检索：{keyword}",
                {
                    "active_keyword": keyword,
                    "active_source": "IEEE",
                    "keyword_group": "zotero",
                    "candidate_pool_per_source": IEEE_PER_KEYWORD_LIMIT,
                },
            )
            try:
                ieee_articles = _search_ieee_articles_for_keyword(
                    request.ieee_api_key or "",
                    keyword,
                )
                ieee_candidates = _build_ieee_candidates(ieee_articles, keyword)
                source_counts["IEEE"] += len(ieee_candidates)
                candidates.extend(ieee_candidates)
            except Exception as exc:
                warning = _normalize_error(
                    f"IEEE 检索失败（关键词：{keyword}）",
                    exc,
                    auth_hint="请检查 IEEE API Key 是否可用。",
                )
                warnings.append(warning)
                _emit_progress(
                    progress,
                    "source_warning",
                    warning,
                    {
                        "active_keyword": keyword,
                        "active_source": "IEEE",
                        "keyword_group": "zotero",
                    },
                )

    for keyword in extra_retrieval_keywords:
        if request.include_springer:
            _emit_progress(
                progress,
                "searching_sources",
                f"正在 Springer 补充检索：{keyword}",
                {
                    "active_keyword": keyword,
                    "active_source": "Springer",
                    "keyword_group": "extra",
                    "candidate_pool_per_source": request.extra_candidate_pool_per_source,
                },
            )
            try:
                springer_records = search_records(
                    api_key=request.springer_api_key or "",
                    keywords=keyword,
                    start=1,
                    page_length=request.extra_candidate_pool_per_source,
                )
                springer_candidates = _build_springer_candidates(springer_records, keyword)
                source_counts["Springer"] += len(springer_candidates)
                candidates.extend(springer_candidates)
            except Exception as exc:
                warning = _normalize_error(
                    f"Springer 补充检索失败（关键词：{keyword}）",
                    exc,
                    auth_hint="请检查 Springer API Key 是否可用。",
                )
                warnings.append(warning)
                _emit_progress(
                    progress,
                    "source_warning",
                    warning,
                    {
                        "active_keyword": keyword,
                        "active_source": "Springer",
                        "keyword_group": "extra",
                    },
                )

        if request.include_elsevier:
            _emit_progress(
                progress,
                "searching_sources",
                f"正在 Elsevier 补充检索：{keyword}",
                {
                    "active_keyword": keyword,
                    "active_source": "Elsevier",
                    "keyword_group": "extra",
                    "candidate_pool_per_source": request.extra_candidate_pool_per_source,
                },
            )
            try:
                elsevier_articles = search_articles(
                    request.elsevier_api_key or "",
                    keyword,
                    request.extra_candidate_pool_per_source,
                )
                elsevier_candidates = _build_elsevier_candidates(elsevier_articles, keyword)
                source_counts["Elsevier"] += len(elsevier_candidates)
                candidates.extend(elsevier_candidates)
            except Exception as exc:
                warning = _normalize_error(
                    f"Elsevier 补充检索失败（关键词：{keyword}）",
                    exc,
                    auth_hint="请检查 Elsevier API Key 是否可用。",
                )
                warnings.append(warning)
                _emit_progress(
                    progress,
                    "source_warning",
                    warning,
                    {
                        "active_keyword": keyword,
                        "active_source": "Elsevier",
                        "keyword_group": "extra",
                    },
                )

        if request.include_ieee:
            _emit_progress(
                progress,
                "searching_sources",
                f"正在 IEEE 补充检索：{keyword}",
                {
                    "active_keyword": keyword,
                    "active_source": "IEEE",
                    "keyword_group": "extra",
                    "candidate_pool_per_source": IEEE_PER_KEYWORD_LIMIT,
                },
            )
            try:
                ieee_articles = _search_ieee_articles_for_keyword(
                    request.ieee_api_key or "",
                    keyword,
                )
                ieee_candidates = _build_ieee_candidates(ieee_articles, keyword)
                source_counts["IEEE"] += len(ieee_candidates)
                candidates.extend(ieee_candidates)
            except Exception as exc:
                warning = _normalize_error(
                    f"IEEE 补充检索失败（关键词：{keyword}）",
                    exc,
                    auth_hint="请检查 IEEE API Key 是否可用。",
                )
                warnings.append(warning)
                _emit_progress(
                    progress,
                    "source_warning",
                    warning,
                    {
                        "active_keyword": keyword,
                        "active_source": "IEEE",
                        "keyword_group": "extra",
                    },
                )

    candidates = _deduplicate_candidates(candidates)
    if not candidates:
        message = "No candidate papers were retrieved from the enabled sources."
        if warnings:
            message = f"{message} {' '.join(warnings)}"
        raise RuntimeError(message)

    _emit_progress(
        progress,
        "ranking_candidates",
        "对跨平台候选论文执行语义重排。",
        {
            "candidate_count": len(candidates),
            "source_counts": source_counts,
            "embedding_device": request.embedding_device,
            "embedding_proxy": bool(request.embedding_proxy),
        },
    )
    try:
        ranked_candidates = _rank_candidates(candidates, corpus, request)
        recommendations = _select_recommendations(ranked_candidates, request)
    except Exception as exc:
        raise RuntimeError(
            _normalize_error(
                "语义重排",
                exc,
                auth_hint="请检查本地模型依赖是否已安装，或确认 `sentence-transformers` 相关环境可用。",
            )
        ) from exc
    warnings.extend(_attach_journal_ranks(recommendations, progress))

    result = WorkflowResult(
        run_date=date.today().isoformat(),
        corpus_count=len(corpus),
        sampled_title_count=len(sampled_titles),
        keyword_snapshot=keyword_snapshot,
        extra_keyword_snapshot=extra_keyword_snapshot,
        zotero_selected_keywords=zotero_selected_keywords,
        extra_selected_keywords=extra_selected_keywords,
        retrieval_keywords=retrieval_keywords,
        direct_sentence_search=request.direct_sentence_search,
        selected_keywords=selected_keywords,
        candidate_count=len(candidates),
        source_counts=source_counts,
        warnings=warnings,
        recommendations=recommendations,
    )

    if request.write_to_zotero:
        _emit_progress(
            progress,
            "writing_zotero",
            "正在写入推荐结果到 Zotero 集合。",
            {"recommendation_count": len(recommendations)},
        )
        try:
            write_summary = _write_recommendations_to_zotero(request, recommendations)
            result.zotero_write = write_summary
            if write_summary.get("fallback_created_count"):
                warnings.append(
                    f"Zotero 有 {write_summary['fallback_created_count']} 条条目使用最小字段回退导入。"
                )
            if write_summary.get("failed_count"):
                failure_message = (
                    f"Zotero 写入部分失败：成功 {write_summary['created_count']} 条，"
                    f"失败 {write_summary['failed_count']} 条。"
                )
                if write_summary.get("first_failure"):
                    failure_message = (
                        f"{failure_message} 示例：{write_summary['first_failure']}"
                    )
                warnings.append(failure_message)
            progress_message = "推荐结果已写入 Zotero。"
            if write_summary.get("failed_count"):
                progress_message = "推荐结果已部分写入 Zotero。"
            elif write_summary.get("fallback_created_count"):
                progress_message = "推荐结果已写入 Zotero（部分条目使用最小字段回退）。"
            _emit_progress(
                progress,
                "zotero_written",
                progress_message,
                write_summary,
            )
        except Exception as exc:
            warning = _normalize_error(
                "写入 Zotero",
                exc,
                auth_hint="请检查 Zotero ID / Key 及写权限是否正确。",
            )
            warnings.append(warning)
            _emit_progress(
                progress,
                "source_warning",
                warning,
                {"active_source": "Zotero"},
            )

    _emit_progress(
        progress,
        "completed",
        "今日论文推荐工作流执行完成。",
        result.to_dict(),
    )
    return result


def _emit_progress(
    callback: ProgressCallback | None,
    step: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    if callback is not None:
        callback(step, message, details)


def _require_text(
    payload: Mapping[str, Any],
    key: str,
    default: str | None = None,
) -> str:
    value = _optional_text(payload.get(key), default)
    if value is None:
        raise ValueError(f"{key} is required.")
    return value


def _optional_text(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or default
    return str(value).strip() or default


def _optional_secret(value: Any, default: str | None = None) -> str | None:
    return _optional_text(value, default)


def _as_int(value: Any) -> int:
    return int(value)


def _as_float(value: Any) -> float:
    return float(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _merge_keywords(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for keyword in group:
            cleaned = _optional_text(keyword, "")
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            merged.append(cleaned)
    return merged


def _subtract_keywords(source: list[str], excluded: list[str]) -> list[str]:
    excluded_set = {
        keyword.lower()
        for keyword in _merge_keywords(excluded)
    }
    return [
        keyword
        for keyword in _merge_keywords(source)
        if keyword.lower() not in excluded_set
    ]


def _get_zotero_corpus(zotero_id: str, zotero_key: str) -> list[dict]:
    client = zotero.Zotero(zotero_id, "user", zotero_key)
    collections = client.everything(client.collections())
    collections = {item["key"]: item for item in collections}
    corpus = client.everything(
        client.items(itemType="conferencePaper || journalArticle || preprint")
    )
    corpus = [
        item
        for item in corpus
        if item.get("data", {}).get("abstractNote", "").strip()
    ]

    def get_collection_path(collection_key: str) -> str:
        parent = collections[collection_key]["data"]["parentCollection"]
        if parent:
            return f"{get_collection_path(parent)}/{collections[collection_key]['data']['name']}"
        return collections[collection_key]["data"]["name"]

    for item in corpus:
        item["paths"] = [get_collection_path(key) for key in item["data"]["collections"]]
    return corpus


def _split_collection_path(path: str) -> list[str]:
    parts = [part.strip() for part in path.split("/")]
    return [part for part in parts if part]


def _find_collection_key(
    collections: list[dict[str, Any]],
    name: str,
    parent_key: str | None,
) -> str | None:
    for coll in collections:
        data = coll.get("data")
        if not isinstance(data, dict):
            continue
        coll_name = data.get("name", "")
        coll_parent = data.get("parentCollection") or None
        if coll_name == name and coll_parent == parent_key:
            key = data.get("key")
            if isinstance(key, str) and key:
                return key
    return None


def _extract_first_success_key(resp: Any) -> str:
    if not isinstance(resp, dict):
        raise RuntimeError(f"Unexpected response: {resp}")

    successful = resp.get("successful", {})
    if isinstance(successful, dict) and successful:
        first_success = next(iter(successful.values()))
        if isinstance(first_success, dict):
            key = first_success.get("key")
            if isinstance(key, str) and key:
                return key

    success = resp.get("success", {})
    if isinstance(success, dict) and success:
        return next(iter(success.values()))

    unchanged = resp.get("unchanged", {})
    if isinstance(unchanged, dict) and unchanged:
        return next(iter(unchanged.values()))

    raise RuntimeError(f"No created key in response: {resp}")


def _ensure_collection_path(client: zotero.Zotero, path: list[str]) -> str:
    if not path:
        raise ValueError("Collection path is empty")

    all_collections = client.everything(client.collections())
    parent_key: str | None = None

    for name in path:
        existing_key = _find_collection_key(all_collections, name, parent_key)
        if existing_key:
            parent_key = existing_key
            continue

        payload = [{"name": name, "parentCollection": parent_key or ""}]
        created = client.create_collections(payload)
        new_key = _extract_first_success_key(created)
        all_collections.append(
            {
                "data": {
                    "name": name,
                    "key": new_key,
                    "parentCollection": parent_key or "",
                }
            }
        )
        parent_key = new_key

    if not parent_key:
        raise RuntimeError("Failed to ensure collection path")
    return parent_key


def _to_zotero_creator(full_name: str) -> dict[str, str]:
    parts = full_name.strip().split()
    if len(parts) <= 1:
        return {
            "creatorType": "author",
            "name": full_name.strip(),
        }
    return {
        "creatorType": "author",
        "firstName": " ".join(parts[:-1]),
        "lastName": parts[-1],
    }


def _zotero_item_type_for_source(source: str) -> str:
    normalized = (source or "").strip().lower()
    if normalized in {"arxiv", "arxiv.org"}:
        return "preprint"
    return "journalArticle"


def _build_zotero_item_payload(
    client: zotero.Zotero,
    candidate: RecommendationCandidate,
    collection_key: str,
    source: str,
    timestamp: str,
    include_enrichment: bool,
) -> dict[str, Any]:
    item_type = _zotero_item_type_for_source(source)
    try:
        template = client.item_template(item_type)
    except Exception:
        item_type = "journalArticle"
        template = client.item_template(item_type)

    template["title"] = candidate.title
    template["collections"] = [collection_key]

    creators = [
        _to_zotero_creator(author)
        for author in candidate.authors
        if isinstance(author, str) and author.strip()
    ]
    if creators:
        template["creators"] = creators

    published_date = _optional_text(candidate.published_date)
    if published_date:
        template["date"] = published_date
    if candidate.doi:
        template["DOI"] = candidate.doi
    if candidate.url:
        template["url"] = candidate.url

    if not include_enrichment:
        return template

    if candidate.summary:
        template["abstractNote"] = candidate.summary
    if candidate.journal:
        if item_type == "preprint":
            if "repository" in template:
                template["repository"] = candidate.journal
        elif "publicationTitle" in template:
            template["publicationTitle"] = candidate.journal

    extra_lines: list[str] = []
    if candidate.score is not None:
        extra_lines.append(f"PulsePaper Score: {float(candidate.score):.4f}")
    if candidate.matched_keywords:
        extra_lines.append(f"PulsePaper Keywords: {', '.join(candidate.matched_keywords)}")
    if extra_lines:
        existing_extra = _optional_text(template.get("extra"), "") or ""
        template["extra"] = "\n".join(
            [part for part in [existing_extra, *extra_lines] if part]
        ).strip()

    template["tags"] = [
        {"tag": "PulsePaper"},
        {"tag": f"PulsePaper/{timestamp}"},
        {"tag": f"Source/{source}"},
    ]
    return template


def _create_zotero_item(client: zotero.Zotero, template: dict[str, Any]) -> str:
    response = client.create_items([template])
    return _extract_first_success_key(response)


def _format_zotero_failure_detail(
    title: str,
    primary_error: str,
    fallback_error: str,
) -> dict[str, str]:
    return {
        "title": title,
        "primary_error": primary_error,
        "fallback_error": fallback_error,
    }


def _summarize_zotero_failure(detail: Mapping[str, Any] | None) -> str | None:
    if not detail:
        return None

    title = _optional_text(detail.get("title"), "Unknown title") or "Unknown title"
    fallback_error = _optional_text(detail.get("fallback_error"), "Unknown error") or "Unknown error"
    primary_error = _optional_text(detail.get("primary_error"))
    if primary_error:
        return f"{title}: {fallback_error} (full payload: {primary_error})"
    return f"{title}: {fallback_error}"


def _write_recommendations_to_zotero(
    request: WorkflowRequest,
    recommendations: list[RecommendationCandidate],
) -> dict[str, Any]:
    client = zotero.Zotero(request.zotero_id, "user", request.zotero_key)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    created_count = 0
    fallback_created_count = 0
    failed_count = 0
    failed_titles: list[str] = []
    failure_details: list[dict[str, str]] = []
    collection_paths: dict[str, str] = {}
    collection_keys: dict[str, str] = {}

    for candidate in recommendations:
        source = _optional_text(candidate.source, "Unknown") or "Unknown"
        if source not in collection_keys:
            path_parts = [PULSEPAPER_COLLECTION_ROOT, timestamp, source]
            collection_key = _ensure_collection_path(client, path_parts)
            collection_keys[source] = collection_key
            collection_paths[source] = "/".join(path_parts)

        try:
            template = _build_zotero_item_payload(
                client,
                candidate,
                collection_keys[source],
                source,
                timestamp,
                include_enrichment=True,
            )
            _create_zotero_item(client, template)
            created_count += 1
        except Exception as primary_exc:
            primary_error = _clean_exception_message(primary_exc)
            try:
                fallback_template = _build_zotero_item_payload(
                    client,
                    candidate,
                    collection_keys[source],
                    source,
                    timestamp,
                    include_enrichment=False,
                )
                _create_zotero_item(client, fallback_template)
                created_count += 1
                fallback_created_count += 1
            except Exception as fallback_exc:
                fallback_error = _clean_exception_message(fallback_exc)
                failure_details.append(
                    _format_zotero_failure_detail(
                        candidate.title,
                        primary_error,
                        fallback_error,
                    )
                )
                failed_count += 1
                failed_titles.append(candidate.title)

    first_failure = _summarize_zotero_failure(
        failure_details[0] if failure_details else None
    )

    return {
        "enabled": True,
        "timestamp": timestamp,
        "collection_count": len(collection_keys),
        "collections": collection_paths,
        "created_count": created_count,
        "fallback_created_count": fallback_created_count,
        "failed_count": failed_count,
        "failed_titles": failed_titles[:8],
        "failure_details": failure_details[:5],
        "first_failure": first_failure,
    }


def _filter_corpus(corpus: list[dict], pattern: str) -> list[dict]:
    matcher = _build_gitignore_matcher(pattern)
    return [
        item
        for item in corpus
        if not any(matcher(path) for path in item.get("paths", []))
    ]


def _collect_titles(corpus: list[dict]) -> list[str]:
    titles: list[str] = []
    for item in corpus:
        title = item.get("data", {}).get("title")
        if isinstance(title, str) and title.strip():
            titles.append(title.strip())
    if not titles:
        raise RuntimeError("No Zotero titles were found for keyword generation.")
    return titles


def _uses_keyword_sources(request: WorkflowRequest) -> bool:
    return request.include_springer or request.include_elsevier or request.include_ieee


def _parse_keywords(raw_keywords: str) -> list[str]:
    return [line.strip() for line in raw_keywords.splitlines() if line.strip()]


def _build_arxiv_candidates(papers: list[ArxivRecord]) -> list[RecommendationCandidate]:
    candidates: list[RecommendationCandidate] = []
    for paper in papers:
        title = _optional_text(paper.title)
        summary = _optional_text(paper.abstract)
        if not title or not summary:
            continue
        candidates.append(
            RecommendationCandidate(
                source="arXiv",
                title=title,
                authors=list(paper.authors),
                journal=paper.journal,
                published_date=paper.published_date,
                summary=summary,
                url=paper.url,
                doi=paper.doi,
            )
        )
    return candidates


def _build_springer_candidates(
    records: list[Record],
    keyword: str,
) -> list[RecommendationCandidate]:
    candidates: list[RecommendationCandidate] = []
    for record in records:
        title = (record.title or "").strip()
        summary = (record.abstract or "").strip()
        if not title or not summary:
            continue
        candidate = RecommendationCandidate(
            source="Springer",
            title=title,
            authors=list(record.authors),
            journal=record.journal,
            published_date=record.published_date,
            summary=summary,
            url=record.url,
            doi=record.doi,
        )
        candidate.add_keyword(keyword)
        candidates.append(candidate)
    return candidates


def _build_elsevier_candidates(
    articles: list[Article],
    keyword: str,
) -> list[RecommendationCandidate]:
    candidates: list[RecommendationCandidate] = []
    for article in articles:
        title = (article.title or "").strip()
        summary = (article.abstract or "").strip()
        if not title or not summary:
            continue
        candidate = RecommendationCandidate(
            source="Elsevier",
            title=title,
            authors=list(article.authors),
            journal=article.journal,
            published_date=article.published_date or article.year,
            summary=summary,
            url=None,
            doi=article.doi,
        )
        candidate.add_keyword(keyword)
        candidates.append(candidate)
    return candidates


def _search_ieee_articles_for_keyword(
    api_key: str,
    keyword: str,
) -> list[IeeeArticle]:
    articles: list[IeeeArticle] = []
    seen_keys: set[str] = set()
    current_year = datetime.now().year
    for content_type in IEEE_ALLOWED_CONTENT_TYPES:
        batch = search_ieee_articles(
            api_key=api_key,
            keywords=keyword,
            start_record=1,
            max_records=IEEE_SINGLE_REQUEST_LIMIT,
            sort_field=IEEE_SORT_FIELD,
            sort_order=IEEE_SORT_ORDER,
            content_type=content_type,
            start_year=current_year,
            end_year=current_year,
        )
        for article in batch:
            key = (article.doi or "").strip().lower()
            if not key:
                key = " ".join((article.title or "").lower().split())
            if not key:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            articles.append(article)
    articles.sort(
        key=lambda article: _parse_ieee_publication_date(article.published_date),
        reverse=True,
    )
    return articles[:IEEE_SINGLE_REQUEST_LIMIT]


def _parse_ieee_publication_date(value: str | None) -> tuple[int, int, int]:
    text = (value or "").strip()
    if not text:
        return (0, 0, 0)

    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return (parsed.year, parsed.month, parsed.day)
        except ValueError:
            continue

    year_match = re.findall(r"\b(?:19|20)\d{2}\b", text)
    year = int(year_match[-1]) if year_match else 0
    if year == 0:
        return (0, 0, 0)

    lowered = text.lower().replace(".", " ")
    month = 1
    for name, number in IEEE_MONTH_MAP.items():
        if re.search(rf"\b{name}\b", lowered):
            month = number
            break

    day = 1
    day_match = re.search(r"\b(\d{1,2})(?:\s*-\s*\d{1,2})?\s+[a-zA-Z]", lowered)
    if day_match:
        day = int(day_match.group(1))

    return (year, month, day)


def _build_ieee_candidates(
    articles: list[IeeeArticle],
    keyword: str,
) -> list[RecommendationCandidate]:
    candidates: list[RecommendationCandidate] = []
    for article in articles:
        if _is_ieee_conference_article(article):
            continue
        title = (article.title or "").strip()
        summary = (article.abstract or "").strip()
        if not title or not summary:
            continue
        candidate = RecommendationCandidate(
            source="IEEE",
            title=title,
            authors=list(article.authors),
            journal=article.journal,
            published_date=article.published_date,
            summary=summary,
            url=article.url,
            doi=article.doi,
        )
        candidate.add_keyword(keyword)
        candidates.append(candidate)
    return candidates


def _is_ieee_conference_article(article: IeeeArticle) -> bool:
    content_type = (article.content_type or "").strip().lower()
    if "conference" in content_type or "workshop" in content_type:
        return True

    if _optional_text(article.conference_location) or _optional_text(article.conference_dates):
        return True

    journal = (article.journal or "").strip().lower()
    if "workshop" in journal:
        return True

    title = (article.title or "").strip().lower()
    if "workshop" in title:
        return True
    return False


def _deduplicate_candidates(
    candidates: list[RecommendationCandidate],
) -> list[RecommendationCandidate]:
    merged: dict[str, RecommendationCandidate] = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue
        existing.merge_from(candidate)
    return list(merged.values())


def _candidate_key(candidate: RecommendationCandidate) -> str:
    if candidate.doi:
        return candidate.doi.lower()
    normalized_title = " ".join(candidate.title.lower().split())
    return f"{candidate.source}:{normalized_title}"


def _rank_candidates(
    candidates: list[RecommendationCandidate],
    corpus: list[dict],
    request: WorkflowRequest,
) -> list[RecommendationCandidate]:
    ranked_candidates = [candidate for candidate in candidates if candidate.summary.strip()]
    if ranked_candidates:
        rerank_paper(
            ranked_candidates,
            corpus,
            device=request.embedding_device,
            proxy=request.embedding_proxy,
        )
    for candidate in candidates:
        if candidate.score is None:
            candidate.score = 0.0
    ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
    return _prioritize_ieee_recency(ranked)


def _prioritize_ieee_recency(
    candidates: list[RecommendationCandidate],
) -> list[RecommendationCandidate]:
    ieee_positions: list[int] = []
    ieee_candidates: list[RecommendationCandidate] = []
    for index, candidate in enumerate(candidates):
        if candidate.source != "IEEE":
            continue
        ieee_positions.append(index)
        ieee_candidates.append(candidate)

    if len(ieee_candidates) <= 1:
        return candidates

    ieee_candidates.sort(
        key=lambda item: (
            _parse_ieee_publication_date(item.published_date),
            float(item.score),
        ),
        reverse=True,
    )
    reordered = list(candidates)
    for index, candidate in zip(ieee_positions, ieee_candidates):
        reordered[index] = candidate
    return reordered


def _select_recommendations(
    ranked_candidates: list[RecommendationCandidate],
    request: WorkflowRequest,
) -> list[RecommendationCandidate]:
    sources = request.enabled_sources()
    if len(sources) <= 1:
        return ranked_candidates[: request.top_k]
    return _select_balanced_recommendations(
        ranked_candidates,
        sources=sources,
        top_k=request.top_k,
    )


def _select_balanced_recommendations(
    ranked_candidates: list[RecommendationCandidate],
    sources: list[str],
    top_k: int,
) -> list[RecommendationCandidate]:
    if top_k < 1 or not ranked_candidates:
        return []

    base_quota, _ = divmod(top_k, len(sources))
    selected_indices: set[int] = set()

    for source in sources:
        taken = 0
        for index, candidate in enumerate(ranked_candidates):
            if index in selected_indices or candidate.source != source:
                continue
            if taken >= base_quota:
                break
            selected_indices.add(index)
            taken += 1

    for index, _candidate in enumerate(ranked_candidates):
        if len(selected_indices) >= top_k:
            break
        if index not in selected_indices:
            selected_indices.add(index)

    return [
        candidate
        for index, candidate in enumerate(ranked_candidates)
        if index in selected_indices
    ][:top_k]


def _attach_journal_ranks(
    recommendations: list[RecommendationCandidate],
    progress: ProgressCallback | None,
) -> list[str]:
    journals = sorted(
        {
            journal
            for candidate in recommendations
            if candidate.source != "arXiv"
            for journal in [_optional_text(candidate.journal)]
            if journal
        }
    )
    if not journals:
        return []

    if not has_config_text("providers", "easyscholar_secret_key"):
        warning = "未配置 EasyScholar 密钥，已跳过期刊评级。"
        _emit_progress(
            progress,
            "source_warning",
            warning,
            {"source": "journal_rank"},
        )
        return [warning]

    _emit_progress(
        progress,
        "rating_journals",
        "补充期刊评级信息。",
        {"journal_count": len(journals)},
    )

    warnings: list[str] = []
    cached_ranks: dict[str, dict[str, str | None] | None] = {}
    rated_journal_count = 0
    for candidate in recommendations:
        if candidate.source == "arXiv":
            continue
        journal = _optional_text(candidate.journal)
        if not journal:
            continue
        if journal not in cached_ranks:
            try:
                cached_ranks[journal] = get_publication_rank_summary(
                    journal,
                    fields=JOURNAL_RANK_FIELDS,
                )
            except Exception as exc:
                warning = _normalize_error(f"期刊评级获取失败（{journal}）", exc)
                warnings.append(warning)
                cached_ranks[journal] = None
                _emit_progress(
                    progress,
                    "source_warning",
                    warning,
                    {"journal": journal},
                )
        if cached_ranks[journal]:
            candidate.journal_rank = dict(cached_ranks[journal])
    rated_journal_count = len([value for value in cached_ranks.values() if value])
    _emit_progress(
        progress,
        "rating_complete",
        "期刊评级补充完成。",
        {
            "journal_count": len(journals),
            "rated_journal_count": rated_journal_count,
            "unrated_journal_count": max(0, len(journals) - rated_journal_count),
        },
    )
    return warnings


def _extract_year(value: str | None) -> str | None:
    if value is None:
        return None
    for token in str(value).split("-"):
        if len(token) == 4 and token.isdigit():
            return token
    text = str(value)
    for index in range(len(text) - 3):
        snippet = text[index : index + 4]
        if snippet.isdigit():
            return snippet
    return None


def _build_gitignore_matcher(pattern_text: str):
    rules = [
        line.strip()
        for line in pattern_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    def matcher(candidate: str) -> bool:
        normalized = candidate.replace("\\", "/").strip("/")
        matched = False
        for rule in rules:
            negated = rule.startswith("!")
            pattern = rule[1:] if negated else rule
            normalized_pattern = pattern.replace("\\", "/").strip()
            if _match_ignore_rule(normalized, normalized_pattern):
                matched = not negated
        return matched

    return matcher


def _match_ignore_rule(candidate: str, pattern: str) -> bool:
    if not pattern:
        return False

    normalized_pattern = pattern.strip("/")
    if pattern.endswith("/"):
        return candidate == normalized_pattern or candidate.startswith(f"{normalized_pattern}/")
    if fnmatch(candidate, normalized_pattern):
        return True
    if fnmatch(candidate, f"*/{normalized_pattern}"):
        return True
    return normalized_pattern in candidate.split("/")


def _normalize_error(prefix: str, exc: Exception, auth_hint: str | None = None) -> str:
    message = _clean_exception_message(exc)
    if _looks_like_auth_error(message):
        if auth_hint:
            return f"{prefix}失败：鉴权错误。{message} {auth_hint}"
        return f"{prefix}失败：鉴权错误。{message}"
    return f"{prefix}失败：{message}"


def _clean_exception_message(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text or exc.__class__.__name__


def _looks_like_auth_error(message: str) -> bool:
    normalized = message.lower()
    patterns = [
        r"\b401\b",
        r"\b403\b",
        r"invalid token",
        r"authentication",
        r"unauthorized",
        r"authorization_error",
        r"incorrect api key",
        r"invalid api key",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def _redact_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"
