import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import AppClient, DeviceAccount, DeviceAppLink, UserDevice, UserRegistration

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)


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


def hash_password(value: str) -> str:
    return _password_hasher.hash(value)


def verify_password(stored_hash: str | None, supplied: str) -> bool:
    if not stored_hash:
        return False
    try:
        return _password_hasher.verify(stored_hash, supplied)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def password_needs_rehash(stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        return _password_hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return False


def _token_secret() -> bytes:
    return (settings.user_token_secret or settings.master_admin_key).encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_user_token(reg: UserRegistration, *, current_device_hash: str | None = None) -> str:
    now = int(time.time())
    payload = {
        "v": 2,
        "app_id": str(reg.app_id),
        "user_id": reg.external_user_id,
        "device_hash": current_device_hash or reg.device_id_hash,
        "iat": now,
        "exp": now + int(settings.user_token_days) * 86400,
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "USER_TOKEN_REQUIRED", "message": "Sila log masuk semula."})
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
        version = int(payload.get("v", 1))
        app_id = payload["app_id"]
        user_id = payload["user_id"]
        token_device_hash = payload["device_hash"]
        if version >= 2 and int(payload.get("exp", 0)) < int(time.time()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "TOKEN_EXPIRED", "message": "Sesi telah tamat. Sila log masuk semula menggunakan e-mel dan kata laluan."})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "INVALID_USER_TOKEN", "message": "Sesi tidak sah. Sila log masuk semula."})

    current_device_hash = device_hash(x_device_id)
    if not hmac.compare_digest(token_device_hash, current_device_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "DEVICE_MISMATCH", "message": "Sesi ini bukan untuk peranti ini. Sila log masuk semula."})

    reg = db.scalar(select(UserRegistration).where(
        UserRegistration.app_id == app_id,
        UserRegistration.external_user_id == user_id,
        UserRegistration.active.is_(True),
    ))
    if not reg:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "USER_NOT_FOUND", "message": "Akaun pengguna tidak aktif."})

    # Token v2 diwujudkan selepas login/pendaftaran dan peranti mesti pernah dipautkan.
    # Token v1 lama masih diterima pada peranti pendaftaran asal untuk migrasi tanpa memutuskan pengguna.
    if int(payload.get("v", 1)) >= 2:
        link = db.scalar(select(UserDevice).where(
            UserDevice.app_id == reg.app_id,
            UserDevice.external_user_id == reg.external_user_id,
            UserDevice.device_id_hash == current_device_hash,
        ))
        if not link:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "DEVICE_NOT_LINKED", "message": "Peranti ini belum dipautkan. Sila log masuk semula."})
        link.last_seen_at = datetime.now(timezone.utc)
        reg.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    else:
        if not hmac.compare_digest(reg.device_id_hash, current_device_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "DEVICE_MISMATCH", "message": "Sila log masuk semula."})

    client = db.scalar(select(AppClient).where(AppClient.id == reg.app_id, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "APP_INACTIVE", "message": "Aplikasi tidak aktif."})
    return UserAuth(client=client, registration=reg)



def create_device_token(account: DeviceAccount, client: AppClient, *, current_device_hash: str) -> str:
    now = int(time.time())
    payload = {
        "v": 3,
        "type": "device",
        "app_id": str(client.id),
        "user_id": account.external_user_id,
        "device_hash": current_device_hash,
        "iat": now,
        "exp": now + int(settings.user_token_days) * 86400,
    }
    encoded = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_token_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + _b64url_encode(sig)


@dataclass
class DeviceAuth:
    client: AppClient
    account: DeviceAccount


def require_device_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_device_id: str | None = Header(default=None, alias="X-Device-ID"),
    db: Session = Depends(get_db),
) -> DeviceAuth:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "DEVICE_TOKEN_REQUIRED", "message": "Sesi peranti diperlukan."})
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
        if int(payload.get("v", 0)) < 3 or payload.get("type") != "device":
            raise ValueError("type")
        if int(payload.get("exp", 0)) < int(time.time()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "TOKEN_EXPIRED", "message": "Sesi tamat. Aplikasi akan menyambung semula secara automatik."})
        app_id = payload["app_id"]
        user_id = payload["user_id"]
        token_device_hash = payload["device_hash"]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "INVALID_DEVICE_TOKEN", "message": "Sesi peranti tidak sah."})

    current_device_hash = device_hash(x_device_id)
    if not hmac.compare_digest(token_device_hash, current_device_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "DEVICE_MISMATCH", "message": "Sesi ini bukan untuk peranti ini."})

    account = db.scalar(select(DeviceAccount).where(
        DeviceAccount.external_user_id == user_id,
        DeviceAccount.device_id_hash == current_device_hash,
        DeviceAccount.active.is_(True),
    ))
    if not account:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "DEVICE_ACCOUNT_NOT_FOUND", "message": "Dompet peranti tidak ditemui."})

    client = db.scalar(select(AppClient).where(AppClient.id == app_id, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "APP_INACTIVE", "message": "Aplikasi tidak aktif."})

    link = db.scalar(select(DeviceAppLink).where(
        DeviceAppLink.device_account_id == account.id,
        DeviceAppLink.app_id == client.id,
    ))
    if not link:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "APP_NOT_LINKED", "message": "Aplikasi belum dipautkan kepada dompet peranti."})

    now = datetime.now(timezone.utc)
    account.last_seen_at = now
    link.last_seen_at = now
    db.commit()
    return DeviceAuth(client=client, account=account)
