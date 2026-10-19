from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "flask_cors" not in sys.modules:
    flask_cors_stub = types.ModuleType("flask_cors")
    flask_cors_stub.CORS = lambda *_args, **_kwargs: None
    sys.modules["flask_cors"] = flask_cors_stub

import app as app_module
from app import NO_CACHE_HEADER_VALUE, SESSION_COOKIE_NAME, create_app
from workflow_service import RecommendationCandidate, WorkflowResult


@pytest.fixture
def app(tmp_path):
    return create_app(runner=_completed_runner, data_dir=tmp_path)


@pytest.fixture
def client(app):
    return app.test_client()


def _completed_runner(request, progress):
    progress("starting", "runner started", {"request": request.to_safe_dict()})
    return WorkflowResult(
        run_date="2026-04-23",
        corpus_count=1,
        sampled_title_count=1,
        keyword_snapshot=[],
        extra_keyword_snapshot=[],
        zotero_selected_keywords=[],
        extra_selected_keywords=[],
        retrieval_keywords=[],
        direct_sentence_search=False,
        selected_keywords=[],
        candidate_count=1,
        source_counts={source: 1 for source in request.enabled_sources()},
        warnings=[],
        recommendations=[
            RecommendationCandidate(
                source=request.enabled_sources()[0],
                title="Test Paper",
                authors=["Alice"],
                journal="Test Journal",
                published_date="2026-04-23",
                summary="summary",
                url="https://example.com/paper",
                doi=None,
                score=1.0,
            )
        ],
        zotero_write=None,
    )


def _authenticate(client, app, username: str) -> None:
    token = app.config["auth_manager"].create_session(username)
    client.set_cookie(SESSION_COOKIE_NAME, token)


def _create_user(app, username: str, password: str = "pw") -> dict[str, object]:
    return app.config["user_store"].create_user(username, password)


