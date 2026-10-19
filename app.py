from __future__ import annotations

import copy
import json
import logging
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from config import get_frontend_config, get_text, load_config
from workflow_service import WorkflowRequest, WorkflowResult, run_daily_recommendation_workflow

WorkflowRunner = Callable[[WorkflowRequest, Callable[[str, str, dict[str, Any] | None], None] | None], WorkflowResult]
LOGGER = logging.getLogger("workflow_web_flask")
HISTORY_LIMIT = 30
# Cap how many workflow jobs are kept in memory. Finished jobs are persisted to
# workflow_history.json, so evicting old terminal jobs here bounds memory without
# losing results. Recent jobs stay resident so clients can still poll them.
MAX_RETAINED_JOBS = 50
SESSION_COOKIE_NAME = "workflow_session"
NO_CACHE_HEADER_VALUE = "no-store, no-cache, must-revalidate, max-age=0"
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_DAILY_SEARCH_LIMIT = 3
ALLOWED_CONFIG_FILES = {"logo.png"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _apply_no_cache_headers(response):
    response.headers["Cache-Control"] = NO_CACHE_HEADER_VALUE
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _clean_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_daily_search_limit(value: Any, default: int = DEFAULT_DAILY_SEARCH_LIMIT) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return default
        try:
            limit = int(cleaned)
        except ValueError as exc:
            raise ValueError("每日检索次数必须是整数。") from exc
    elif isinstance(value, bool):
        raise ValueError("每日检索次数必须是整数。")
    else:
        try:
            limit = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("每日检索次数必须是整数。") from exc
    if limit < 0:
        raise ValueError("每日检索次数必须大于或等于 0。")
    return limit


def _coerce_optional_secret(value: Any) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


def _copy_user_record(record: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(record)


def _get_daily_search_limit(record: dict[str, Any]) -> int:
    permissions = record.get("permissions") if isinstance(record.get("permissions"), dict) else {}
    try:
        return _normalize_daily_search_limit(permissions.get("daily_search_limit"), DEFAULT_DAILY_SEARCH_LIMIT)
    except ValueError:
        return DEFAULT_DAILY_SEARCH_LIMIT


def _get_daily_search_usage(record: dict[str, Any]) -> tuple[str, int]:
    usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
    daily_search = usage.get("daily_search") if isinstance(usage.get("daily_search"), dict) else {}
    today = _today_iso()
    usage_date = _clean_text(daily_search.get("date"), today)
    count = _clean_int(daily_search.get("count"), 0)
    if usage_date != today:
        return today, 0
    return usage_date, max(count, 0)


def _build_default_permissions() -> dict[str, Any]:
    return {"daily_search_limit": DEFAULT_DAILY_SEARCH_LIMIT}


def _build_default_usage() -> dict[str, Any]:
    return {"daily_search": {"date": _today_iso(), "count": 0}}


def _build_default_profile() -> dict[str, Any]:
    return {
        "zotero": {"library_id": "", "api_key": ""},
        "providers": {
            "springer_api_key": "",
            "elsevier_api_key": "",
            "ieee_api_key": "",
        },
    }


def _build_user_record(username: str, password_hash: str, role: str, now: str) -> dict[str, Any]:
    return {
        "username": username,
        "role": role,
        "status": "active",
        "password_hash": password_hash,
        "profile": _build_default_profile(),
        "permissions": _build_default_permissions(),
        "usage": _build_default_usage(),
        "created_at": now,
        "updated_at": now,
    }


def _ensure_user_record_defaults(record: dict[str, Any]) -> dict[str, Any]:
    updated = _copy_user_record(record)
    profile = updated.get("profile") if isinstance(updated.get("profile"), dict) else {}
    updated["profile"] = profile
    zotero = profile.get("zotero") if isinstance(profile.get("zotero"), dict) else {}
    profile["zotero"] = zotero
    zotero.setdefault("library_id", "")
    zotero.setdefault("api_key", "")
    providers = profile.get("providers") if isinstance(profile.get("providers"), dict) else {}
    profile["providers"] = providers
    providers.setdefault("springer_api_key", "")
    providers.setdefault("elsevier_api_key", "")
    providers.setdefault("ieee_api_key", "")

    permissions = updated.get("permissions") if isinstance(updated.get("permissions"), dict) else {}
    updated["permissions"] = permissions
    permissions["daily_search_limit"] = _get_daily_search_limit(updated)

    usage = updated.get("usage") if isinstance(updated.get("usage"), dict) else {}
    updated["usage"] = usage
    daily_search = usage.get("daily_search") if isinstance(usage.get("daily_search"), dict) else {}
    usage["daily_search"] = daily_search
    usage_date, count = _get_daily_search_usage(updated)
    daily_search["date"] = usage_date
    daily_search["count"] = count
    return updated


def _user_binding_defaults(record: dict[str, Any]) -> dict[str, Any]:
    profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    zotero = profile.get("zotero") if isinstance(profile.get("zotero"), dict) else {}
    return {
        "zotero_id": _clean_text(zotero.get("library_id")),
    }


def _user_binding_configured(record: dict[str, Any]) -> dict[str, bool]:
    profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    zotero = profile.get("zotero") if isinstance(profile.get("zotero"), dict) else {}
    providers = profile.get("providers") if isinstance(profile.get("providers"), dict) else {}
    return {
        "zotero_key": bool(_clean_text(zotero.get("api_key"))),
        "springer_api_key": bool(_clean_text(providers.get("springer_api_key"))),
        "elsevier_api_key": bool(_clean_text(providers.get("elsevier_api_key"))),
        "ieee_api_key": bool(_clean_text(providers.get("ieee_api_key"))),
    }


def _summarize_user(record: dict[str, Any]) -> dict[str, Any]:
    normalized = _ensure_user_record_defaults(record)
    profile = normalized.get("profile") if isinstance(normalized.get("profile"), dict) else {}
    zotero = profile.get("zotero") if isinstance(profile.get("zotero"), dict) else {}
    providers = profile.get("providers") if isinstance(profile.get("providers"), dict) else {}
    _, daily_search_used_today = _get_daily_search_usage(normalized)
    return {
        "username": normalized.get("username"),
        "role": normalized.get("role", "user"),
        "status": normalized.get("status", "active"),
        "created_at": normalized.get("created_at"),
        "updated_at": normalized.get("updated_at"),
        "daily_search_limit": _get_daily_search_limit(normalized),
        "daily_search_used_today": daily_search_used_today,
        "configured": {
            "zotero_id": bool(_clean_text(zotero.get("library_id"))),
            "zotero_key": bool(_clean_text(zotero.get("api_key"))),
            "springer_api_key": bool(_clean_text(providers.get("springer_api_key"))),
            "elsevier_api_key": bool(_clean_text(providers.get("elsevier_api_key"))),
            "ieee_api_key": bool(_clean_text(providers.get("ieee_api_key"))),
        },
    }


def _build_user_bindings_payload(record: dict[str, Any]) -> dict[str, Any]:
    profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    zotero = profile.get("zotero") if isinstance(profile.get("zotero"), dict) else {}
    providers = profile.get("providers") if isinstance(profile.get("providers"), dict) else {}
    return {
        "username": record.get("username"),
        "role": record.get("role", "user"),
        "zotero_id": _clean_text(zotero.get("library_id")),
        "configured": {
            "zotero_key": bool(_clean_text(zotero.get("api_key"))),
            "springer_api_key": bool(_clean_text(providers.get("springer_api_key"))),
            "elsevier_api_key": bool(_clean_text(providers.get("elsevier_api_key"))),
            "ieee_api_key": bool(_clean_text(providers.get("ieee_api_key"))),
        },
    }


def _apply_user_bindings(record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    updated = _copy_user_record(record)
    profile = updated.setdefault("profile", {})
    zotero = profile.setdefault("zotero", {})
    providers = profile.setdefault("providers", {})

    if "zotero_id" in payload:
        zotero["library_id"] = _clean_text(payload.get("zotero_id"))
    if "zotero_key" in payload:
        zotero["api_key"] = _clean_text(payload.get("zotero_key"))
    if "springer_api_key" in payload:
        providers["springer_api_key"] = _clean_text(payload.get("springer_api_key"))
    if "elsevier_api_key" in payload:
        providers["elsevier_api_key"] = _clean_text(payload.get("elsevier_api_key"))
    if "ieee_api_key" in payload:
        providers["ieee_api_key"] = _clean_text(payload.get("ieee_api_key"))
    updated["updated_at"] = _now_iso()
    return updated


def _merge_workflow_payload(payload: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    effective = dict(payload)
    profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    zotero = profile.get("zotero") if isinstance(profile.get("zotero"), dict) else {}
    providers = profile.get("providers") if isinstance(profile.get("providers"), dict) else {}
    global_config = load_config()
    global_providers = global_config.get("providers") if isinstance(global_config.get("providers"), dict) else {}

    zotero_id = _clean_text(effective.get("zotero_id"), _clean_text(zotero.get("library_id")))
    zotero_key = _clean_text(effective.get("zotero_key"), _clean_text(zotero.get("api_key")))
    if not zotero_id:
        raise ValueError("请先在后台绑定当前账号的 Zotero ID。")
    if not zotero_key:
        raise ValueError("请先在后台绑定当前账号的 Zotero Key。")
    effective["zotero_id"] = zotero_id
    effective["zotero_key"] = zotero_key

    for field_name in ("springer_api_key", "elsevier_api_key", "ieee_api_key"):
        user_value = _clean_text(providers.get(field_name))
        global_value = _clean_text(global_providers.get(field_name))
        effective[field_name] = _clean_text(effective.get(field_name), user_value or global_value)
    return effective


class WorkflowCancelledError(RuntimeError):
    pass


@dataclass(slots=True)
class WorkflowJob:
    job_id: str
    owner: str | None = None
    status: str = "queued"
    step: str = "queued"
    message: str = "等待执行。"
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "owner": self.owner,
            "status": self.status,
            "step": self.step,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "details": dict(self.details),
            "events": list(self.events),
            "result": self.result,
            "cancel_requested": self.cancel_requested,
        }


class WorkflowHistoryStore:
    def __init__(self, path: Path, limit: int = HISTORY_LIMIT) -> None:
        self._path = path
        self._limit = limit
        self._lock = threading.Lock()
        self._entries = self._load()

    def list_entries(self, owner: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            entries = [entry for entry in self._entries if owner is None or entry.get("owner") == owner]
            return [self._summarize(entry) for entry in entries]

    def get_entry(self, job_id: str, owner: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            for entry in self._entries:
                if entry.get("job_id") != job_id:
                    continue
                if owner is not None and entry.get("owner") != owner:
                    return None
                return copy.deepcopy(entry)
        return None

    def add_job(self, job: WorkflowJob) -> None:
        if job.status != "completed" or job.result is None:
            return
        entry = job.to_dict()
        with self._lock:
            retained = [existing for existing in self._entries if existing.get("job_id") != job.job_id]
            retained.insert(0, entry)
            self._entries = retained[: self._limit]
            self._persist_locked()

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Failed to load workflow history from %s: %s", self._path, exc)
            return []
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if isinstance(entry, dict)]

    def _persist_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("Failed to persist workflow history to %s: %s", self._path, exc)

    def _summarize(self, entry: dict[str, Any]) -> dict[str, Any]:
        result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        request = details.get("request") if isinstance(details.get("request"), dict) else {}
        source_counts = result.get("source_counts") if isinstance(result.get("source_counts"), dict) else {}
        recommendations = result.get("recommendations") if isinstance(result.get("recommendations"), list) else []
        enabled_sources = request.get("enabled_sources")
        if not isinstance(enabled_sources, list):
            enabled_sources = [source for source, count in source_counts.items() if count]
        selected_keywords = result.get("selected_keywords", [])
        extra_selected_keywords = result.get("extra_selected_keywords", [])
        if isinstance(selected_keywords, list) and isinstance(extra_selected_keywords, list):
            history_keywords = []
            seen: set[str] = set()
            for keyword in [*selected_keywords, *extra_selected_keywords]:
                if not isinstance(keyword, str):
                    continue
                lowered = keyword.strip().lower()
                if not lowered or lowered in seen:
                    continue
                seen.add(lowered)
                history_keywords.append(keyword.strip())
        else:
            history_keywords = []
        return {
            "job_id": entry.get("job_id"),
            "owner": entry.get("owner"),
            "status": entry.get("status"),
            "message": entry.get("message"),
            "created_at": entry.get("created_at"),
            "finished_at": entry.get("finished_at"),
            "run_date": result.get("run_date"),
            "enabled_sources": enabled_sources,
            "selected_keywords": history_keywords,
            "source_counts": source_counts,
            "recommendation_count": len(recommendations),
        }


class UserStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._payload = self._load()
        self._bootstrap_admin()

    def get_user(self, username: str) -> dict[str, Any] | None:
        cleaned = _clean_text(username)
        if not cleaned:
            return None
        with self._lock:
            user = self._payload.get("users", {}).get(cleaned)
            if not isinstance(user, dict):
                return None
            return _copy_user_record(self._normalize_user_locked(cleaned, user))

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            users = self._payload.get("users", {})
            normalized_records = []
            changed = False
            for username, record in users.items():
                if not isinstance(record, dict):
                    continue
                normalized = self._normalize_user_locked(username, record)
                normalized_records.append(normalized)
                if normalized is not record:
                    changed = True
            if changed:
                self._persist_locked()
            return [
                _summarize_user(record)
                for record in sorted(normalized_records, key=lambda item: _clean_text(item.get("username")))
            ]

    def create_user(self, username: str, password: str, role: str = "user", daily_search_limit: int = DEFAULT_DAILY_SEARCH_LIMIT) -> dict[str, Any]:
        cleaned_username = _clean_text(username)
        cleaned_password = str(password or "")
        if not cleaned_username:
            raise ValueError("username is required.")
        if not cleaned_password:
            raise ValueError("password is required.")
        normalized_limit = _normalize_daily_search_limit(daily_search_limit)
        now = _now_iso()
        with self._lock:
            users = self._payload.setdefault("users", {})
            if cleaned_username in users:
                raise ValueError("用户名已存在。")
            user_record = _build_user_record(
                username=cleaned_username,
                password_hash=generate_password_hash(cleaned_password),
                role=role,
                now=now,
            )
            user_record["permissions"]["daily_search_limit"] = normalized_limit
            users[cleaned_username] = user_record
            self._persist_locked()
            return _copy_user_record(user_record)

    def update_bindings(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        cleaned = _clean_text(username)
        with self._lock:
            users = self._payload.setdefault("users", {})
            user = users.get(cleaned)
            if not isinstance(user, dict):
                raise ValueError("User not found.")
            normalized = self._normalize_user_locked(cleaned, user)
            updated = _apply_user_bindings(normalized, payload)
            users[cleaned] = updated
            self._persist_locked()
            return _copy_user_record(updated)

    def update_daily_search_limit(self, username: str, limit: Any) -> dict[str, Any]:
        cleaned = _clean_text(username)
        normalized_limit = _normalize_daily_search_limit(limit)
        with self._lock:
            users = self._payload.setdefault("users", {})
            user = users.get(cleaned)
            if not isinstance(user, dict):
                raise ValueError("User not found.")
            updated = self._normalize_user_locked(cleaned, user)
            updated["permissions"]["daily_search_limit"] = normalized_limit
            updated["updated_at"] = _now_iso()
            users[cleaned] = updated
            self._persist_locked()
            return _copy_user_record(updated)

    def delete_user(self, username: str, actor_username: str) -> None:
        cleaned = _clean_text(username)
        actor = _clean_text(actor_username)
        if not cleaned:
            raise ValueError("username is required.")
        if cleaned == actor:
            raise ValueError("不允许删除当前登录的管理员账号。")
        with self._lock:
            users = self._payload.setdefault("users", {})
            user = users.get(cleaned)
            if not isinstance(user, dict):
                raise ValueError("User not found.")
            normalized = self._normalize_user_locked(cleaned, user)
            if normalized.get("role") == "admin":
                admin_count = sum(
                    1
                    for name, record in users.items()
                    if isinstance(record, dict) and self._normalize_user_locked(name, record).get("role") == "admin"
                )
                if admin_count <= 1:
                    raise ValueError("至少需要保留一个管理员账号。")
            users.pop(cleaned, None)
            self._persist_locked()

    def consume_daily_search(self, username: str) -> dict[str, int]:
        cleaned = _clean_text(username)
        with self._lock:
            users = self._payload.setdefault("users", {})
            user = users.get(cleaned)
            if not isinstance(user, dict):
                raise ValueError("User not found.")
            updated = self._normalize_user_locked(cleaned, user)
            limit = _get_daily_search_limit(updated)
            usage = updated["usage"]["daily_search"]
            count = _clean_int(usage.get("count"), 0)
            if count >= limit:
                raise ValueError(f"今日检索次数已达上限（{limit} 次）。")
            usage["count"] = count + 1
            updated["updated_at"] = _now_iso()
            users[cleaned] = updated
            self._persist_locked()
            return {
                "daily_search_limit": limit,
                "daily_search_used_today": usage["count"],
            }

    def verify_credentials(self, username: str, password: str) -> dict[str, Any] | None:
        record = self.get_user(username)
        if record is None:
            return None
        if record.get("status") != "active":
            return None
        password_hash = _clean_text(record.get("password_hash"))
        if not password_hash or not check_password_hash(password_hash, str(password or "")):
            return None
        return record

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema_version": 1, "users": {}}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Failed to load users from %s: %s", self._path, exc)
            return {"schema_version": 1, "users": {}}
        if not isinstance(payload, dict):
            return {"schema_version": 1, "users": {}}
        if not isinstance(payload.get("users"), dict):
            payload["users"] = {}
        payload.setdefault("schema_version", 1)
        return payload

    def _bootstrap_admin(self) -> None:
        admin_user = get_text("auth", "admin_user", default=DEFAULT_ADMIN_USERNAME) or DEFAULT_ADMIN_USERNAME
        admin_password = get_text("auth", "admin_password", default="") or ""
        with self._lock:
            users = self._payload.setdefault("users", {})
            if admin_user in users:
                normalized = self._normalize_user_locked(admin_user, users[admin_user])
                if normalized is not users[admin_user]:
                    users[admin_user] = normalized
                    self._persist_locked()
                return
            if not admin_password:
                raise RuntimeError(
                    "Set auth.admin_password in config/settings.json before the first start."
                )
            now = _now_iso()
            users[admin_user] = _build_user_record(
                username=admin_user,
                password_hash=generate_password_hash(admin_password),
                role="admin",
                now=now,
            )
            self._persist_locked()

    def _normalize_user_locked(self, username: str, record: dict[str, Any]) -> dict[str, Any]:
        normalized = _ensure_user_record_defaults(record)
        if normalized != record:
            self._payload.setdefault("users", {})[username] = normalized
            return normalized
        return record

    def _persist_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("Failed to persist users to %s: %s", self._path, exc)


class WorkflowTaskManager:
    def __init__(
        self,
        runner: WorkflowRunner | None = None,
        history_store: WorkflowHistoryStore | None = None,
    ) -> None:
        self._runner = runner or run_daily_recommendation_workflow
        self._history_store = history_store
        self._jobs: dict[str, WorkflowJob] = {}
        self._lock = threading.Lock()

    def create_job(self, request: WorkflowRequest, owner: str) -> WorkflowJob:
        job = WorkflowJob(job_id=str(uuid4()), owner=owner)
        with self._lock:
            self._jobs[job.job_id] = job
            self._evict_finished_locked()
        LOGGER.info("Accepted workflow job %s for %s with config: %s", job.job_id[:8], owner, request.to_safe_dict())
        worker = threading.Thread(
            target=self._run_job,
            args=(job.job_id, request),
            daemon=True,
        )
        worker.start()
        return job

    def get_job(self, job_id: str) -> WorkflowJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return self._clone_job_locked(job)

    def stop_job(self, job_id: str) -> tuple[WorkflowJob | None, str, str]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None, "not_found", "Job not found."

            if job.status in {"completed", "failed", "cancelled"}:
                snapshot = self._clone_job_locked(job)
                return snapshot, "not_running", "任务已结束，无法停止。"

            if job.cancel_requested:
                snapshot = self._clone_job_locked(job)
                return snapshot, "already_requested", "停止请求已提交，请稍候。"

            job.cancel_requested = True
            if job.status != "stopping":
                job.status = "stopping"
                job.step = "stopping"
                job.message = "正在停止工作流。"
            job.events.append(
                {
                    "time": _now_iso(),
                    "step": "stopping",
                    "message": "收到停止请求，正在终止任务。",
                    "details": {"cancel_requested": True},
                }
            )
            snapshot = self._clone_job_locked(job)
            return snapshot, "accepted", "停止请求已提交。"

    def list_history(self, owner: str | None = None) -> list[dict[str, Any]]:
        if self._history_store is None:
            return []
        return self._history_store.list_entries(owner=owner)

    def get_history_entry(self, job_id: str, owner: str | None = None) -> dict[str, Any] | None:
        if self._history_store is None:
            return None
        return self._history_store.get_entry(job_id, owner=owner)

    def _run_job(self, job_id: str, request: WorkflowRequest) -> None:
        if self._is_cancel_requested(job_id):
            self._mark_job_cancelled(job_id)
            return
        self._update_job(
            job_id,
            status="running",
            step="starting",
            message="工作流已启动。",
            started_at=_now_iso(),
        )
        LOGGER.info("[job %s] Workflow started.", job_id[:8])

        def progress(step: str, message: str, details: dict[str, Any] | None = None) -> None:
            if self._is_cancel_requested(job_id):
                raise WorkflowCancelledError("工作流已停止。")
            payload = dict(details or {})
            self._append_event(job_id, step, message, payload)
            self._update_job(job_id, step=step, message=message, details=payload)
            LOGGER.info("[job %s] %s - %s", job_id[:8], step, message)
            if payload:
                LOGGER.info("[job %s] details: %s", job_id[:8], payload)

        try:
            result = self._runner(request, progress)
        except WorkflowCancelledError as exc:
            LOGGER.info("[job %s] Workflow cancelled: %s", job_id[:8], exc)
            self._mark_job_cancelled(job_id, message=str(exc))
            return
        except Exception as exc:
            LOGGER.exception("[job %s] Workflow failed: %s", job_id[:8], exc)
            self._update_job(
                job_id,
                status="failed",
                step="failed",
                message="工作流执行失败。",
                error=str(exc),
                finished_at=_now_iso(),
            )
            self._append_event(job_id, "failed", "工作流执行失败。", {"error": str(exc)})
            return

        if self._is_cancel_requested(job_id):
            self._mark_job_cancelled(job_id)
            return

        LOGGER.info("[job %s] Workflow completed.", job_id[:8])
        self._update_job(
            job_id,
            status="completed",
            step="completed",
            message="工作流执行完成。",
            result=result.to_dict(),
            finished_at=_now_iso(),
        )
        self._append_event(
            job_id,
            "completed",
            "工作流执行完成。",
            {"recommendation_count": len(result.recommendations)},
        )
        if self._history_store is not None:
            completed_job = self.get_job(job_id)
            if completed_job is not None:
                self._history_store.add_job(completed_job)

    def _append_event(
        self,
        job_id: str,
        step: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        event = {
            "time": _now_iso(),
            "step": step,
            "message": message,
            "details": dict(details or {}),
        }
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.events.append(event)

    def _update_job(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in changes.items():
                if key == "details" and value:
                    job.details.update(value)
                    continue
                setattr(job, key, value)

    def _evict_finished_locked(self) -> None:
        """Drop the oldest terminal jobs once the in-memory cap is exceeded.
        Must be called while holding self._lock. Insertion order is preserved by
        dict, so the oldest jobs are considered first; running/stopping jobs are
        always kept."""
        overflow = len(self._jobs) - MAX_RETAINED_JOBS
        if overflow <= 0:
            return
        terminal = {"completed", "failed", "cancelled"}
        for job_id in list(self._jobs.keys()):
            if overflow <= 0:
                break
            if self._jobs[job_id].status in terminal:
                del self._jobs[job_id]
                overflow -= 1

    def _is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            return bool(job.cancel_requested)

    def _mark_job_cancelled(self, job_id: str, message: str = "工作流已停止。") -> None:
        self._update_job(
            job_id,
            status="cancelled",
            step="cancelled",
            message=message,
            error=None,
            finished_at=_now_iso(),
            cancel_requested=True,
        )
        self._append_event(
            job_id,
            "cancelled",
            message,
            {"cancel_requested": True},
        )

    @staticmethod
    def _clone_job_locked(job: WorkflowJob) -> WorkflowJob:
        return WorkflowJob(
            job_id=job.job_id,
            owner=job.owner,
            status=job.status,
            step=job.step,
            message=job.message,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error=job.error,
            result=None if job.result is None else dict(job.result),
            details=dict(job.details),
            events=list(job.events),
            cancel_requested=job.cancel_requested,
        )


class SessionAuthManager:
    def __init__(self, user_store: UserStore) -> None:
        self._user_store = user_store
        self._lock = threading.Lock()
        self._sessions: dict[str, str] = {}

    def verify_credentials(self, username: str, password: str) -> dict[str, Any] | None:
        return self._user_store.verify_credentials(username, password)

    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = username
        return token

    def get_user(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            return self._sessions.get(token)

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)


def create_app(
    runner: WorkflowRunner | None = None,
    data_dir: Path | None = None,
) -> Flask:
    """创建并配置 Flask 应用"""
    app = Flask(__name__, static_folder="static")
    CORS(app)

    app.config["JSON_AS_ASCII"] = False
    app.config["JSON_SORT_KEYS"] = False
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    base_dir = Path(__file__).resolve().parent
    frontend_path = base_dir / "workflow_frontend.html"
    bindings_frontend_path = base_dir / "bindings_frontend.html"
    admin_users_frontend_path = base_dir / "admin_users_frontend.html"
    static_dir = base_dir / "static"
    config_dir = base_dir / "config"
    docs_dir = base_dir / "docs"
    data_root = data_dir or (base_dir / "data")
    history_store = WorkflowHistoryStore(data_root / "workflow_history.json")
    user_store = UserStore(data_root / "users.json")
    task_manager = WorkflowTaskManager(runner=runner, history_store=history_store)
    auth_manager = SessionAuthManager(user_store)

    app.config["task_manager"] = task_manager
    app.config["auth_manager"] = auth_manager
    app.config["user_store"] = user_store
    app.config["frontend_path"] = frontend_path
    app.config["bindings_frontend_path"] = bindings_frontend_path
    app.config["admin_users_frontend_path"] = admin_users_frontend_path
    app.config["static_dir"] = static_dir
    app.config["config_dir"] = config_dir
    app.config["docs_dir"] = docs_dir

    register_routes(app)

    return app


def register_routes(app: Flask) -> None:
    """注册所有路由"""

    @app.before_request
    def log_request():
        LOGGER.debug("%s %s", request.method, request.path)

    @app.after_request
    def disable_cache(response):
        path = request.path or ""
        if (
            path.startswith("/api/")
            or path.startswith("/static/")
            or path.startswith("/config/")
            or path in {"/", "/index.html", "/workflow_frontend.html", "/workflow", "/bindings", "/admin/users"}
        ):
            return _apply_no_cache_headers(response)
        return response

    def _serve_frontend(frontend_path: Path):
        if frontend_path.exists():
            return send_from_directory(frontend_path.parent, frontend_path.name)
        return jsonify({"error": "Frontend file not found"}), 404

    @app.route("/")
    @app.route("/index.html")
    @app.route("/workflow_frontend.html")
    @app.route("/workflow")
    def index():
        return _serve_frontend(app.config["frontend_path"])

    @app.route("/bindings")
    def bindings_page():
        return _serve_frontend(app.config["bindings_frontend_path"])

    @app.route("/admin/users")
    def admin_users_page():
        return _serve_frontend(app.config["admin_users_frontend_path"])

    @app.route("/static/<path:filename>")
    def serve_static(filename):
        return send_from_directory(app.config["static_dir"], filename)

    @app.route("/config/<path:filename>")
    def serve_config(filename):
        if filename not in ALLOWED_CONFIG_FILES:
            return jsonify({"error": "Not found."}), 404
        return send_from_directory(app.config["config_dir"], filename)

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "time": _now_iso()})

    @app.route("/api/auth/me", methods=["GET"])
    def auth_me():
        user = get_current_user(app)
        if user:
            return jsonify(
                {
                    "authenticated": True,
                    "username": user["username"],
                    "role": user["role"],
                }
            )
        return jsonify({"authenticated": False})

    @app.route("/api/auth/login", methods=["POST"])
    def login():
        try:
            payload = request.get_json()
            if not payload or not isinstance(payload, dict):
                return jsonify({"error": "Request body must be a JSON object."}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        auth_manager = app.config["auth_manager"]
        user = auth_manager.verify_credentials(username, password)

        if user is None:
            return jsonify({"error": "账号或密码错误。"}), 401

        token = auth_manager.create_session(user["username"])
        response = jsonify(
            {
                "authenticated": True,
                "username": user["username"],
                "role": user.get("role", "user"),
            }
        )
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            httponly=True,
            path="/",
            samesite="Lax",
        )
        return response

    @app.route("/api/auth/logout", methods=["POST"])
    @require_auth
    def logout():
        auth_manager = app.config["auth_manager"]
        token = request.cookies.get(SESSION_COOKIE_NAME)
        auth_manager.revoke(token)

        response = jsonify({"ok": True})
        response.set_cookie(
            SESSION_COOKIE_NAME,
            "",
            httponly=True,
            path="/",
            samesite="Lax",
            max_age=0,
        )
        return response

    @app.route("/api/config", methods=["GET"])
    @require_auth
    def get_config():
        user = get_current_user(app)
        assert user is not None
        return jsonify(
            get_frontend_config(
                user_defaults=_user_binding_defaults(user),
                user_configured=_user_binding_configured(user),
            )
        )

    @app.route("/api/users/me/bindings", methods=["GET"])
    @require_auth
    def get_my_bindings():
        user = get_current_user(app)
        assert user is not None
        return jsonify(_build_user_bindings_payload(user))

    @app.route("/api/users/me/bindings", methods=["PUT"])
    @require_auth
    def update_my_bindings():
        user = get_current_user(app)
        assert user is not None
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        user_store = app.config["user_store"]
        updated = user_store.update_bindings(user["username"], payload)
        return jsonify(_build_user_bindings_payload(updated))

    @app.route("/api/admin/users", methods=["GET"])
    @require_admin
    def list_users():
        user_store = app.config["user_store"]
        return jsonify({"items": user_store.list_users()})

    @app.route("/api/admin/users", methods=["POST"])
    @require_admin
    def create_user_route():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        username = _clean_text(payload.get("username"))
        password = str(payload.get("password", ""))
        daily_search_limit = payload.get("daily_search_limit", DEFAULT_DAILY_SEARCH_LIMIT)
        user_store = app.config["user_store"]
        try:
            created = user_store.create_user(username, password, daily_search_limit=daily_search_limit)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_summarize_user(created)), 201

    @app.route("/api/admin/users/<username>", methods=["PATCH"])
    @require_admin
    def update_admin_user(username):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400
        if "daily_search_limit" not in payload:
            return jsonify({"error": "daily_search_limit is required."}), 400
        user_store = app.config["user_store"]
        try:
            updated = user_store.update_daily_search_limit(username, payload.get("daily_search_limit"))
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if message == "User not found." else 400
            return jsonify({"error": message}), status_code
        return jsonify(_summarize_user(updated))

    @app.route("/api/admin/users/<username>", methods=["DELETE"])
    @require_admin
    def delete_admin_user(username):
        user = get_current_user(app)
        assert user is not None
        user_store = app.config["user_store"]
        try:
            user_store.delete_user(username, actor_username=user["username"])
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if message == "User not found." else 400
            return jsonify({"error": message}), status_code
        return jsonify({"ok": True})

    @app.route("/api/user-guide", methods=["GET"])
    @require_auth
    def get_user_guide():
        docs_dir = app.config["docs_dir"]
        guide_path = docs_dir / "USER_GUIDE_MINIMAL.md"
        if not guide_path.exists():
            return jsonify({"error": "User guide not found."}), 404
        try:
            markdown = guide_path.read_text(encoding="utf-8")
        except OSError:
            return jsonify({"error": "Failed to read user guide."}), 500
        return jsonify({"markdown": markdown})

    @app.route("/api/history", methods=["GET"])
    @require_auth
    def list_history():
        task_manager = app.config["task_manager"]
        user = get_current_user(app)
        assert user is not None
        owner = None if user.get("role") == "admin" else user["username"]
        return jsonify({"items": task_manager.list_history(owner=owner)})

    @app.route("/api/history/<job_id>", methods=["GET"])
    @require_auth
    def get_history_entry(job_id):
        task_manager = app.config["task_manager"]
        user = get_current_user(app)
        assert user is not None
        owner = None if user.get("role") == "admin" else user["username"]
        entry = task_manager.get_history_entry(job_id, owner=owner)
        if entry is None:
            return jsonify({"error": "History entry not found."}), 404
        return jsonify(entry)

    @app.route("/api/workflows/<job_id>", methods=["GET"])
    @require_auth
    def get_workflow(job_id):
        task_manager = app.config["task_manager"]
        job = task_manager.get_job(job_id)
        if job is None:
            return jsonify({"error": "Job not found."}), 404
        user = get_current_user(app)
        assert user is not None
        if user.get("role") != "admin" and job.owner != user["username"]:
            return jsonify({"error": "Job not found."}), 404
        return jsonify(job.to_dict())

    @app.route("/api/workflows/<job_id>/stop", methods=["POST"])
    @app.route("/api/workflows/<job_id>/stop/", methods=["POST"])
    @app.route("/api/workflows/stop/<job_id>", methods=["POST"])
    @app.route("/api/workflows/stop", methods=["POST"])
    @require_auth
    def stop_workflow(job_id: str | None = None):
        if not job_id:
            payload = request.get_json(silent=True) or {}
            if isinstance(payload, dict):
                value = payload.get("job_id")
                if isinstance(value, str) and value.strip():
                    job_id = value.strip()
        if not job_id:
            return jsonify({"error": "job_id is required."}), 400

        task_manager = app.config["task_manager"]
        existing = task_manager.get_job(job_id)
        if existing is None:
            return jsonify({"error": "Job not found."}), 404
        user = get_current_user(app)
        assert user is not None
        if user.get("role") != "admin" and existing.owner != user["username"]:
            return jsonify({"error": "Job not found."}), 404

        job, outcome, message = task_manager.stop_job(job_id)
        if outcome == "not_found":
            return jsonify({"error": "Job not found."}), 404

        payload = {
            "job_id": job_id,
            "status": job.status if job else "unknown",
            "message": message,
            "job": job.to_dict() if job else None,
        }
        if outcome in {"accepted", "already_requested"}:
            return jsonify(payload), 202
        return jsonify(payload), 409

    @app.route("/api/workflows/today", methods=["POST"])
    @require_auth
    def create_workflow():
        try:
            payload = request.get_json()
            if not payload or not isinstance(payload, dict):
                return jsonify({"error": "Request body must be a JSON object."}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

        user = get_current_user(app)
        assert user is not None
        try:
            effective_payload = _merge_workflow_payload(payload, user)
            workflow_request = WorkflowRequest.from_payload(effective_payload)
        except ValueError as exc:
            LOGGER.warning("Rejected workflow request: %s", exc)
            return jsonify({"error": str(exc)}), 400

        user_store = app.config["user_store"]
        try:
            user_store.consume_daily_search(user["username"])
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if message == "User not found." else 429
            return jsonify({"error": message}), status_code

        task_manager = app.config["task_manager"]
        job = task_manager.create_job(workflow_request, owner=user["username"])
        return jsonify(
            {
                "job_id": job.job_id,
                "status": job.status,
                "message": "工作流任务已提交。",
            }
        ), 202

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found."}), 404
        return jsonify({"error": "Page not found."}), 404

    @app.errorhandler(500)
    def internal_error(error):
        LOGGER.error("Internal server error: %s", error)
        return jsonify({"error": "Internal server error."}), 500


def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import current_app

        user = get_current_user(current_app)
        if user is None:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return decorated_function


def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import current_app

        user = get_current_user(current_app)
        if user is None:
            return jsonify({"error": "Unauthorized"}), 401
        if user.get("role") != "admin":
            return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)

    return decorated_function


def get_current_user(app: Flask) -> dict[str, Any] | None:
    auth_manager = app.config["auth_manager"]
    token = request.cookies.get(SESSION_COOKIE_NAME)
    username = auth_manager.get_user(token)
    if username is None:
        return None
    user_store = app.config["user_store"]
    return user_store.get_user(username)


def main():
    """启动 Flask 开发服务器"""
    import argparse

    parser = argparse.ArgumentParser(description="Serve the Zotero daily recommendation workflow UI with Flask.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=8000, help="Bind port.")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if not args.debug else logging.DEBUG,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    app = create_app()
    print(f"Workflow web app (Flask) listening on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
