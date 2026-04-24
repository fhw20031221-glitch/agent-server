from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, auth, chat, models, quota
from app.core.config import settings
from app.db.session import SessionLocal
from app.services.admin_seed_service import ensure_initial_admin


app = FastAPI(title="agent-server", version="0.1.0")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.on_event("startup")
def seed_initial_admin() -> None:
    if not settings.admin_username or not settings.admin_password:
        return
    with SessionLocal() as db:
        ensure_initial_admin(db)
        db.commit()


app.include_router(auth.router)
app.include_router(quota.router)
app.include_router(models.router)
app.include_router(chat.router)
app.include_router(admin.router)
