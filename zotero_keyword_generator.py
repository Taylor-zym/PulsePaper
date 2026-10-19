import json
import os
import random
import re
import time
from pathlib import Path
from typing import Iterable

import requests
from pyzotero import zotero

try:
    from config import get_text as get_config_text
except ModuleNotFoundError:
    get_config_text = None  # type: ignore[assignment]

KEYWORD_SERVICE_CONFIG_PATH = Path(__file__).resolve().parent / "gemini" / "config.json"
KEYWORD_SERVICE_BASE_URL = "https://api.anthropic.com"
KEYWORD_SERVICE_MODEL = "claude-3-5-haiku-latest"
SILICONFLOW_BASE_URL = KEYWORD_SERVICE_BASE_URL
SILICONFLOW_MODEL = KEYWORD_SERVICE_MODEL
DEFAULT_EXTRA_KEYWORD_COUNT = 3

KEYWORD_PROMPT_TEMPLATE = """
Analyze the following paper titles as a single research corpus.

Return exactly 5 English retrieval keywords that best represent the overall research profile.

Requirements:
- Do not generate keywords separately for each title.
- Output exactly 5 keywords.
- Output one keyword per line.
- Use normal spaced English phrases.
- Do not merge words into CamelCase or PascalCase.
- Output keywords only, with no explanation or extra text.

Paper Titles:
{titles}
"""

EXTRA_KEYWORD_PROMPT_TEMPLATE = """
Analyze the following user-supplied search hint.

The input may be a few keywords, a short sentence, or a longer topic description.
Return exactly {count} concise English retrieval keywords that are suitable for literature search.

Requirements:
- Output exactly {count} keywords.
- Output one keyword per line.
- Use normal spaced English phrases.
- Prefer terms that are broad enough for retrieval but still specific to the topic.
- Output keywords only, with no explanation or extra text.

User Input:
{text}
"""


def get_zotero_titles(zotero_id: str, zotero_key: str) -> list[str]:
    """Fetch paper titles from Zotero items that are article-like records."""
    client = zotero.Zotero(zotero_id, "user", zotero_key)
    items = client.everything(
        client.items(itemType="conferencePaper || journalArticle || preprint")
    )
    titles: list[str] = []
    for item in items:
        title = item.get("data", {}).get("title")
        if isinstance(title, str) and title.strip():
            titles.append(title.strip())
    return titles


def resolve_sample_size(total_titles: int, sample_size: float) -> int:
    """Convert a 0-1 ratio into a concrete sample size."""
    if total_titles < 1:
        raise ValueError("total_titles must be >= 1.")
    if not 0 < sample_size <= 1:
        raise ValueError("sample_size must be a ratio between 0 and 1.")
    return min(total_titles, max(1, round(total_titles * sample_size)))


def sample_titles(titles: list[str], sample_size: float = 0.9) -> list[str]:
    """Randomly sample titles using a relative ratio."""
    actual_sample_size = resolve_sample_size(len(titles), sample_size)
    if len(titles) <= actual_sample_size:
        return list(titles)
    return random.sample(titles, actual_sample_size)


def format_titles_for_prompt(titles: Iterable[str]) -> str:
    """Convert sampled titles into a numbered block for the prompt."""
    return "\n".join(f"{index}. {title}" for index, title in enumerate(titles, start=1))


def build_keyword_prompt(titles: Iterable[str]) -> str:
    """Fill the keyword-generation prompt with sampled paper titles."""
    return KEYWORD_PROMPT_TEMPLATE.format(titles=format_titles_for_prompt(titles))


def build_extra_keyword_prompt(text: str, count: int = DEFAULT_EXTRA_KEYWORD_COUNT) -> str:
    """Fill the extra-keyword prompt with the user-provided topic hint."""
    if count < 1:
        raise ValueError("count must be >= 1.")
    return EXTRA_KEYWORD_PROMPT_TEMPLATE.format(text=text.strip(), count=count)


