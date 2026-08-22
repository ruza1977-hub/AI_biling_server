import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import AppClient, UserRegistration


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


def device_hash(value: str) -> str:
    secret = (settings.device_hash_secret or settings.master_admin_key).encode("utf-8")
    return hmac.new(secret, value.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def _token_secret() -> bytes:
    return (settings.user_token_secret or settings.master_admin_key).encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_user_token(reg: UserRegistration) -> str:
    payload = {
        "v": 1,
        "app_id": str(reg.app_id),
        "user_id": reg.external_user_id,
        "device_hash": reg.device_id_hash,
    }
    encoded = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_token_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + _b64url_encode(sig)


@dataclass
class UserAuth:
    client: AppClient
    registration: UserRegistration


def require_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_device_id: str | None = Header(default=None, alias="X-Device-ID"),
    db: Session = Depends(get_db),
) -> UserAuth:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "USER_TOKEN_REQUIRED", "message": "Token pengguna diperlukan."})
    if not x_device_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "DEVICE_ID_REQUIRED", "message": "ID peranti diperlukan."})
    token = authorization[7:].strip()
    try:
        encoded, sig_text = token.split(".", 1)
        expected = hmac.new(_token_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
        supplied = _b64url_decode(sig_text)
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("signature")
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
        app_id = payload["app_id"]
        user_id = payload["user_id"]
        token_device_hash = payload["device_hash"]
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "INVALID_USER_TOKEN", "message": "Token pengguna tidak sah."})

    current_device_hash = device_hash(x_device_id)
    if not hmac.compare_digest(token_device_hash, current_device_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "DEVICE_MISMATCH", "message": "Token ini bukan untuk peranti ini."})

    reg = db.scalar(select(UserRegistration).where(
        UserRegistration.app_id == app_id,
        UserRegistration.external_user_id == user_id,
        UserRegistration.device_id_hash == current_device_hash,
        UserRegistration.active.is_(True),
    ))
    if not reg:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "USER_NOT_FOUND", "message": "Pendaftaran pengguna tidak aktif."})
    client = db.scalar(select(AppClient).where(AppClient.id == reg.app_id, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "APP_INACTIVE", "message": "Aplikasi tidak aktif."})
    return UserAuth(client=client, registration=reg)
