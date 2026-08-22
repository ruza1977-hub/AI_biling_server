import hashlib
import secrets
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import settings
from .db import SessionLocal
from .models import AppClient


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_api_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return "aib_" + secrets.token_urlsafe(32)


def require_admin(x_admin_key: str = Header(alias="X-Admin-Key")) -> None:
    if not secrets.compare_digest(x_admin_key, settings.master_admin_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")


def require_app(
    x_api_key: str = Header(alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> AppClient:
    digest = hash_api_key(x_api_key)
    app = db.scalar(select(AppClient).where(AppClient.api_key_hash == digest, AppClient.active.is_(True)))
    if not app:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return app
