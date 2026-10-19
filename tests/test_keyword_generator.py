from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import zotero_keyword_generator as kg


class _FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        ok: bool = True,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = text

    def json(self) -> object:
        return self._payload


def test_generate_keywords_from_titles_uses_anthropic_messages(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse(
            {
                "model": "gpt-5.4",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Graph Learning\nVision Transformer\nDiffusion Models\n"
                            "Remote Sensing\nLarge Language Model"
                        ),
                    }
                ],
            }
        )

    monkeypatch.setattr(kg.requests, "post", _fake_post)

    output = kg.generate_keywords_from_titles(
        ["Paper A", "Paper B"],
        api_key="test-key",
        base_url="http://example-gateway",
        model="gpt-5.4",
    )

    assert output.splitlines() == [
        "Graph Learning",
        "Vision Transformer",
        "Diffusion Models",
        "Remote Sensing",
        "Large Language Model",
    ]
    assert captured["url"] == "http://example-gateway/v1/messages"
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "x-api-key": "test-key",
        "anthropic-version": "2023-06-01",
    }
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-5.4"
    assert body["max_tokens"] == 1024
    assert isinstance(body["system"], str)
    assert body["messages"][0]["role"] == "user"
    assert "Paper A" in body["messages"][0]["content"]
    assert "temperature" not in body
    assert "thinking" not in body
    assert "output_config" not in body


def test_extract_anthropic_text_joins_only_text_blocks() -> None:
    payload = {
        "content": [
            {"type": "text", "text": "Graph Learning"},
            {"type": "tool_use", "name": "ignored"},
            {"type": "text", "text": "Vision Transformer"},
        ]
    }

    assert kg.extract_anthropic_text(payload) == "Graph Learning\nVision Transformer"


def test_generate_keywords_rejects_empty_anthropic_content(monkeypatch) -> None:
    monkeypatch.setattr(
        kg.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse({"content": []}),
    )

    with pytest.raises(kg.KeywordServiceError, match=r"content\[\]\.text"):
        kg.generate_keywords_from_text(
            "graph learning",
            api_key="test-key",
            base_url="http://example-gateway",
            model="gpt-5.4",
        )


def test_generate_keywords_does_not_retry_unauthorized_response(monkeypatch) -> None:
    calls = 0

    def _fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _FakeResponse(
            {"error": {"type": "authentication_error"}},
            ok=False,
            status_code=401,
            text='{"error":{"type":"authentication_error"}}',
        )

    monkeypatch.setattr(kg.requests, "post", _fake_post)

    with pytest.raises(kg.KeywordServiceError) as exc_info:
        kg.generate_keywords_from_text(
            "graph learning",
            api_key="secret-test-key",
            base_url="http://example-gateway",
            model="gpt-5.4",
        )

    assert calls == 1
    assert "HTTP 401" in str(exc_info.value)
    assert "secret-test-key" not in str(exc_info.value)


def test_has_keyword_api_key_configured_reads_legacy_config(tmp_path) -> None:
    config_path = tmp_path / "keyword.json"
    config_path.write_text(
        '{"GOOGLE_GEMINI_BASE_URL":"http://x","GEMINI_API_KEY":"test-key"}',
        encoding="utf-8",
    )
    assert kg.has_keyword_api_key_configured(config_path) is True