def test_frontend_pages_are_available_with_no_cache(client):
    for path in ["/", "/index.html", "/workflow_frontend.html", "/workflow", "/bindings", "/admin/users"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == NO_CACHE_HEADER_VALUE
        assert "text/html" in response.content_type


def test_protected_endpoints_require_auth(client):
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/history").status_code == 401
    assert client.post("/api/workflows/today", json={}).status_code == 401


def test_non_admin_cannot_access_admin_users(app, client):
    _create_user(app, "alice")
    _authenticate(client, app, "alice")

    response = client.get("/api/admin/users")
    assert response.status_code == 403
    assert response.get_json()["error"] == "Forbidden"




def test_admin_can_create_user_with_default_daily_limit(client, app):
    _authenticate(client, app, "zym")

    response = client.post(
        "/api/admin/users",
        json={"username": "alice", "password": "secret"},
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["username"] == "alice"
    assert data["role"] == "user"
    assert data["daily_search_limit"] == 3
    assert data["daily_search_used_today"] == 0
    assert app.config["user_store"].verify_credentials("alice", "secret") is not None


def test_admin_can_update_daily_search_limit(app, client):
    _create_user(app, "alice")
    _authenticate(client, app, "zym")

    response = client.patch(
        "/api/admin/users/alice",
        json={"daily_search_limit": 7},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["daily_search_limit"] == 7

    users = client.get("/api/admin/users").get_json()["items"]
    alice = next(item for item in users if item["username"] == "alice")
    assert alice["daily_search_limit"] == 7


def test_non_admin_cannot_update_or_delete_user(app, client):
    _create_user(app, "alice")
    _create_user(app, "bob")
    _authenticate(client, app, "alice")

    update_response = client.patch(
        "/api/admin/users/bob",
        json={"daily_search_limit": 2},
    )
    assert update_response.status_code == 403
    assert update_response.get_json()["error"] == "Forbidden"

    delete_response = client.delete("/api/admin/users/bob")
    assert delete_response.status_code == 403
    assert delete_response.get_json()["error"] == "Forbidden"


def test_admin_can_delete_regular_user(app, client):
    _create_user(app, "alice", password="secret")
    _authenticate(client, app, "zym")

    response = client.delete("/api/admin/users/alice")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert app.config["user_store"].get_user("alice") is None
    assert app.config["user_store"].verify_credentials("alice", "secret") is None


def test_admin_cannot_delete_self(app, client):
    _authenticate(client, app, "zym")

    response = client.delete("/api/admin/users/zym")
    assert response.status_code == 400
    assert "不允许删除当前登录的管理员账号" in response.get_json()["error"]


def test_admin_cannot_delete_last_admin(app, client):
    app.config["user_store"].create_user("admin2", "pw", role="admin")
    _authenticate(client, app, "zym")

    response = client.delete("/api/admin/users/admin2")
    assert response.status_code == 200

    response = client.delete("/api/admin/users/zym")
    assert response.status_code == 400
    assert "不允许删除当前登录的管理员账号" in response.get_json()["error"]


def test_workflow_enforces_daily_search_limit(app, client):
    _create_user(app, "alice")
    app.config["user_store"].update_bindings(
        "alice",
        {"zotero_id": "alice-library", "zotero_key": "alice-key"},
    )
    app.config["user_store"].update_daily_search_limit("alice", 1)
    _authenticate(client, app, "alice")

    first_response = client.post(
        "/api/workflows/today",
        json={
            "include_arxiv": True,
            "include_springer": False,
            "include_elsevier": False,
            "include_ieee": False,
            "arxiv_query": "cs.AI",
            "top_k": 1,
        },
    )
    assert first_response.status_code == 202

    second_response = client.post(
        "/api/workflows/today",
        json={
            "include_arxiv": True,
            "include_springer": False,
            "include_elsevier": False,
            "include_ieee": False,
            "arxiv_query": "cs.AI",
            "top_k": 1,
        },
    )
    assert second_response.status_code == 429
    assert "今日检索次数已达上限" in second_response.get_json()["error"]


def test_workflow_daily_search_usage_resets_next_day(app, client, monkeypatch):
    _create_user(app, "alice")
    app.config["user_store"].update_bindings(
        "alice",
        {"zotero_id": "alice-library", "zotero_key": "alice-key"},
    )
    app.config["user_store"].update_daily_search_limit("alice", 1)
    app.config["user_store"]._payload["users"]["alice"]["usage"]["daily_search"] = {
        "date": "2026-04-27",
        "count": 1,
    }
    _authenticate(client, app, "alice")
    monkeypatch.setattr(app_module, "_today_iso", lambda: "2026-04-28")

    response = client.post(
        "/api/workflows/today",
        json={
            "include_arxiv": True,
            "include_springer": False,
            "include_elsevier": False,
            "include_ieee": False,
            "arxiv_query": "cs.AI",
            "top_k": 1,
        },
    )
    assert response.status_code == 202

    refreshed = app.config["user_store"].get_user("alice")
    assert refreshed["usage"]["daily_search"] == {"date": "2026-04-28", "count": 1}




def test_admin_rejects_non_integer_daily_search_limit(app, client):
    _create_user(app, "alice")
    _authenticate(client, app, "zym")

    response = client.patch(
        "/api/admin/users/alice",
        json={"daily_search_limit": "abc"},
    )
    assert response.status_code == 400
    assert "每日检索次数必须是整数" in response.get_json()["error"]


def test_user_bindings_save_and_clear(app, client):
    _create_user(app, "alice")
    _authenticate(client, app, "alice")

    save_response = client.put(
        "/api/users/me/bindings",
        json={
            "zotero_id": "library-1",
            "zotero_key": "z-key",
            "springer_api_key": "s-key",
            "elsevier_api_key": "e-key",
            "ieee_api_key": "i-key",
        },
    )
    assert save_response.status_code == 200
    saved = save_response.get_json()
    assert saved["zotero_id"] == "library-1"
    assert saved["configured"] == {
        "zotero_key": True,
        "springer_api_key": True,
        "elsevier_api_key": True,
        "ieee_api_key": True,
    }

    get_response = client.get("/api/users/me/bindings")
    assert get_response.status_code == 200
    fetched = get_response.get_json()
    assert fetched["zotero_id"] == "library-1"
    assert fetched["configured"]["zotero_key"] is True

    clear_response = client.put(
        "/api/users/me/bindings",
        json={
            "zotero_key": "",
            "springer_api_key": "",
            "elsevier_api_key": "",
            "ieee_api_key": "",
        },
    )
    assert clear_response.status_code == 200
    cleared = clear_response.get_json()
    assert cleared["configured"] == {
        "zotero_key": False,
        "springer_api_key": False,
        "elsevier_api_key": False,
        "ieee_api_key": False,
    }


def test_bindings_partial_update_keeps_existing_provider_keys(app, client):
    _create_user(app, "alice")
    app.config["user_store"].update_bindings(
        "alice",
        {
            "zotero_id": "library-1",
            "zotero_key": "z-key",
            "springer_api_key": "s-key",
            "elsevier_api_key": "e-key",
            "ieee_api_key": "i-key",
        },
    )
    _authenticate(client, app, "alice")

    response = client.put(
        "/api/users/me/bindings",
        json={
            "zotero_id": "library-2",
        },
    )
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["zotero_id"] == "library-2"
    assert updated["configured"] == {
        "zotero_key": True,
        "springer_api_key": True,
        "elsevier_api_key": True,
        "ieee_api_key": True,
    }


def test_workflow_uses_user_bound_zotero_and_source_keys(app, client, monkeypatch):
    captured = {}

    def runner(request, progress):
        captured["zotero_id"] = request.zotero_id
        captured["zotero_key"] = request.zotero_key
        captured["springer_api_key"] = request.springer_api_key
        progress("starting", "runner started", None)
        return _completed_runner(request, progress)

    monkeypatch.setattr(app_module, "load_config", lambda: {"providers": {"springer_api_key": "global-s"}})
    app.config["task_manager"]._runner = runner
    _create_user(app, "alice")
    app.config["user_store"].update_bindings(
        "alice",
        {
            "zotero_id": "user-library",
            "zotero_key": "user-zotero-key",
            "springer_api_key": "user-springer-key",
        },
    )
    _authenticate(client, app, "alice")

    response = client.post(
        "/api/workflows/today",
        json={
            "include_arxiv": True,
            "include_springer": True,
            "include_elsevier": False,
            "include_ieee": False,
            "arxiv_query": "cs.AI",
            "keyword_api_key": "keyword-key",
            "top_k": 1,
        },
    )
    assert response.status_code == 202
    assert captured == {
        "zotero_id": "user-library",
        "zotero_key": "user-zotero-key",
        "springer_api_key": "user-springer-key",
    }


def test_workflow_falls_back_to_global_source_key_only(app, client, monkeypatch):
    captured = {}

    def runner(request, progress):
        captured["springer_api_key"] = request.springer_api_key
        progress("starting", "runner started", None)
        return _completed_runner(request, progress)

    monkeypatch.setattr(app_module, "load_config", lambda: {"providers": {"springer_api_key": "global-s"}})
    app.config["task_manager"]._runner = runner
    _create_user(app, "alice")
    app.config["user_store"].update_bindings(
        "alice",
        {"zotero_id": "user-library", "zotero_key": "user-zotero-key"},
    )
    _authenticate(client, app, "alice")

    response = client.post(
        "/api/workflows/today",
        json={
            "include_arxiv": False,
            "include_springer": True,
            "include_elsevier": False,
            "include_ieee": False,
            "keyword_api_key": "keyword-key",
            "top_k": 1,
        },
    )
    assert response.status_code == 202
    assert captured["springer_api_key"] == "global-s"


def test_workflow_requires_user_bound_zotero_without_global_fallback(app, client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "load_config",
        lambda: {
            "providers": {"springer_api_key": "global-s"},
            "zotero": {"library_id": "global-library", "api_key": "global-key"},
        },
    )
    _create_user(app, "alice")
    _authenticate(client, app, "alice")

    response = client.post(
        "/api/workflows/today",
        json={
            "include_arxiv": True,
            "include_springer": False,
            "include_elsevier": False,
            "include_ieee": False,
            "arxiv_query": "cs.AI",
            "top_k": 1,
        },
    )
    assert response.status_code == 400
    assert "Zotero ID" in response.get_json()["error"]


def test_history_is_scoped_to_owner_for_users_and_visible_to_admin(app, client):
    _create_user(app, "alice")
    app.config["user_store"].update_bindings(
        "alice",
        {"zotero_id": "alice-library", "zotero_key": "alice-key"},
    )
    app.config["user_store"].update_bindings(
        "zym",
        {"zotero_id": "admin-library", "zotero_key": "admin-key"},
    )

    _authenticate(client, app, "alice")
    user_job = client.post(
        "/api/workflows/today",
        json={
            "include_arxiv": True,
            "include_springer": False,
            "include_elsevier": False,
            "include_ieee": False,
            "arxiv_query": "cs.AI",
            "top_k": 1,
        },
    ).get_json()["job_id"]

    admin_client = app.test_client()
    _authenticate(admin_client, app, "zym")
    admin_job = admin_client.post(
        "/api/workflows/today",
        json={
            "include_arxiv": True,
            "include_springer": False,
            "include_elsevier": False,
            "include_ieee": False,
            "arxiv_query": "cs.LG",
            "top_k": 1,
        },
    ).get_json()["job_id"]

    user_history = client.get("/api/history")
    assert user_history.status_code == 200
    user_items = user_history.get_json()["items"]
    assert len(user_items) == 1
    assert user_items[0]["owner"] == "alice"
    assert user_items[0]["job_id"] == user_job

    admin_history = admin_client.get("/api/history")
    assert admin_history.status_code == 200
    owners = {item["owner"] for item in admin_history.get_json()["items"]}
    assert {"alice", "zym"}.issubset(owners)

    assert client.get(f"/api/history/{admin_job}").status_code == 404
    assert admin_client.get(f"/api/history/{user_job}").status_code == 200
