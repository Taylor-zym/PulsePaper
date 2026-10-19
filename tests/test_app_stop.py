from __future__ import annotations

import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "flask_cors" not in sys.modules:
    flask_cors_stub = types.ModuleType("flask_cors")
    flask_cors_stub.CORS = lambda *_args, **_kwargs: None
    sys.modules["flask_cors"] = flask_cors_stub

from app import SESSION_COOKIE_NAME, create_app


def _minimal_payload() -> dict[str, object]:
    return {
        "zotero_id": "test-zotero-id",
        "zotero_key": "test-zotero-key",
        "include_arxiv": True,
        "include_springer": False,
        "include_elsevier": False,
        "arxiv_query": "cs.AI",
        "write_to_zotero": False,
        "top_k": 1,
    }


def _authenticate(client, app, username: str = "zym") -> None:
    token = app.config["auth_manager"].create_session(username)
    client.set_cookie(SESSION_COOKIE_NAME, token)


def _poll_job(client, job_id: str, timeout: float = 3.0) -> dict[str, object]:
    deadline = time.time() + timeout
    latest: dict[str, object] | None = None
    while time.time() < deadline:
        response = client.get(f"/api/workflows/{job_id}")
        assert response.status_code == 200
        latest = response.get_json()
        status = latest.get("status")
        if status in {"completed", "failed", "cancelled"}:
            return latest
        time.sleep(0.03)
    assert latest is not None
    return latest


def test_stop_running_job_switches_to_cancelled(tmp_path) -> None:
    def slow_runner(_request, progress):
        progress("starting", "runner started", None)
        for index in range(300):
            time.sleep(0.01)
            progress("working", f"tick {index}", {"tick": index})
        raise AssertionError("runner should be cancelled before finishing")

    app = create_app(runner=slow_runner, data_dir=tmp_path)
    client = app.test_client()
    _authenticate(client, app)

    create_response = client.post("/api/workflows/today", json=_minimal_payload())
    assert create_response.status_code == 202
    job_id = create_response.get_json()["job_id"]

    stop_response = client.post(f"/api/workflows/{job_id}/stop")
    assert stop_response.status_code == 202
    stop_data = stop_response.get_json()
    assert stop_data["status"] in {"stopping", "cancelled"}

    final_job = _poll_job(client, job_id, timeout=4.0)
    assert final_job["status"] == "cancelled"
    assert final_job["step"] == "cancelled"
    assert final_job["error"] is None


def test_stop_unknown_job_returns_404(tmp_path) -> None:
    app = create_app(runner=lambda _request, _progress: None, data_dir=tmp_path)  # type: ignore[arg-type]
    client = app.test_client()
    _authenticate(client, app)

    response = client.post("/api/workflows/not-exists/stop")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Job not found."


def test_stop_finished_job_returns_conflict(tmp_path) -> None:
    def failing_runner(_request, progress):
        progress("starting", "runner started", None)
        raise RuntimeError("boom")

    app = create_app(runner=failing_runner, data_dir=tmp_path)
    client = app.test_client()
    _authenticate(client, app)

    create_response = client.post("/api/workflows/today", json=_minimal_payload())
    assert create_response.status_code == 202
    job_id = create_response.get_json()["job_id"]

    final_job = _poll_job(client, job_id, timeout=2.0)
    assert final_job["status"] == "failed"

    stop_response = client.post(f"/api/workflows/{job_id}/stop")
    assert stop_response.status_code == 409
    data = stop_response.get_json()
    assert data["status"] == "failed"
    assert "任务已结束" in data["message"]


def test_frontend_assets_disable_cache(tmp_path) -> None:
    app = create_app(runner=lambda _request, _progress: None, data_dir=tmp_path)  # type: ignore[arg-type]
    client = app.test_client()

    html_response = client.get("/workflow_frontend.html")
    assert html_response.status_code == 200
    assert "no-store" in html_response.headers.get("Cache-Control", "")

    js_response = client.get("/static/workflow.js")
    assert js_response.status_code == 200
    assert "no-store" in js_response.headers.get("Cache-Control", "")


def test_create_workflow_forwards_ieee_toggle_to_runner(tmp_path) -> None:
    captured: dict[str, object] = {}

    def capturing_runner(request, progress):
        captured["include_ieee"] = request.include_ieee
        captured["ieee_api_key"] = request.ieee_api_key
        progress("starting", "runner started", None)
        raise RuntimeError("intentional")

    app = create_app(runner=capturing_runner, data_dir=tmp_path)
    client = app.test_client()
    _authenticate(client, app)

    payload = {
        "zotero_id": "test-zotero-id",
        "zotero_key": "test-zotero-key",
        "keyword_api_key": "keyword-key",
        "ieee_api_key": "ieee-key",
        "include_arxiv": False,
        "include_springer": False,
        "include_elsevier": False,
        "include_ieee": True,
        "top_k": 1,
    }

    create_response = client.post("/api/workflows/today", json=payload)
    assert create_response.status_code == 202
    job_id = create_response.get_json()["job_id"]

    final_job = _poll_job(client, job_id, timeout=2.0)
    assert final_job["status"] == "failed"
    assert captured["include_ieee"] is True
    assert captured["ieee_api_key"] == "ieee-key"
