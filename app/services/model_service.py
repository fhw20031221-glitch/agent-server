from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import LlmModel


@dataclass(frozen=True)
class RuntimeModel:
    model_key: str
    display_name: str
    provider: str
    base_url: str
    api_key: str
    upstream_model: str
    max_tokens: int
    temperature_default: float


@dataclass(frozen=True)
class RemoteModel:
    model_key: str
    created: int | None = None
    owned_by: str = ""


def _settings_model() -> RuntimeModel:
    return RuntimeModel(
        model_key=settings.openai_model,
        display_name=settings.openai_model,
        provider="openai-compatible",
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        upstream_model=settings.openai_model,
        max_tokens=2048,
        temperature_default=0.2,
    )


def _read_env_file_value(name: str) -> str:
    if not name:
        return ""

    root = Path(__file__).resolve().parents[2]
    paths = [Path.cwd() / ".env", root / ".env"]
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        for line in resolved.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() != name:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value
    return ""


def resolve_api_key(api_key_env: str, explicit_api_key: str | None = None) -> str:
    api_key = (explicit_api_key or "").strip()
    if api_key:
        return api_key

    env_name = (api_key_env or "").strip()
    if env_name:
        api_key = os.environ.get(env_name, "").strip()
        if api_key:
            return api_key
        api_key = _read_env_file_value(env_name).strip()
        if api_key:
            return api_key

    if not env_name or env_name == "AGENT_SERVER_OPENAI_API_KEY":
        return settings.openai_api_key
    return ""


def resolve_models_url(base_url: str) -> str:
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        raise ValueError("模型服务 Base URL 不能为空")
    if raw.endswith("/models"):
        return raw
    if raw.endswith("/chat/completions"):
        return f"{raw.removesuffix('/chat/completions')}/models"
    return f"{raw}/models"


def _model_key_from_remote(remote_model_key: str, prefix: str) -> str:
    model_key = f"{prefix}{remote_model_key}"
    if len(model_key) > 120:
        raise ValueError(f"模型标识过长：{model_key}")
    return model_key