def split_compound_keyword(text: str) -> str:
    """Split CamelCase or PascalCase keywords into normal spaced phrases."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_anthropic_text(payload: dict[str, object]) -> str:
    """Join text blocks from an Anthropic Messages response."""
    content = payload.get("content", [])
    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    return "\n".join(chunks).strip()


def normalize_keywords_output(raw_text: str) -> str:
    """Normalize model output to at most five non-empty keyword lines."""
    pieces = re.split(r"[\n,;，；]+", raw_text)
    keywords: list[str] = []
    seen: set[str] = set()

    for piece in pieces:
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", piece).strip()
        cleaned = cleaned.strip(" .:!?'\"()[]{}")
        cleaned = split_compound_keyword(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        keywords.append(cleaned)
        if len(keywords) == 5:
            break

    return "\n".join(keywords)


class KeywordServiceError(RuntimeError):
    """Raised when the keyword model service returns no usable response."""


def _read_legacy_keyword_config() -> dict[str, object]:
    if not KEYWORD_SERVICE_CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(KEYWORD_SERVICE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_keyword_api_key(api_key: str | None) -> str:
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip()
    if get_config_text is not None:
        configured = get_config_text("providers", "gemini_api_key", default="") or ""
        if configured.strip():
            return configured.strip()
    for env_name in ("KEYWORD_API_KEY", "GEMINI_API_KEY", "SILICONFLOW_API_KEY"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    legacy = _read_legacy_keyword_config().get("GEMINI_API_KEY")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()
    raise ValueError("Missing keyword service API key.")


def _resolve_keyword_base_url(base_url: str | None) -> str:
    if isinstance(base_url, str) and base_url.strip():
        return base_url.strip()
    legacy = _read_legacy_keyword_config().get("GOOGLE_GEMINI_BASE_URL")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()
    return KEYWORD_SERVICE_BASE_URL


def _resolve_keyword_model(model: str | None) -> str:
    if isinstance(model, str) and model.strip():
        return model.strip()
    legacy = _read_legacy_keyword_config().get("GEMINI_MODEL")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()
    return KEYWORD_SERVICE_MODEL


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 429, 500, 502, 503, 504}


def _shorten_response(text: str, max_len: int = 280) -> str:
    cleaned = text.strip().replace("\n", " ")
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _request_keywords(
    prompt: str,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
    system_prompt: str,
) -> str:
    resolved_api_key = _resolve_keyword_api_key(api_key)
    resolved_base_url = _resolve_keyword_base_url(base_url)
    resolved_model = _resolve_keyword_model(model)
    endpoint = f"{resolved_base_url.rstrip('/')}/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": resolved_api_key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": resolved_model,
        "max_tokens": 1024,
        "system": system_prompt.strip(),
        "messages": [{"role": "user", "content": prompt.strip()}],
    }

    last_error: KeywordServiceError | None = None
    for attempt in range(3):
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=body,
                timeout=60,
            )
        except requests.RequestException as exc:
            last_error = KeywordServiceError(f"{endpoint} -> request error: {exc}")
            if attempt < 2:
                time.sleep(1.2 * (2**attempt))
                continue
            break

        if not response.ok:
            last_error = KeywordServiceError(
                f"{endpoint} -> HTTP {response.status_code}: "
                f"{_shorten_response(response.text)}"
            )
            if attempt < 2 and _is_retryable_status(response.status_code):
                time.sleep(1.2 * (2**attempt))
                continue
            break

        try:
            payload = response.json()
        except ValueError as exc:
            raise KeywordServiceError(
                f"{endpoint} -> response was not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise KeywordServiceError(f"{endpoint} -> response JSON must be an object.")

        text = extract_anthropic_text(payload)
        if not text:
            raise KeywordServiceError(
                "Anthropic Messages request succeeded but no text was found in "
                "`content[].text`."
            )
        return normalize_keywords_output(text)

    if last_error is not None:
        raise last_error
    raise KeywordServiceError(f"{endpoint} -> request failed with an unknown error.")



def generate_keywords_from_titles(
    titles: list[str],
    api_key: str | None,
    base_url: str = SILICONFLOW_BASE_URL,
    model: str = SILICONFLOW_MODEL,
) -> str:
    """Call Gemini API and return normalized keyword lines."""
    if not titles:
        raise ValueError("titles cannot be empty.")

    return _request_keywords(
        build_keyword_prompt(titles),
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_prompt="""
        You are an assistant who analyzes batches of scientific paper titles
        and returns exactly five English retrieval keywords that summarize
        the user's overall research profile.
        Always use normal spaced phrases, never CamelCase or PascalCase.
        """,
    )


def generate_keywords_from_text(
    text: str,
    api_key: str | None,
    count: int = DEFAULT_EXTRA_KEYWORD_COUNT,
    base_url: str = SILICONFLOW_BASE_URL,
    model: str = SILICONFLOW_MODEL,
) -> str:
    """Call Gemini API and summarize free-form user input into search keywords."""
    if not text.strip():
        raise ValueError("text cannot be empty.")
    if count < 1:
        raise ValueError("count must be >= 1.")

    return _request_keywords(
        build_extra_keyword_prompt(text, count=count),
        api_key=api_key,
        base_url=base_url,
        model=model,
        system_prompt="""
        You turn a user's topic hint into concise English literature-search keywords.
        The input may already contain keywords or may be a natural-language description.
        Return only retrieval keywords, one per line, using normal spaced phrases.
        """,
    )


def generate_keywords_from_zotero(
    zotero_id: str,
    zotero_key: str,
    api_key: str | None,
    sample_size: float = 0.9,
    base_url: str = SILICONFLOW_BASE_URL,
    model: str = SILICONFLOW_MODEL,
) -> tuple[list[str], str]:
    """Fetch Zotero titles, sample them, and generate retrieval keywords with Gemini."""
    titles = get_zotero_titles(zotero_id, zotero_key)
    sampled_titles = sample_titles(titles, sample_size=sample_size)
    keywords = generate_keywords_from_titles(
        sampled_titles,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    return sampled_titles, keywords


def has_keyword_api_key_configured(
    config_path: Path = KEYWORD_SERVICE_CONFIG_PATH,
) -> bool:
    if get_config_text is not None:
        settings_key = get_config_text("providers", "gemini_api_key", default="") or ""
        if settings_key.strip():
            return True

    if any(
        os.getenv(name, "").strip()
        for name in ("KEYWORD_API_KEY", "GEMINI_API_KEY", "SILICONFLOW_API_KEY")
    ):
        return True
    if not config_path.exists():
        return False
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    value = payload.get("GEMINI_API_KEY")
    return isinstance(value, str) and bool(value.strip())


has_gemini_api_key_configured = has_keyword_api_key_configured
