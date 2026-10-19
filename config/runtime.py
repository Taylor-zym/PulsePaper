from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CONFIG_DIR / "settings.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "auth": {
        "admin_user": "admin",
        "admin_password": "",
    },
    "zotero": {
        "library_id": "",
        "api_key": "",
    },
    "providers": {
        "gemini_api_key": "",
        "keyword_api_key": "",
        "springer_api_key": "",
        "elsevier_api_key": "",
        "ieee_api_key": "",
        "easyscholar_secret_key": "",
    },
    "workflow": {
        "include_arxiv": True,
        "include_springer": False,
        "include_elsevier": False,
        "include_ieee": False,
        "write_to_zotero": False,
        "arxiv_query": "cs.AI+cs.CV+cs.LG+cs.CL",
        "zotero_ignore": "AI Agent/\n**/survey\n!LLM/survey",
        "sample_ratio": 0.9,
        "selected_keyword_count": 3,
        "candidate_pool_per_source": 200,
        "top_k": 8,
        "embedding_device": "auto",
        "embedding_proxy": "",
    },
    "keyword_service": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-haiku-latest",
    },
}


def load_config() -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        return config

    try:
        raw = json.loads(_strip_json_comments(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return config

    if isinstance(raw, dict):
        _merge_dict(config, raw)
    return config


def get_text(*path: str, default: str | None = "") -> str | None:
    value = _lookup(path, default)
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or default
    return str(value).strip() or default


def has_text(*path: str) -> bool:
    return bool(get_text(*path, default=""))


def get_bool(*path: str, default: bool = False) -> bool:
    value = _lookup(path, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def get_int(*path: str, default: int = 0) -> int:
    value = _lookup(path, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_float(*path: str, default: float = 0.0) -> float:
    value = _lookup(path, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_frontend_config(
    user_defaults: dict[str, Any] | None = None,
    user_configured: dict[str, bool] | None = None,
) -> dict[str, Any]:
    workflow_defaults = {
        "zotero_id": get_text("zotero", "library_id", default=""),
        "include_arxiv": get_bool("workflow", "include_arxiv", default=True),
        "include_springer": get_bool("workflow", "include_springer", default=False),
        "include_elsevier": get_bool("workflow", "include_elsevier", default=False),
        "include_ieee": get_bool("workflow", "include_ieee", default=False),
        "write_to_zotero": get_bool("workflow", "write_to_zotero", default=False),
        "arxiv_query": get_text("workflow", "arxiv_query", default=""),
        "zotero_ignore": get_text("workflow", "zotero_ignore", default=""),
        "sample_ratio": get_float("workflow", "sample_ratio", default=0.9),
        "selected_keyword_count": get_int("workflow", "selected_keyword_count", default=3),
        "candidate_pool_per_source": get_int("workflow", "candidate_pool_per_source", default=200),
        "top_k": get_int("workflow", "top_k", default=8),
        "embedding_device": get_text("workflow", "embedding_device", default="auto"),
        "embedding_proxy": get_text("workflow", "embedding_proxy", default=""),
        "keyword_base_url": get_text("keyword_service", "base_url", default=""),
        "keyword_model": get_text("keyword_service", "model", default=""),
    }
    configured = {
        "zotero_key": has_text("zotero", "api_key"),
        "gemini_api_key": has_text("providers", "gemini_api_key"),
        "keyword_api_key": has_text("providers", "keyword_api_key"),
        "springer_api_key": has_text("providers", "springer_api_key"),
        "elsevier_api_key": has_text("providers", "elsevier_api_key"),
        "ieee_api_key": has_text("providers", "ieee_api_key"),
        "easyscholar_secret_key": has_text("providers", "easyscholar_secret_key"),
    }
    if user_defaults:
        workflow_defaults.update(user_defaults)
    if user_configured:
        configured.update(user_configured)
    return {
        "defaults": workflow_defaults,
        "configured": configured,
        "config_path": str(CONFIG_PATH),
    }


def _lookup(path: tuple[str, ...], default: Any) -> Any:
    current: Any = load_config()
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    if current is None:
        return default
    return current


def _merge_dict(target: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_dict(target[key], value)
            continue
        target[key] = value


def _strip_json_comments(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < length and text[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue

        result.append(char)
        index += 1

    return "".join(result)