def fetch_openai_compatible_models(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: int | None = None,
) -> list[RemoteModel]:
    if not api_key:
        raise ValueError("模型服务 API Key 不能为空")

    url = resolve_models_url(base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        with httpx.Client(timeout=timeout_seconds or settings.openai_timeout_seconds) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()
        suffix = f": {detail[:300]}" if detail else ""
        raise RuntimeError(f"模型列表接口返回 HTTP {exc.response.status_code}{suffix}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"模型列表请求失败: {exc}") from exc

    payload = response.json()
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise RuntimeError("模型列表响应格式不正确")

    models: list[RemoteModel] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        model_key = str(item.get("id") or item.get("model") or "").strip()
        if not model_key or model_key in seen or len(model_key) > 120:
            continue
        seen.add(model_key)
        created_raw = item.get("created")
        created = int(created_raw) if isinstance(created_raw, int) else None
        owned_by = str(item.get("owned_by") or "")
        models.append(RemoteModel(model_key=model_key, created=created, owned_by=owned_by))
    return models


def sync_remote_models(
    db: Session,
    *,
    provider: str,
    base_url: str,
    api_key_env: str,
    remote_models: list[RemoteModel],
    model_key_prefix: str = "",
    default_model_key: str | None = None,
    max_tokens: int = 8192,
    temperature_default: float = 0.2,
    enable_new_models: bool = True,
    enable_existing_models: bool = False,
    disable_missing_models: bool = False,
    sort_order_start: int = 100,
) -> dict[str, int | str | None]:
    provider = provider.strip()
    base_url = base_url.strip().rstrip("/")
    api_key_env = api_key_env.strip()
    model_key_prefix = model_key_prefix.strip()

    if not provider:
        raise ValueError("模型供应商不能为空")
    if not base_url:
        raise ValueError("模型服务 Base URL 不能为空")
    if not api_key_env:
        raise ValueError("API Key 环境变量名不能为空")

    remote_pairs: list[tuple[str, RemoteModel]] = []
    skipped = 0
    for remote in remote_models:
        try:
            model_key = _model_key_from_remote(remote.model_key, model_key_prefix)
        except ValueError:
            skipped += 1
            continue
        remote_pairs.append((model_key, remote))

    model_keys = [model_key for model_key, _remote in remote_pairs]
    existing_rows: dict[str, LlmModel] = {}
    if model_keys:
        existing_rows = {
            row.model_key: row
            for row in db.scalars(select(LlmModel).where(LlmModel.model_key.in_(model_keys)))
        }

    created = 0
    updated = 0
    default_local_key = (
        _model_key_from_remote(default_model_key, model_key_prefix)
        if default_model_key
        else None
    )

    for index, (model_key, remote) in enumerate(remote_pairs):
        row = existing_rows.get(model_key)
        sort_order = min(sort_order_start + index, 100000)
        if row is None:
            row = LlmModel(
                model_key=model_key,
                display_name=remote.model_key,
                provider=provider,
                base_url=base_url,
                api_key_env=api_key_env,
                upstream_model=remote.model_key,
                max_tokens=max_tokens,
                temperature_default=temperature_default,
                is_enabled=enable_new_models,
                is_default=False,
                sort_order=sort_order,
            )
            db.add(row)
            existing_rows[model_key] = row
            created += 1
            continue

        changes = {
            "provider": provider,
            "base_url": base_url,
            "api_key_env": api_key_env,
            "upstream_model": remote.model_key,
            "max_tokens": max_tokens,
            "temperature_default": temperature_default,
            "sort_order": sort_order,
        }
        if enable_existing_models:
            changes["is_enabled"] = True

        changed = False
        for field_name, value in changes.items():
            if getattr(row, field_name) != value:
                setattr(row, field_name, value)
                changed = True
        if changed:
            db.add(row)
            updated += 1

    disabled = 0
    if disable_missing_models:
        synced_keys = set(model_keys)
        rows = db.scalars(
            select(LlmModel).where(
                LlmModel.provider == provider,
                LlmModel.base_url == base_url,
                LlmModel.api_key_env == api_key_env,
            )
        )
        for row in rows:
            if row.model_key in synced_keys or not row.is_enabled:
                continue
            row.is_enabled = False
            row.is_default = False
            db.add(row)
            disabled += 1

    resolved_default_key: str | None = None
    if default_local_key and default_local_key in existing_rows:
        unset_other_defaults(db, default_local_key)
        default_row = existing_rows[default_local_key]
        default_row.is_default = True
        default_row.is_enabled = True
        db.add(default_row)
        resolved_default_key = default_local_key

    return {
        "provider": provider,
        "base_url": base_url,
        "total_remote": len(remote_models),
        "created": created,
        "updated": updated,
        "disabled": disabled,
        "skipped": skipped,
        "default_model_key": resolved_default_key,
    }


def _model_order_query(enabled_only: bool = False):
    stmt = select(LlmModel)
    if enabled_only:
        stmt = stmt.where(LlmModel.is_enabled.is_(True))
    return stmt.order_by(LlmModel.is_default.desc(), LlmModel.sort_order.asc(), LlmModel.created_at.asc())


def list_public_models(db: Session) -> list[LlmModel | RuntimeModel]:
    rows = list(db.scalars(_model_order_query(enabled_only=True)))
    if rows:
        return rows
    if settings.openai_model:
        return [_settings_model()]
    return []


def list_admin_models(db: Session) -> list[LlmModel]:
    return list(db.scalars(_model_order_query(enabled_only=False)))


def resolve_model(db: Session, model_key: str | None = None) -> RuntimeModel:
    requested = (model_key or "").strip()
    stmt = select(LlmModel).where(LlmModel.is_enabled.is_(True))
    if requested:
        row = db.scalar(stmt.where(LlmModel.model_key == requested))
    else:
        row = db.scalar(stmt.where(LlmModel.is_default.is_(True)).order_by(LlmModel.sort_order.asc()))
        if row is None:
            row = db.scalar(stmt.order_by(LlmModel.sort_order.asc(), LlmModel.created_at.asc()))

    if row is not None:
        api_key = resolve_api_key(row.api_key_env)
        return RuntimeModel(
            model_key=row.model_key,
            display_name=row.display_name,
            provider=row.provider,
            base_url=row.base_url,
            api_key=api_key,
            upstream_model=row.upstream_model,
            max_tokens=row.max_tokens,
            temperature_default=row.temperature_default,
        )

    fallback = _settings_model()
    if requested and requested != fallback.model_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型不存在或已禁用")
    return fallback


def get_model_or_404(db: Session, model_id: str) -> LlmModel:
    row = db.get(LlmModel, model_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型不存在")
    return row


def unset_other_defaults(db: Session, model_id: str | None = None) -> None:
    stmt = update(LlmModel).values(is_default=False)
    if model_id:
        stmt = stmt.where(LlmModel.id != model_id)
    db.execute(stmt)
