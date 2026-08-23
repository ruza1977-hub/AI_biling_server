from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import base64
import json
import time
from decimal import Decimal, ROUND_CEILING
import hashlib
import hmac
import re
import uuid
import secrets
import string

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, SessionLocal
from .models import AIRequest, AppClient, DeviceAccount, DeviceAppLink, LedgerEntry, ModelPrice, UsageRecord, UserDevice, UserRegistration, Wallet
from .openai_gateway import OpenAIGatewayError, create_response
from .schemas import AdminSetPasswordIn, AIEstimateIn, AIChatIn, ChangePasswordIn, ChargeIn, DeviceBootstrapIn, DeviceTopUpCreditsIn, ForgotPasswordIn, PricingIn, PublicLoginUserIn, PublicRegisterUserIn, RegisterAppIn, RegisterAppOut, RegisterUserIn, TopUpCreditsIn, TopUpIn
from .security import DeviceAuth, UserAuth, create_device_token, create_user_token, device_hash, generate_api_key, get_db, hash_api_key, hash_password, password_needs_rehash, require_admin, require_app, require_device_user, require_user, verify_password
from .email_service import EmailDeliveryError, email_configured, email_provider, send_temporary_password

MICROS_PER_RM = 1_000_000
MICROS_PER_CREDIT = 10_000  # 1 kredit = RM0.01 = 1 sen
CREDITS_PER_RM = 100
TOKENS_PER_MILLION = 1_000_000


@asynccontextmanager
async def lifespan(_: FastAPI):
    # v0.6 preserves every existing wallet/ledger row. create_all creates new tables,
    # while the explicit ALTER statements safely add auth columns to an existing database.
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE user_registrations ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"))
        conn.execute(text("ALTER TABLE user_registrations ADD COLUMN IF NOT EXISTS password_set_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE user_registrations ADD COLUMN IF NOT EXISTS password_must_change BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE user_registrations ADD COLUMN IF NOT EXISTS password_reset_hash VARCHAR(255)"))
        conn.execute(text("ALTER TABLE user_registrations ADD COLUMN IF NOT EXISTS password_reset_sent_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE user_registrations ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE user_registrations ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0"))
        conn.execute(text("ALTER TABLE user_registrations ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ"))
    # user_devices depends only on existing columns, so create again after the migration.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        # Backfill the registration device for accounts created before v0.6.
        for reg in db.scalars(select(UserRegistration)).all():
            link = db.scalar(select(UserDevice).where(
                UserDevice.app_id == reg.app_id,
                UserDevice.external_user_id == reg.external_user_id,
                UserDevice.device_id_hash == reg.device_id_hash,
            ))
            if link is None:
                db.add(UserDevice(
                    app_id=reg.app_id, external_user_id=reg.external_user_id,
                    device_id_hash=reg.device_id_hash,
                    first_seen_at=reg.created_at, last_seen_at=reg.last_seen_at,
                ))
        db.commit()
        # Public device apps do not expose API keys to Android. They are identified by
        # app_name during device bootstrap; the server still keeps an AppClient row so
        # usage can be attributed to the consuming app.
        public_names = [x.strip() for x in settings.public_device_apps.split(",") if x.strip()]
        if settings.public_opr_app_name not in public_names:
            public_names.append(settings.public_opr_app_name)
        if settings.device_wallet_hub_app_name not in public_names:
            public_names.append(settings.device_wallet_hub_app_name)
        for app_name in public_names:
            app_row = db.scalar(select(AppClient).where(AppClient.name == app_name))
            if app_row is None:
                raw_key = generate_api_key()
                db.add(AppClient(name=app_name, api_key_hash=hash_api_key(raw_key), active=True))
        db.commit()
    yield


app = FastAPI(title=settings.service_name, version="0.9.0", lifespan=lifespan)


def micros_to_rm(value: int) -> str:
    return str((Decimal(value) / Decimal(MICROS_PER_RM)).quantize(Decimal("0.000001")))


def credits_to_rm_display(credits: int) -> str:
    return str((Decimal(credits) / Decimal(CREDITS_PER_RM)).quantize(Decimal("0.00")))


def rm_to_micros(value: Decimal) -> int:
    return int((value * MICROS_PER_RM).to_integral_value(rounding=ROUND_CEILING))


def credits_to_micros(value: int) -> int:
    return int(value) * MICROS_PER_CREDIT


def micros_to_credits(value: int) -> int:
    # Kredit ialah unit boleh guna. Baki pecahan sen warisan tidak dikira sebagai 1 kredit penuh.
    return int(Decimal(value) / Decimal(MICROS_PER_CREDIT))


def charge_for_tokens(input_tokens: int, output_tokens: int, *, input_rate: int, output_rate: int, markup_bps: int) -> int:
    # ModelPrice menyimpan kos sebenar penyedia dalam RM per 1 juta token.
    # Harga pengguna = kos sebenar RM x CONSUMER_PRICE_MULTIPLIER (lalai 10).
    # markup_bps dikekalkan dalam skema untuk keserasian admin lama tetapi tidak digunakan
    # dalam aliran harga pengguna v0.5.
    del markup_bps
    provider_cost_micros = (
        Decimal(input_tokens) * Decimal(input_rate)
        + Decimal(output_tokens) * Decimal(output_rate)
    ) / Decimal(TOKENS_PER_MILLION)
    if input_tokens == 0 and output_tokens == 0:
        return 0
    user_price_micros = provider_cost_micros * Decimal(settings.consumer_price_multiplier)
    raw_micros = max(1, int(user_price_micros.to_integral_value(rounding=ROUND_CEILING)))
    # Pengguna membayar kredit bulat. 1 kredit = 1 sen = RM0.01.
    credits = int((Decimal(raw_micros) / Decimal(MICROS_PER_CREDIT)).to_integral_value(rounding=ROUND_CEILING))
    return max(1, credits) * MICROS_PER_CREDIT


def reservation_input_tokens(input_text: str, instructions: str | None) -> int:
    # Conservative upper bound for normal UTF-8 text: one token cannot contain less than one byte.
    # The extra allowance covers request framing and model-side tokenization overhead.
    total_bytes = len(input_text.encode("utf-8")) + len((instructions or "").encode("utf-8"))
    return max(256, total_bytes + 2048)


def ai_ledger_key(idempotency_key: str, stage: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:48]
    return f"ai-{digest}-{stage}"


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail={"code": "INVALID_EMAIL", "message": "Alamat e-mel tidak sah."})
    return email


def hash_device_id(value: str) -> str:
    return device_hash(value)


def registration_response(reg: UserRegistration, db: Session, *, duplicate: bool, trial_granted_now: bool) -> dict:
    wallet = db.scalar(select(Wallet).where(Wallet.app_id == reg.app_id, Wallet.external_user_id == reg.external_user_id))
    balance = wallet.balance_micros if wallet else 0
    return {
        "ok": True,
        "duplicate": duplicate,
        "trial_granted": trial_granted_now,
        "trial_credits": reg.trial_credits,
        "external_user_id": reg.external_user_id,
        "email": reg.email_normalized,
        "balance_credits": micros_to_credits(balance),
        "balance_rm": micros_to_rm(balance),
        "password_configured": bool(reg.password_hash),
        "password_must_change": bool(reg.password_must_change),
    }


def get_or_create_wallet(db: Session, app_id, external_user_id: str) -> Wallet:
    stmt = pg_insert(Wallet).values(
        app_id=app_id, external_user_id=external_user_id, balance_micros=0
    ).on_conflict_do_nothing(constraint="uq_wallet_app_user")
    db.execute(stmt)
    wallet = db.scalar(
        select(Wallet)
        .where(Wallet.app_id == app_id, Wallet.external_user_id == external_user_id)
        .with_for_update()
    )
    if wallet is None:
        raise RuntimeError("Unable to acquire wallet")
    return wallet


def get_active_price(db: Session, provider: str, model: str) -> ModelPrice:
    price = db.scalar(select(ModelPrice).where(
        ModelPrice.provider == provider,
        ModelPrice.model == model,
        ModelPrice.active.is_(True),
    ))
    if not price:
        raise HTTPException(status_code=400, detail="Pricing not configured for this provider/model")
    return price


def get_default_ai_price(db: Session) -> ModelPrice:
    # Server memilih model; aplikasi pengguna tidak diberi pilihan model.
    # Keutamaan: model yang ditetapkan dalam DEFAULT_AI_MODEL, kemudian model aktif
    # yang mengandungi perkataan "luna", kemudian model OpenAI aktif pertama sebagai fallback.
    price = db.scalar(select(ModelPrice).where(
        ModelPrice.provider == "openai",
        ModelPrice.model == settings.default_ai_model,
        ModelPrice.active.is_(True),
    ))
    if price:
        return price
    price = db.scalar(select(ModelPrice).where(
        ModelPrice.provider == "openai",
        ModelPrice.active.is_(True),
        func.lower(ModelPrice.model).contains("luna"),
    ).order_by(ModelPrice.model).limit(1))
    if price:
        return price
    price = db.scalar(select(ModelPrice).where(
        ModelPrice.provider == "openai",
        ModelPrice.active.is_(True),
    ).order_by(ModelPrice.model).limit(1))
    if not price:
        raise HTTPException(status_code=400, detail={
            "code": "NO_ACTIVE_AI_MODEL",
            "message": "Tiada model OpenAI aktif/harga dikonfigurasi pada server.",
        })
    return price


def replay_ai_request(req: AIRequest, db: Session) -> dict:
    wallet = db.get(Wallet, req.wallet_id)
    balance = wallet.balance_micros if wallet else 0
    if req.status == "completed":
        return {
            "ok": True,
            "duplicate": True,
            "request_id": str(req.id),
            "openai_response_id": req.response_id,
            "model": req.model,
            "output_text": req.output_text or "",
            "input_tokens": req.input_tokens or 0,
            "output_tokens": req.output_tokens or 0,
            "charged_rm": micros_to_rm(req.charged_micros or 0),
            "charged_credits": micros_to_credits(req.charged_micros or 0),
            "balance_rm": micros_to_rm(balance),
            "balance_credits": micros_to_credits(balance),
        }
    if req.status == "pending":
        raise HTTPException(status_code=409, detail={"code": "REQUEST_IN_PROGRESS", "request_id": str(req.id)})
    raise HTTPException(status_code=409, detail={
        "code": "PREVIOUS_REQUEST_FAILED",
        "request_id": str(req.id),
        "error_code": req.error_code,
        "message": req.error_detail,
    })


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": settings.service_name,
        "version": "0.9.0",
        "openai_configured": bool(settings.openai_api_key),
        "email_configured": email_configured(),
        "email_provider": email_provider(),
        "auth_mode": "device_wallet",
        "shared_wallet": True,
        "default_ai_model": settings.default_ai_model,
        "consumer_price_multiplier": settings.consumer_price_multiplier,
    }


# ----------------------------- Admin API -----------------------------

@app.post("/v1/admin/apps", response_model=RegisterAppOut, dependencies=[Depends(require_admin)])
def register_app(payload: RegisterAppIn, db: Session = Depends(get_db)):
    if db.scalar(select(AppClient).where(AppClient.name == payload.name)):
        raise HTTPException(status_code=409, detail="App name already exists")
    raw_key = generate_api_key()
    record = AppClient(name=payload.name, api_key_hash=hash_api_key(raw_key))
    db.add(record)
    db.commit()
    db.refresh(record)
    return RegisterAppOut(app_id=str(record.id), name=record.name, api_key=raw_key)


@app.get("/v1/admin/apps", dependencies=[Depends(require_admin)])
def list_apps(db: Session = Depends(get_db)):
    rows = db.scalars(select(AppClient).order_by(AppClient.created_at.desc())).all()
    return [{
        "app_id": str(x.id), "name": x.name, "active": x.active, "created_at": x.created_at
    } for x in rows]


@app.post("/v1/admin/pricing", dependencies=[Depends(require_admin)])
def upsert_pricing(payload: PricingIn, db: Session = Depends(get_db)):
    row = db.scalar(select(ModelPrice).where(ModelPrice.provider == payload.provider, ModelPrice.model == payload.model))
    markup_bps = int((payload.markup_percent * 100).to_integral_value(rounding=ROUND_CEILING))
    if row is None:
        row = ModelPrice(
            provider=payload.provider,
            model=payload.model,
            input_micros_per_million=rm_to_micros(payload.input_rm_per_million),
            output_micros_per_million=rm_to_micros(payload.output_rm_per_million),
            markup_bps=markup_bps,
            active=payload.active,
        )
        db.add(row)
    else:
        row.input_micros_per_million = rm_to_micros(payload.input_rm_per_million)
        row.output_micros_per_million = rm_to_micros(payload.output_rm_per_million)
        row.markup_bps = markup_bps
        row.active = payload.active
    db.commit()
    return {"ok": True, "provider": row.provider, "model": row.model}


@app.get("/v1/admin/pricing", dependencies=[Depends(require_admin)])
def list_pricing(db: Session = Depends(get_db)):
    rows = db.scalars(select(ModelPrice).order_by(ModelPrice.provider, ModelPrice.model)).all()
    return [{
        "provider": x.provider,
        "model": x.model,
        "input_rm_per_million": micros_to_rm(x.input_micros_per_million),
        "output_rm_per_million": micros_to_rm(x.output_micros_per_million),
        "markup_percent": str(Decimal(x.markup_bps) / Decimal(100)),
        "active": x.active,
        "updated_at": x.updated_at,
    } for x in rows]


@app.post("/v1/admin/topup", dependencies=[Depends(require_admin)])
def topup(payload: TopUpIn, db: Session = Depends(get_db)):
    if payload.amount_rm < Decimal(settings.min_topup_rm):
        raise HTTPException(status_code=422, detail={"code": "MINIMUM_TOPUP", "message": f"Top up minimum RM{settings.min_topup_rm}.", "minimum_rm": settings.min_topup_rm, "minimum_credits": settings.min_topup_rm * CREDITS_PER_RM})
    client = db.scalar(select(AppClient).where(AppClient.name == payload.app_name, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=404, detail="App not found")

    existing = db.scalar(select(LedgerEntry).where(
        LedgerEntry.app_id == client.id,
        LedgerEntry.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        return {"ok": True, "duplicate": True, "balance_rm": micros_to_rm(existing.balance_after_micros), "balance_credits": micros_to_credits(existing.balance_after_micros)}

    try:
        wallet = get_or_create_wallet(db, client.id, payload.external_user_id)
        amount = rm_to_micros(payload.amount_rm)
        wallet.balance_micros += amount
        db.add(LedgerEntry(
            app_id=client.id, wallet_id=wallet.id, kind="topup", amount_micros=amount,
            balance_after_micros=wallet.balance_micros, idempotency_key=payload.idempotency_key,
            note=payload.note,
        ))
        db.commit()
        return {"ok": True, "duplicate": False, "balance_rm": micros_to_rm(wallet.balance_micros), "balance_credits": micros_to_credits(wallet.balance_micros)}
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(LedgerEntry).where(
            LedgerEntry.app_id == client.id, LedgerEntry.idempotency_key == payload.idempotency_key))
        if existing:
            return {"ok": True, "duplicate": True, "balance_rm": micros_to_rm(existing.balance_after_micros), "balance_credits": micros_to_credits(existing.balance_after_micros)}
        raise


@app.post("/v1/admin/topup-credits", dependencies=[Depends(require_admin)])
def topup_credits(payload: TopUpCreditsIn, db: Session = Depends(get_db)):
    minimum_credits = settings.min_topup_rm * CREDITS_PER_RM
    if payload.credits < minimum_credits:
        raise HTTPException(status_code=422, detail={"code": "MINIMUM_TOPUP", "message": f"Top up minimum {minimum_credits} kredit (RM{settings.min_topup_rm}).", "minimum_rm": settings.min_topup_rm, "minimum_credits": minimum_credits})
    client = db.scalar(select(AppClient).where(AppClient.name == payload.app_name, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=404, detail="App not found")
    existing = db.scalar(select(LedgerEntry).where(
        LedgerEntry.app_id == client.id, LedgerEntry.idempotency_key == payload.idempotency_key))
    if existing:
        return {"ok": True, "duplicate": True, "balance_credits": micros_to_credits(existing.balance_after_micros)}
    wallet = get_or_create_wallet(db, client.id, payload.external_user_id)
    amount = credits_to_micros(payload.credits)
    wallet.balance_micros += amount
    db.add(LedgerEntry(
        app_id=client.id, wallet_id=wallet.id, kind="topup", amount_micros=amount,
        balance_after_micros=wallet.balance_micros, idempotency_key=payload.idempotency_key,
        note=payload.note or f"Topup {payload.credits} kredit",
    ))
    db.commit()
    return {
        "ok": True, "duplicate": False, "added_credits": payload.credits,
        "balance_credits": micros_to_credits(wallet.balance_micros),
        "balance_rm": micros_to_rm(wallet.balance_micros),
    }


@app.post("/v1/admin/users/set-password", dependencies=[Depends(require_admin)])
def admin_set_user_password(payload: AdminSetPasswordIn, db: Session = Depends(get_db)):
    client = db.scalar(select(AppClient).where(AppClient.name == payload.app_name, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=404, detail={"code": "APP_NOT_FOUND", "message": "Aplikasi tidak ditemui."})
    email = normalize_email(payload.email)
    reg = db.scalar(select(UserRegistration).where(
        UserRegistration.app_id == client.id,
        UserRegistration.email_normalized == email,
    ))
    if not reg:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Akaun e-mel tidak ditemui."})
    reg.password_hash = hash_password(payload.new_password)
    reg.password_set_at = datetime.now(timezone.utc)
    reg.password_must_change = False
    reg.password_reset_hash = None
    reg.password_reset_sent_at = None
    reg.password_reset_expires_at = None
    reg.failed_login_count = 0
    reg.locked_until = None
    db.commit()
    return {"ok": True, "email": reg.email_normalized, "external_user_id": reg.external_user_id, "password_configured": True}


@app.get("/v1/admin/users", dependencies=[Depends(require_admin)])
def admin_users(
    limit: int = Query(default=200, ge=1, le=1000),
    app_name: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = (select(UserRegistration, AppClient.name, Wallet.balance_micros)
            .join(AppClient, AppClient.id == UserRegistration.app_id)
            .join(Wallet, (Wallet.app_id == UserRegistration.app_id) & (Wallet.external_user_id == UserRegistration.external_user_id))
            .order_by(UserRegistration.created_at.desc()).limit(limit))
    if app_name:
        stmt = stmt.where(AppClient.name == app_name)
    rows = db.execute(stmt).all()
    return [{
        "id": str(reg.id), "app_name": name, "email": reg.email_normalized,
        "external_user_id": reg.external_user_id,
        "device_id_hash_prefix": reg.device_id_hash[:12],
        "trial_credits": reg.trial_credits, "trial_granted": reg.trial_granted,
        "password_configured": bool(reg.password_hash),
        "password_must_change": bool(reg.password_must_change),
        "balance_credits": micros_to_credits(balance),
        "balance_rm": micros_to_rm(balance),
        "created_at": reg.created_at, "last_seen_at": reg.last_seen_at,
    } for reg, name, balance in rows]


@app.get("/v1/admin/dashboard", dependencies=[Depends(require_admin)])
def admin_dashboard(db: Session = Depends(get_db)):
    apps = db.scalar(select(func.count()).select_from(AppClient)) or 0
    wallets = db.scalar(select(func.count()).select_from(Wallet)) or 0
    balance_micros = db.scalar(select(func.coalesce(func.sum(Wallet.balance_micros), 0))) or 0
    total_charged = db.scalar(select(func.coalesce(func.sum(UsageRecord.charged_micros), 0))) or 0
    ai_requests = db.scalar(select(func.count()).select_from(AIRequest)) or 0
    completed = db.scalar(select(func.count()).select_from(AIRequest).where(AIRequest.status == "completed")) or 0
    registrations = db.scalar(select(func.count()).select_from(UserRegistration)) or 0
    return {
        "apps": apps,
        "wallets": wallets,
        "total_balance_rm": micros_to_rm(balance_micros),
        "total_balance_credits": micros_to_credits(balance_micros),
        "total_ai_charged_rm": micros_to_rm(total_charged),
        "total_ai_charged_credits": micros_to_credits(total_charged),
        "registrations": registrations,
        "ai_requests": ai_requests,
        "ai_completed": completed,
    }


@app.get("/v1/admin/ledger", dependencies=[Depends(require_admin)])
def admin_ledger(
    limit: int = Query(default=100, ge=1, le=500),
    app_name: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = (select(LedgerEntry, AppClient.name, Wallet.external_user_id)
            .join(AppClient, AppClient.id == LedgerEntry.app_id)
            .join(Wallet, Wallet.id == LedgerEntry.wallet_id)
            .order_by(LedgerEntry.created_at.desc()).limit(limit))
    if app_name:
        stmt = stmt.where(AppClient.name == app_name)
    rows = db.execute(stmt).all()
    return [{
        "id": str(entry.id), "app_name": name, "external_user_id": user_id,
        "kind": entry.kind, "amount_rm": micros_to_rm(entry.amount_micros),
        "amount_credits": str(Decimal(entry.amount_micros) / Decimal(MICROS_PER_CREDIT)),
        "balance_after_rm": micros_to_rm(entry.balance_after_micros),
        "balance_after_credits": micros_to_credits(entry.balance_after_micros),
        "note": entry.note, "created_at": entry.created_at,
    } for entry, name, user_id in rows]


@app.get("/v1/admin/usage", dependencies=[Depends(require_admin)])
def admin_usage(
    limit: int = Query(default=100, ge=1, le=500),
    app_name: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = (select(UsageRecord, AppClient.name)
            .join(AppClient, AppClient.id == UsageRecord.app_id)
            .order_by(UsageRecord.created_at.desc()).limit(limit))
    if app_name:
        stmt = stmt.where(AppClient.name == app_name)
    rows = db.execute(stmt).all()
    return [{
        "id": str(u.id), "app_name": name, "external_user_id": u.external_user_id,
        "provider": u.provider, "model": u.model,
        "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
        "charged_rm": micros_to_rm(u.charged_micros), "charged_credits": micros_to_credits(u.charged_micros), "created_at": u.created_at,
    } for u, name in rows]




def _estimate_secret() -> bytes:
    return (settings.user_token_secret or settings.master_admin_key).encode("utf-8")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64u(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _estimate_fingerprint(payload: AIEstimateIn | AIChatIn) -> str:
    canonical = json.dumps({
        "external_user_id": payload.external_user_id,
        "model": payload.model,
        "input": payload.input,
        "instructions": payload.instructions or "",
        "max_output_tokens": payload.max_output_tokens,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _make_estimate_token(*, app_id: str, user_id: str, fingerprint: str, estimate_credits: int) -> str:
    body = {
        "v": 1,
        "app_id": app_id,
        "user_id": user_id,
        "fp": fingerprint,
        "credits": estimate_credits,
        "exp": int(time.time()) + settings.estimate_ttl_seconds,
    }
    encoded = _b64u(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_estimate_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + _b64u(sig)


def _read_estimate_token(token: str) -> dict:
    try:
        encoded, sig_text = token.split(".", 1)
        expected = hmac.new(_estimate_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
        supplied = _unb64u(sig_text)
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("signature")
        body = json.loads(_unb64u(encoded).decode("utf-8"))
        if int(body.get("exp", 0)) < int(time.time()):
            raise HTTPException(status_code=409, detail={"code": "ESTIMATE_EXPIRED", "message": "Anggaran kredit telah tamat tempoh. Sila anggar semula."})
        return body
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=409, detail={"code": "INVALID_ESTIMATE", "message": "Anggaran kredit tidak sah. Sila anggar semula."})



# ----------------------------- Device Wallet v0.9 -----------------------------

def _device_wallet_hub(db: Session) -> AppClient:
    hub = db.scalar(select(AppClient).where(AppClient.name == settings.device_wallet_hub_app_name))
    if hub is None:
        raw_key = generate_api_key()
        hub = AppClient(name=settings.device_wallet_hub_app_name, api_key_hash=hash_api_key(raw_key), active=True)
        db.add(hub)
        db.flush()
    return hub


def _device_app_client(db: Session, app_name: str) -> AppClient:
    allowed = {x.strip() for x in settings.public_device_apps.split(",") if x.strip()}
    if app_name not in allowed:
        raise HTTPException(status_code=404, detail={
            "code": "APP_NOT_PUBLIC_DEVICE_APP",
            "message": "Aplikasi ini belum dibenarkan menggunakan Device Wallet.",
        })
    client = db.scalar(select(AppClient).where(AppClient.name == app_name, AppClient.active.is_(True)))
    if client is None:
        raw_key = generate_api_key()
        client = AppClient(name=app_name, api_key_hash=hash_api_key(raw_key), active=True)
        db.add(client)
        db.flush()
    return client


def _link_device_app_v09(db: Session, account: DeviceAccount, client: AppClient) -> None:
    now = datetime.now(timezone.utc)
    link = db.scalar(select(DeviceAppLink).where(
        DeviceAppLink.device_account_id == account.id,
        DeviceAppLink.app_id == client.id,
    ))
    if link is None:
        db.add(DeviceAppLink(
            device_account_id=account.id,
            app_id=client.id,
            first_seen_at=now,
            last_seen_at=now,
        ))
    else:
        link.last_seen_at = now
    account.last_seen_at = now


def _legacy_registrations_for_device(db: Session, device_hash_value: str) -> list[UserRegistration]:
    # Only the ORIGINAL registration device may inherit the old wallet. Devices that
    # were merely linked later by password login are treated as new devices in v0.9,
    # matching the new rule: a different device receives a different wallet.
    return db.scalars(select(UserRegistration).where(
        UserRegistration.device_id_hash == device_hash_value,
        UserRegistration.active.is_(True),
    )).all()


def _create_or_migrate_device_account(db: Session, device_hash_value: str) -> tuple[DeviceAccount, Wallet, bool, bool, int]:
    account = db.scalar(select(DeviceAccount).where(DeviceAccount.device_id_hash == device_hash_value))
    hub = _device_wallet_hub(db)
    if account is not None:
        wallet_row = get_or_create_wallet(db, hub.id, account.external_user_id)
        return account, wallet_row, False, False, 0

    legacy_regs = _legacy_registrations_for_device(db, device_hash_value)
    account = DeviceAccount(
        device_id_hash=device_hash_value,
        external_user_id="dev_" + uuid.uuid4().hex,
        trial_granted=bool(legacy_regs),
        trial_credits=max([x.trial_credits for x in legacy_regs] or [0]),
        migrated_from_legacy=bool(legacy_regs),
        active=True,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(account)
    db.flush()
    wallet_row = get_or_create_wallet(db, hub.id, account.external_user_id)

    if legacy_regs:
        migrated_micros = 0
        old_wallet_ids = set()
        for reg in legacy_regs:
            old_wallet = db.scalar(select(Wallet).where(
                Wallet.app_id == reg.app_id,
                Wallet.external_user_id == reg.external_user_id,
            ).with_for_update())
            if old_wallet is not None:
                old_wallet_ids.add(old_wallet.id)
                if old_wallet.balance_micros:
                    amount = old_wallet.balance_micros
                    migrated_micros += amount
                    old_wallet.balance_micros = 0
                    db.add(LedgerEntry(
                        app_id=reg.app_id,
                        wallet_id=old_wallet.id,
                        kind="migration_out",
                        amount_micros=-amount,
                        balance_after_micros=0,
                        idempotency_key=f"device-migrate-out-{account.id}-{old_wallet.id}",
                        note="Migrasi baki ke Device Wallet v0.9",
                    ))
            # Preserve old usage history under the new device account id.
            usage_rows = db.scalars(select(UsageRecord).where(
                UsageRecord.app_id == reg.app_id,
                UsageRecord.external_user_id == reg.external_user_id,
            )).all()
            for row in usage_rows:
                row.external_user_id = account.external_user_id
                row.wallet_id = wallet_row.id
            req_rows = db.scalars(select(AIRequest).where(
                AIRequest.app_id == reg.app_id,
                AIRequest.external_user_id == reg.external_user_id,
            )).all()
            for row in req_rows:
                row.external_user_id = account.external_user_id
                row.wallet_id = wallet_row.id

        wallet_row.balance_micros += migrated_micros
        if migrated_micros:
            db.add(LedgerEntry(
                app_id=hub.id,
                wallet_id=wallet_row.id,
                kind="migration_in",
                amount_micros=migrated_micros,
                balance_after_micros=wallet_row.balance_micros,
                idempotency_key=f"device-migrate-in-{account.id}",
                note="Baki dompet lama dipulihkan ke Device Wallet v0.9",
            ))
        return account, wallet_row, True, True, micros_to_credits(migrated_micros)

    # Brand-new physical device: grant the trial once for the device, not once per app.
    bonus = credits_to_micros(settings.trial_credits)
    account.trial_granted = True
    account.trial_credits = settings.trial_credits
    wallet_row.balance_micros += bonus
    db.add(LedgerEntry(
        app_id=hub.id,
        wallet_id=wallet_row.id,
        kind="trial_bonus",
        amount_micros=bonus,
        balance_after_micros=wallet_row.balance_micros,
        idempotency_key=f"device-trial-{account.id}",
        note=f"Bonus peranti baharu {settings.trial_credits} kredit",
    ))
    return account, wallet_row, True, False, 0


@app.post("/v1/device/bootstrap")
def device_bootstrap(payload: DeviceBootstrapIn, db: Session = Depends(get_db)):
    """Passwordless bootstrap. Device ID is hashed server-side and never stored raw.

    Existing v0.3-v0.8 OPR registrations on the same device are migrated automatically,
    including their current wallet balance. A brand-new device receives 50 credits once.
    """
    client = _device_app_client(db, payload.app_name)
    device_hash_value = hash_device_id(payload.device_id)
    try:
        account, wallet_row, created, migrated, migrated_credits = _create_or_migrate_device_account(db, device_hash_value)
        _link_device_app_v09(db, account, client)
        db.commit()
        db.refresh(account)
        db.refresh(wallet_row)
    except IntegrityError:
        db.rollback()
        account = db.scalar(select(DeviceAccount).where(DeviceAccount.device_id_hash == device_hash_value))
        if account is None:
            raise
        wallet_row = get_or_create_wallet(db, _device_wallet_hub(db).id, account.external_user_id)
        _link_device_app_v09(db, account, client)
        db.commit()
        created = False
        migrated = account.migrated_from_legacy
        migrated_credits = 0

    return {
        "ok": True,
        "auto_login": True,
        "new_device": created and not migrated,
        "migrated_legacy_wallet": migrated,
        "migrated_credits": migrated_credits,
        "trial_granted": bool(created and not migrated),
        "trial_credits": account.trial_credits,
        "external_user_id": account.external_user_id,
        "device_account_id": account.external_user_id,
        "balance_credits": micros_to_credits(wallet_row.balance_micros),
        "balance_rm": micros_to_rm(wallet_row.balance_micros),
        "app_name": client.name,
        "access_token": create_device_token(account, client, current_device_hash=device_hash_value),
    }


def _device_wallet_for_auth(db: Session, auth: DeviceAuth, *, lock: bool = False) -> Wallet:
    hub = _device_wallet_hub(db)
    stmt = select(Wallet).where(
        Wallet.app_id == hub.id,
        Wallet.external_user_id == auth.account.external_user_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    wallet_row = db.scalar(stmt)
    if wallet_row is None:
        wallet_row = get_or_create_wallet(db, hub.id, auth.account.external_user_id)
    return wallet_row


@app.get("/v1/device/account/summary")
def device_account_summary(auth: DeviceAuth = Depends(require_device_user), db: Session = Depends(get_db)):
    wallet_row = _device_wallet_for_auth(db, auth)
    total_used = db.scalar(select(func.coalesce(func.sum(UsageRecord.charged_micros), 0)).where(
        UsageRecord.external_user_id == auth.account.external_user_id,
    )) or 0
    requests = db.scalar(select(func.count()).select_from(UsageRecord).where(
        UsageRecord.external_user_id == auth.account.external_user_id,
    )) or 0
    links = db.execute(select(AppClient.name).join(DeviceAppLink, DeviceAppLink.app_id == AppClient.id).where(
        DeviceAppLink.device_account_id == auth.account.id,
    ).order_by(AppClient.name)).scalars().all()
    return {
        "device_account_id": auth.account.external_user_id,
        "external_user_id": auth.account.external_user_id,
        "balance_credits": micros_to_credits(wallet_row.balance_micros),
        "used_credits": micros_to_credits(total_used),
        "trial_credits": auth.account.trial_credits,
        "usage_count": requests,
        "linked_apps": list(links),
        "credit_value_sen": 1,
        "wallet_scope": "device",
    }


@app.get("/v1/device/usage/recent")
def device_recent_usage(limit: int = Query(default=30, ge=1, le=100), auth: DeviceAuth = Depends(require_device_user), db: Session = Depends(get_db)):
    rows = db.execute(select(UsageRecord, AppClient.name).join(
        AppClient, UsageRecord.app_id == AppClient.id
    ).where(
        UsageRecord.external_user_id == auth.account.external_user_id,
    ).order_by(UsageRecord.created_at.desc()).limit(limit)).all()
    return [{
        "id": str(row.id),
        "app_name": app_name,
        "provider": row.provider,
        "model": row.model,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "charged_credits": micros_to_credits(row.charged_micros),
        "created_at": row.created_at,
    } for row, app_name in rows]


@app.get("/v1/device/topup/info")
def device_topup_info(auth: DeviceAuth = Depends(require_device_user)):
    del auth
    return {
        "mode": "manual",
        "minimum_rm": settings.min_topup_rm,
        "minimum_credits": settings.min_topup_rm * CREDITS_PER_RM,
        "credit_value_sen": 1,
        "future_provider": "ToyyibPay",
        "online_topup_enabled": False,
        "message": f"Top up kini dibuat secara manual. Minimum RM{settings.min_topup_rm} = {settings.min_topup_rm * CREDITS_PER_RM} kredit.",
    }


@app.post("/v1/device/ai/estimate")
def device_ai_estimate(payload: AIEstimateIn, auth: DeviceAuth = Depends(require_device_user), db: Session = Depends(get_db)):
    account = auth.account
    if payload.external_user_id != account.external_user_id:
        raise HTTPException(status_code=403, detail={"code": "USER_MISMATCH", "message": "ID dompet peranti tidak sepadan."})
    price = get_default_ai_price(db)
    payload.model = price.model
    estimated_input = reservation_input_tokens(payload.input, payload.instructions)
    reserve_micros = charge_for_tokens(
        estimated_input, payload.max_output_tokens,
        input_rate=price.input_micros_per_million,
        output_rate=price.output_micros_per_million,
        markup_bps=price.markup_bps,
    )
    estimate_credits = micros_to_credits(reserve_micros)
    wallet_row = _device_wallet_for_auth(db, auth)
    balance_credits = micros_to_credits(wallet_row.balance_micros)
    token = _make_estimate_token(
        app_id=str(auth.client.id), user_id=account.external_user_id,
        fingerprint=_estimate_fingerprint(payload), estimate_credits=estimate_credits,
    )
    return {
        "ok": True,
        "model": price.model,
        "estimate_credits": estimate_credits,
        "display_price_rm": credits_to_rm_display(estimate_credits),
        "price_multiplier": settings.consumer_price_multiplier,
        "balance_credits": balance_credits,
        "balance_after_estimate_credits": max(0, balance_credits - estimate_credits),
        "estimate_token": token,
        "expires_in_seconds": settings.estimate_ttl_seconds,
        "note": "Anggaran belum menolak kredit. Selepas persetujuan, baki Device Wallet disemak semula sebelum AI dipanggil.",
    }


@app.post("/v1/device/ai/chat")
def device_ai_chat(payload: AIChatIn, auth: DeviceAuth = Depends(require_device_user), db: Session = Depends(get_db)):
    account = auth.account
    if payload.external_user_id != account.external_user_id:
        raise HTTPException(status_code=403, detail={"code": "USER_MISMATCH", "message": "ID dompet peranti tidak sepadan."})
    if not payload.estimate_token:
        raise HTTPException(status_code=409, detail={"code": "ESTIMATE_REQUIRED", "message": "Anggaran kredit mesti dipaparkan dan diluluskan sebelum transaksi AI."})

    price = get_default_ai_price(db)
    payload.model = price.model
    quote = _read_estimate_token(payload.estimate_token)
    if quote.get("app_id") != str(auth.client.id) or quote.get("user_id") != account.external_user_id or quote.get("fp") != _estimate_fingerprint(payload):
        raise HTTPException(status_code=409, detail={"code": "ESTIMATE_MISMATCH", "message": "Maklumat atau model berubah selepas anggaran. Sila anggar semula."})

    current_reserve = charge_for_tokens(
        reservation_input_tokens(payload.input, payload.instructions), payload.max_output_tokens,
        input_rate=price.input_micros_per_million,
        output_rate=price.output_micros_per_million,
        markup_bps=price.markup_bps,
    )
    current_credits = micros_to_credits(current_reserve)
    if current_credits > int(quote.get("credits", 0)):
        raise HTTPException(status_code=409, detail={"code": "ESTIMATE_CHANGED", "message": "Anggaran harga telah berubah. Sila semak anggaran baharu sebelum meneruskan."})

    hub = _device_wallet_hub(db)
    return _ai_chat_core(payload, client=auth.client, wallet_app_id=hub.id, db=db)


@app.get("/v1/admin/device-wallets", dependencies=[Depends(require_admin)])
def admin_device_wallets(limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)):
    hub = _device_wallet_hub(db)
    accounts = db.scalars(select(DeviceAccount).order_by(DeviceAccount.created_at.desc()).limit(limit)).all()
    result = []
    for account in accounts:
        wallet_row = db.scalar(select(Wallet).where(
            Wallet.app_id == hub.id,
            Wallet.external_user_id == account.external_user_id,
        ))
        linked_apps = db.execute(select(AppClient.name).join(DeviceAppLink, DeviceAppLink.app_id == AppClient.id).where(
            DeviceAppLink.device_account_id == account.id,
        ).order_by(AppClient.name)).scalars().all()
        used = db.scalar(select(func.coalesce(func.sum(UsageRecord.charged_micros), 0)).where(
            UsageRecord.external_user_id == account.external_user_id,
        )) or 0
        result.append({
            "device_account_id": account.external_user_id,
            "device_hash_prefix": account.device_id_hash[:12],
            "balance_credits": micros_to_credits(wallet_row.balance_micros if wallet_row else 0),
            "used_credits": micros_to_credits(used),
            "trial_credits": account.trial_credits,
            "migrated_from_legacy": account.migrated_from_legacy,
            "linked_apps": list(linked_apps),
            "created_at": account.created_at,
            "last_seen_at": account.last_seen_at,
        })
    return result


@app.post("/v1/admin/device-wallets/topup-credits", dependencies=[Depends(require_admin)])
def admin_device_wallet_topup(payload: DeviceTopUpCreditsIn, db: Session = Depends(get_db)):
    minimum = settings.min_topup_rm * CREDITS_PER_RM
    if payload.credits < minimum:
        raise HTTPException(status_code=422, detail={
            "code": "MINIMUM_TOPUP",
            "message": f"Top up minimum RM{settings.min_topup_rm} = {minimum} kredit.",
        })
    account = db.scalar(select(DeviceAccount).where(
        DeviceAccount.external_user_id == payload.external_user_id,
        DeviceAccount.active.is_(True),
    ))
    if not account:
        raise HTTPException(status_code=404, detail={"code": "DEVICE_WALLET_NOT_FOUND", "message": "Dompet peranti tidak ditemui."})
    hub = _device_wallet_hub(db)
    existing = db.scalar(select(LedgerEntry).where(
        LedgerEntry.app_id == hub.id,
        LedgerEntry.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        return {
            "ok": True,
            "duplicate": True,
            "balance_credits": micros_to_credits(existing.balance_after_micros),
        }
    wallet_row = get_or_create_wallet(db, hub.id, account.external_user_id)
    amount = credits_to_micros(payload.credits)
    wallet_row.balance_micros += amount
    db.add(LedgerEntry(
        app_id=hub.id,
        wallet_id=wallet_row.id,
        kind="topup",
        amount_micros=amount,
        balance_after_micros=wallet_row.balance_micros,
        idempotency_key=payload.idempotency_key,
        note=payload.note or "Top up manual Device Wallet",
    ))
    db.commit()
    return {
        "ok": True,
        "duplicate": False,
        "added_credits": payload.credits,
        "balance_credits": micros_to_credits(wallet_row.balance_micros),
    }


# ----------------------------- User Registration -----------------------------

@app.post("/v1/auth/register")
def register_user(payload: RegisterUserIn, client: AppClient = Depends(require_app), db: Session = Depends(get_db)):
    email = normalize_email(payload.email)
    device_hash = hash_device_id(payload.device_id)
    existing_email = db.scalar(select(UserRegistration).where(
        UserRegistration.app_id == client.id, UserRegistration.email_normalized == email))
    existing_device = db.scalar(select(UserRegistration).where(
        UserRegistration.app_id == client.id, UserRegistration.device_id_hash == device_hash))

    if existing_email or existing_device:
        if existing_email and existing_device and existing_email.id == existing_device.id:
            existing_email.last_seen_at = datetime.now(timezone.utc)
            db.commit()
            return registration_response(existing_email, db, duplicate=True, trial_granted_now=False)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REGISTRATION_ALREADY_USED",
                "message": "E-mel atau peranti ini telah digunakan untuk pendaftaran percubaan aplikasi ini.",
            },
        )

    reg = UserRegistration(
        app_id=client.id,
        email_normalized=email,
        device_id_hash=device_hash,
        external_user_id="usr_" + uuid.uuid4().hex,
        trial_granted=True,
        trial_credits=settings.trial_credits,
        last_seen_at=datetime.now(timezone.utc),
    )
    try:
        db.add(reg)
        db.flush()
        wallet = get_or_create_wallet(db, client.id, reg.external_user_id)
        bonus = credits_to_micros(settings.trial_credits)
        wallet.balance_micros += bonus
        db.add(LedgerEntry(
            app_id=client.id, wallet_id=wallet.id, kind="trial_bonus",
            amount_micros=bonus, balance_after_micros=wallet.balance_micros,
            idempotency_key=f"trial-{reg.id}",
            note=f"Bonus pendaftaran percubaan {settings.trial_credits} kredit",
        ))
        db.commit()
        db.refresh(reg)
        return registration_response(reg, db, duplicate=False, trial_granted_now=True)
    except IntegrityError:
        db.rollback()
        existing_email = db.scalar(select(UserRegistration).where(
            UserRegistration.app_id == client.id, UserRegistration.email_normalized == email))
        existing_device = db.scalar(select(UserRegistration).where(
            UserRegistration.app_id == client.id, UserRegistration.device_id_hash == device_hash))
        if existing_email and existing_device and existing_email.id == existing_device.id:
            return registration_response(existing_email, db, duplicate=True, trial_granted_now=False)
        raise HTTPException(status_code=409, detail={
            "code": "REGISTRATION_ALREADY_USED",
            "message": "E-mel atau peranti ini telah digunakan untuk pendaftaran percubaan aplikasi ini.",
        })




def _link_user_device(db: Session, reg: UserRegistration, device_hash_value: str) -> None:
    link = db.scalar(select(UserDevice).where(
        UserDevice.app_id == reg.app_id,
        UserDevice.external_user_id == reg.external_user_id,
        UserDevice.device_id_hash == device_hash_value,
    ))
    now = datetime.now(timezone.utc)
    if link is None:
        db.add(UserDevice(
            app_id=reg.app_id,
            external_user_id=reg.external_user_id,
            device_id_hash=device_hash_value,
            first_seen_at=now,
            last_seen_at=now,
        ))
    else:
        link.last_seen_at = now


def _device_has_any_account(db: Session, app_id, device_hash_value: str) -> bool:
    if db.scalar(select(UserDevice.id).where(
        UserDevice.app_id == app_id,
        UserDevice.device_id_hash == device_hash_value,
    )):
        return True
    # Covers v0.5 rows before the startup backfill completes.
    return db.scalar(select(UserRegistration.id).where(
        UserRegistration.app_id == app_id,
        UserRegistration.device_id_hash == device_hash_value,
    )) is not None


def _device_belongs_to_registration(db: Session, reg: UserRegistration, device_hash_value: str) -> bool:
    if hmac.compare_digest(reg.device_id_hash, device_hash_value):
        return True
    return db.scalar(select(UserDevice.id).where(
        UserDevice.app_id == reg.app_id,
        UserDevice.external_user_id == reg.external_user_id,
        UserDevice.device_id_hash == device_hash_value,
    )) is not None


@app.post("/v1/public/register")
def public_register_user(payload: PublicRegisterUserIn, db: Session = Depends(get_db)):
    """Create a new account, or activate a password for a legacy pre-v0.6 account.

    A legacy account can only set its first password from a device already associated
    with that account. This prevents somebody who merely knows an email address from
    claiming an old wallet.
    """
    client = db.scalar(select(AppClient).where(AppClient.name == payload.app_name, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=404, detail={"code": "APP_NOT_FOUND", "message": "Aplikasi tidak didaftarkan pada server."})

    email = normalize_email(payload.email)
    device_hash_value = hash_device_id(payload.device_id)
    existing = db.scalar(select(UserRegistration).where(
        UserRegistration.app_id == client.id,
        UserRegistration.email_normalized == email,
    ))

    if existing:
        if existing.password_hash:
            raise HTTPException(status_code=409, detail={
                "code": "ACCOUNT_EXISTS",
                "message": "Akaun ini sudah mempunyai kata laluan. Gunakan Log Masuk.",
            })
        if not _device_belongs_to_registration(db, existing, device_hash_value):
            raise HTTPException(status_code=409, detail={
                "code": "LEGACY_ACCOUNT_ORIGINAL_DEVICE_REQUIRED",
                "message": "Akaun lama ini belum mempunyai kata laluan. Aktifkan kata laluan pada peranti yang pernah digunakan untuk akaun ini.",
            })
        existing.password_hash = hash_password(payload.password)
        existing.password_set_at = datetime.now(timezone.utc)
        existing.password_must_change = False
        existing.password_reset_hash = None
        existing.password_reset_sent_at = None
        existing.password_reset_expires_at = None
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.failed_login_count = 0
        existing.locked_until = None
        _link_user_device(db, existing, device_hash_value)
        db.commit()
        result = registration_response(existing, db, duplicate=True, trial_granted_now=False)
        result["legacy_activated"] = True
        result["access_token"] = create_user_token(existing, current_device_hash=device_hash_value)
        return result

    if _device_has_any_account(db, client.id, device_hash_value):
        raise HTTPException(status_code=409, detail={
            "code": "DEVICE_TRIAL_ALREADY_USED",
            "message": "Peranti ini telah digunakan untuk akaun OPR. Log masuk ke akaun sedia ada; bonus 50 kredit tidak boleh dituntut berulang.",
        })

    reg = UserRegistration(
        app_id=client.id,
        email_normalized=email,
        device_id_hash=device_hash_value,
        external_user_id="usr_" + uuid.uuid4().hex,
        trial_granted=True,
        trial_credits=settings.trial_credits,
        password_hash=hash_password(payload.password),
        password_set_at=datetime.now(timezone.utc),
        password_must_change=False,
        failed_login_count=0,
        last_seen_at=datetime.now(timezone.utc),
    )
    try:
        db.add(reg)
        db.flush()
        _link_user_device(db, reg, device_hash_value)
        wallet_row = get_or_create_wallet(db, client.id, reg.external_user_id)
        bonus = credits_to_micros(settings.trial_credits)
        wallet_row.balance_micros += bonus
        db.add(LedgerEntry(
            app_id=client.id, wallet_id=wallet_row.id, kind="trial_bonus", amount_micros=bonus,
            balance_after_micros=wallet_row.balance_micros,
            idempotency_key=f"trial-{reg.id}", note=f"Bonus pendaftaran {settings.trial_credits} kredit",
        ))
        db.commit()
        db.refresh(reg)
        result = registration_response(reg, db, duplicate=False, trial_granted_now=True)
        result["legacy_activated"] = False
        result["access_token"] = create_user_token(reg, current_device_hash=device_hash_value)
        return result
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "REGISTRATION_CONFLICT",
            "message": "Akaun atau peranti ini telah didaftarkan. Cuba Log Masuk.",
        })


@app.post("/v1/public/login")
def public_login_user(payload: PublicLoginUserIn, db: Session = Depends(get_db)):
    client = db.scalar(select(AppClient).where(AppClient.name == payload.app_name, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=404, detail={"code": "APP_NOT_FOUND", "message": "Aplikasi tidak didaftarkan pada server."})
    email = normalize_email(payload.email)
    reg = db.scalar(select(UserRegistration).where(
        UserRegistration.app_id == client.id,
        UserRegistration.email_normalized == email,
        UserRegistration.active.is_(True),
    ))
    if not reg:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "Akaun e-mel ini belum didaftarkan."})
    if not reg.password_hash:
        raise HTTPException(status_code=409, detail={
            "code": "PASSWORD_NOT_SET",
            "message": "Akaun ini dibuat sebelum sistem kata laluan. Pada peranti asal, tekan Daftar / Aktifkan Akaun Lama untuk menetapkan kata laluan tanpa kehilangan dompet.",
        })

    now = datetime.now(timezone.utc)
    if reg.locked_until and reg.locked_until > now:
        seconds = max(1, int((reg.locked_until - now).total_seconds()))
        raise HTTPException(status_code=429, detail={
            "code": "LOGIN_TEMPORARILY_LOCKED",
            "message": f"Terlalu banyak percubaan kata laluan. Cuba lagi dalam kira-kira {max(1, seconds // 60)} minit.",
        })

    normal_password_ok = verify_password(reg.password_hash, payload.password)
    temporary_password_ok = False
    if reg.password_reset_hash and reg.password_reset_expires_at and reg.password_reset_expires_at >= now:
        temporary_password_ok = verify_password(reg.password_reset_hash, payload.password)

    if not normal_password_ok and not temporary_password_ok:
        reg.failed_login_count = int(reg.failed_login_count or 0) + 1
        if reg.failed_login_count >= settings.login_max_attempts:
            reg.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            reg.failed_login_count = 0
        db.commit()
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS", "message": "E-mel atau kata laluan tidak betul. Jika kata laluan sementara telah tamat tempoh, minta yang baharu."})

    if temporary_password_ok:
        reg.password_must_change = True
    else:
        # Kata laluan asal masih sah walaupun seseorang meminta reset. Login asal membatalkan
        # kata laluan sementara supaya endpoint lupa kata laluan tidak boleh digunakan untuk DoS.
        if password_needs_rehash(reg.password_hash):
            reg.password_hash = hash_password(payload.password)
            reg.password_set_at = now
        reg.password_must_change = False
        reg.password_reset_hash = None
        reg.password_reset_sent_at = None
        reg.password_reset_expires_at = None
    reg.failed_login_count = 0
    reg.locked_until = None
    reg.last_seen_at = now
    device_hash_value = hash_device_id(payload.device_id)
    _link_user_device(db, reg, device_hash_value)
    db.commit()

    result = registration_response(reg, db, duplicate=True, trial_granted_now=False)
    result["access_token"] = create_user_token(reg, current_device_hash=device_hash_value)
    result["login"] = True
    result["password_must_change"] = bool(reg.password_must_change)
    return result


def _temporary_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value) and any(c in "!@#$%" for c in value)):
            return value


@app.post("/v1/public/forgot-password")
def public_forgot_password(payload: ForgotPasswordIn, db: Session = Depends(get_db)):
    client = db.scalar(select(AppClient).where(AppClient.name == payload.app_name, AppClient.active.is_(True)))
    if not client:
        # Keep the public response generic.
        return {"ok": True, "message": "Jika e-mel berdaftar, kata laluan sementara akan dihantar."}

    email = normalize_email(payload.email)
    reg = db.scalar(select(UserRegistration).where(
        UserRegistration.app_id == client.id,
        UserRegistration.email_normalized == email,
        UserRegistration.active.is_(True),
    ))
    generic = {"ok": True, "message": "Jika e-mel berdaftar, kata laluan sementara akan dihantar."}
    if not reg:
        return generic
    if not email_configured():
        raise HTTPException(status_code=503, detail={
            "code": "EMAIL_NOT_CONFIGURED",
            "message": "Perkhidmatan e-mel belum dikonfigurasi pada server.",
        })

    now = datetime.now(timezone.utc)
    if reg.password_reset_sent_at:
        age = (now - reg.password_reset_sent_at).total_seconds()
        if age < settings.password_reset_cooldown_minutes * 60:
            return generic

    temporary = _temporary_password()
    try:
        send_temporary_password(email, temporary, settings.password_reset_expiry_minutes)
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail={"code": "EMAIL_SEND_FAILED", "message": str(exc)})

    reg.password_reset_hash = hash_password(temporary)
    reg.password_must_change = False
    reg.password_reset_sent_at = now
    reg.password_reset_expires_at = now + timedelta(minutes=settings.password_reset_expiry_minutes)
    reg.failed_login_count = 0
    reg.locked_until = None
    db.commit()
    return generic


@app.post("/v1/user/password/change")
def user_change_password(payload: ChangePasswordIn, auth: UserAuth = Depends(require_user), db: Session = Depends(get_db)):
    reg = auth.registration
    reg.password_hash = hash_password(payload.new_password)
    reg.password_set_at = datetime.now(timezone.utc)
    reg.password_must_change = False
    reg.password_reset_hash = None
    reg.password_reset_sent_at = None
    reg.password_reset_expires_at = None
    reg.failed_login_count = 0
    reg.locked_until = None
    db.commit()
    return {"ok": True, "message": "Kata laluan baharu berjaya disimpan."}


@app.get("/v1/user/models")
def user_models(auth: UserAuth = Depends(require_user), db: Session = Depends(get_db)):
    return ai_models(client=auth.client, db=db)


@app.get("/v1/user/account/summary")
def user_account_summary(auth: UserAuth = Depends(require_user), db: Session = Depends(get_db)):
    reg = auth.registration
    wallet_row = db.scalar(select(Wallet).where(Wallet.app_id == reg.app_id, Wallet.external_user_id == reg.external_user_id))
    balance = wallet_row.balance_micros if wallet_row else 0
    total_used = db.scalar(select(func.coalesce(func.sum(UsageRecord.charged_micros), 0)).where(
        UsageRecord.app_id == reg.app_id, UsageRecord.external_user_id == reg.external_user_id)) or 0
    requests = db.scalar(select(func.count()).select_from(UsageRecord).where(
        UsageRecord.app_id == reg.app_id, UsageRecord.external_user_id == reg.external_user_id)) or 0
    return {
        "email": reg.email_normalized,
        "external_user_id": reg.external_user_id,
        "balance_credits": micros_to_credits(balance),
        "used_credits": micros_to_credits(total_used),
        "trial_credits": reg.trial_credits,
        "usage_count": requests,
        "credit_value_sen": 1,
    }


@app.get("/v1/user/usage/recent")
def user_recent_usage(limit: int = Query(default=20, ge=1, le=100), auth: UserAuth = Depends(require_user), db: Session = Depends(get_db)):
    reg = auth.registration
    rows = db.scalars(select(UsageRecord).where(
        UsageRecord.app_id == reg.app_id,
        UsageRecord.external_user_id == reg.external_user_id,
    ).order_by(UsageRecord.created_at.desc()).limit(limit)).all()
    return [{
        "id": str(x.id), "provider": x.provider, "model": x.model,
        "input_tokens": x.input_tokens, "output_tokens": x.output_tokens,
        "charged_credits": micros_to_credits(x.charged_micros), "created_at": x.created_at,
    } for x in rows]


@app.get("/v1/user/topup/info")
def user_topup_info(auth: UserAuth = Depends(require_user)):
    del auth
    return {
        "mode": "manual",
        "minimum_rm": settings.min_topup_rm,
        "minimum_credits": settings.min_topup_rm * CREDITS_PER_RM,
        "credit_value_sen": 1,
        "future_provider": "ToyyibPay",
        "online_topup_enabled": False,
        "message": f"Top up kini dibuat secara manual. Minimum RM{settings.min_topup_rm} = {settings.min_topup_rm * CREDITS_PER_RM} kredit.",
    }


@app.post("/v1/user/ai/estimate")
def user_ai_estimate(payload: AIEstimateIn, auth: UserAuth = Depends(require_user), db: Session = Depends(get_db)):
    reg = auth.registration
    if payload.external_user_id != reg.external_user_id:
        raise HTTPException(status_code=403, detail={"code": "USER_MISMATCH", "message": "ID pengguna tidak sepadan."})

    # Model dipilih di server. Klien tidak menentukan model.
    price = get_default_ai_price(db)
    payload.model = price.model
    estimated_input = reservation_input_tokens(payload.input, payload.instructions)
    reserve_micros = charge_for_tokens(
        estimated_input, payload.max_output_tokens,
        input_rate=price.input_micros_per_million,
        output_rate=price.output_micros_per_million,
        markup_bps=price.markup_bps,
    )
    estimate_credits = micros_to_credits(reserve_micros)
    wallet_row = db.scalar(select(Wallet).where(Wallet.app_id == reg.app_id, Wallet.external_user_id == reg.external_user_id))
    balance_micros = wallet_row.balance_micros if wallet_row else 0
    balance_credits = micros_to_credits(balance_micros)
    token = _make_estimate_token(
        app_id=str(reg.app_id), user_id=reg.external_user_id,
        fingerprint=_estimate_fingerprint(payload), estimate_credits=estimate_credits,
    )
    return {
        "ok": True,
        "model": price.model,
        "estimate_credits": estimate_credits,
        "display_price_rm": credits_to_rm_display(estimate_credits),
        "price_multiplier": settings.consumer_price_multiplier,
        "balance_credits": balance_credits,
        "balance_after_estimate_credits": max(0, balance_credits - estimate_credits),
        "estimate_token": token,
        "expires_in_seconds": settings.estimate_ttl_seconds,
        "note": "Anggaran belum menolak kredit. Harga pengguna ialah kos sebenar RM penyedia didarab 10 dan dibundarkan kepada kredit penuh. Selepas pengguna bersetuju, baki disemak semula sebelum AI dipanggil.",
    }


@app.post("/v1/user/ai/chat")
def user_ai_chat(payload: AIChatIn, auth: UserAuth = Depends(require_user), db: Session = Depends(get_db)):
    reg = auth.registration
    if payload.external_user_id != reg.external_user_id:
        raise HTTPException(status_code=403, detail={"code": "USER_MISMATCH", "message": "ID pengguna tidak sepadan."})
    if not payload.estimate_token:
        raise HTTPException(status_code=409, detail={"code": "ESTIMATE_REQUIRED", "message": "Anggaran kredit mesti dipaparkan dan diluluskan sebelum transaksi AI."})

    # Pilih model sekali lagi di server. Jika konfigurasi berubah selepas anggaran, fingerprint
    # akan berbeza dan pengguna mesti melihat anggaran baharu.
    price = get_default_ai_price(db)
    payload.model = price.model
    quote = _read_estimate_token(payload.estimate_token)
    if quote.get("app_id") != str(reg.app_id) or quote.get("user_id") != reg.external_user_id or quote.get("fp") != _estimate_fingerprint(payload):
        raise HTTPException(status_code=409, detail={"code": "ESTIMATE_MISMATCH", "message": "Maklumat atau model berubah selepas anggaran. Sila anggar semula."})

    current_reserve = charge_for_tokens(
        reservation_input_tokens(payload.input, payload.instructions), payload.max_output_tokens,
        input_rate=price.input_micros_per_million,
        output_rate=price.output_micros_per_million,
        markup_bps=price.markup_bps,
    )
    current_credits = micros_to_credits(current_reserve)
    if current_credits > int(quote.get("credits", 0)):
        raise HTTPException(status_code=409, detail={"code": "ESTIMATE_CHANGED", "message": "Anggaran harga telah berubah. Sila semak anggaran baharu sebelum meneruskan."})

    # Semakan baki muktamad berlaku dalam ai_chat sebelum sebarang panggilan OpenAI.
    return ai_chat(payload, client=auth.client, db=db)


# ----------------------------- App Billing API -----------------------------

@app.get("/v1/wallet")
def wallet(external_user_id: str, client: AppClient = Depends(require_app), db: Session = Depends(get_db)):
    row = db.scalar(select(Wallet).where(Wallet.app_id == client.id, Wallet.external_user_id == external_user_id))
    return {
        "app": client.name,
        "external_user_id": external_user_id,
        "balance_rm": micros_to_rm(row.balance_micros if row else 0),
        "balance_credits": micros_to_credits(row.balance_micros if row else 0),
    }


@app.post("/v1/usage/charge")
def charge(payload: ChargeIn, client: AppClient = Depends(require_app), db: Session = Depends(get_db)):
    # Preserved from v0.1 for compatibility with existing clients.
    existing = db.scalar(select(UsageRecord).where(
        UsageRecord.app_id == client.id,
        UsageRecord.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        wallet_row = db.get(Wallet, existing.wallet_id)
        return {
            "ok": True, "duplicate": True,
            "charged_rm": micros_to_rm(existing.charged_micros),
            "charged_credits": micros_to_credits(existing.charged_micros),
            "balance_rm": micros_to_rm(wallet_row.balance_micros),
            "balance_credits": micros_to_credits(wallet_row.balance_micros),
        }

    price = get_active_price(db, payload.provider, payload.model)
    charge_micros = charge_for_tokens(
        payload.input_tokens, payload.output_tokens,
        input_rate=price.input_micros_per_million,
        output_rate=price.output_micros_per_million,
        markup_bps=price.markup_bps,
    )

    try:
        wallet_row = get_or_create_wallet(db, client.id, payload.external_user_id)
        if wallet_row.balance_micros < charge_micros:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "INSUFFICIENT_CREDIT",
                    "required_rm": micros_to_rm(charge_micros),
                    "balance_rm": micros_to_rm(wallet_row.balance_micros),
                },
            )

        wallet_row.balance_micros -= charge_micros
        usage = UsageRecord(
            app_id=client.id, wallet_id=wallet_row.id,
            external_user_id=payload.external_user_id,
            provider=payload.provider, model=payload.model,
            input_tokens=payload.input_tokens, output_tokens=payload.output_tokens,
            charged_micros=charge_micros, idempotency_key=payload.idempotency_key,
            metadata_json=payload.metadata,
        )
        db.add(usage)
        db.flush()
        db.add(LedgerEntry(
            app_id=client.id, wallet_id=wallet_row.id, kind="charge",
            amount_micros=-charge_micros, balance_after_micros=wallet_row.balance_micros,
            idempotency_key=payload.idempotency_key,
            note=f"AI usage {payload.provider}/{payload.model}",
        ))
        db.commit()
        return {
            "ok": True, "duplicate": False,
            "charged_rm": micros_to_rm(charge_micros),
            "charged_credits": micros_to_credits(charge_micros),
            "balance_rm": micros_to_rm(wallet_row.balance_micros),
            "balance_credits": micros_to_credits(wallet_row.balance_micros),
        }
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(UsageRecord).where(
            UsageRecord.app_id == client.id, UsageRecord.idempotency_key == payload.idempotency_key))
        if existing:
            wallet_row = db.get(Wallet, existing.wallet_id)
            return {
                "ok": True, "duplicate": True,
                "charged_rm": micros_to_rm(existing.charged_micros),
                "charged_credits": micros_to_credits(existing.charged_micros),
                "balance_rm": micros_to_rm(wallet_row.balance_micros),
                "balance_credits": micros_to_credits(wallet_row.balance_micros),
            }
        raise


@app.get("/v1/usage/recent")
def recent_usage(
    external_user_id: str,
    limit: int = 50,
    client: AppClient = Depends(require_app),
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 200)
    rows = db.scalars(select(UsageRecord).where(
        UsageRecord.app_id == client.id,
        UsageRecord.external_user_id == external_user_id,
    ).order_by(UsageRecord.created_at.desc()).limit(limit)).all()
    return [{
        "id": str(x.id), "provider": x.provider, "model": x.model,
        "input_tokens": x.input_tokens, "output_tokens": x.output_tokens,
        "charged_rm": micros_to_rm(x.charged_micros), "charged_credits": micros_to_credits(x.charged_micros), "created_at": x.created_at,
    } for x in rows]


# ----------------------------- AI Gateway -----------------------------

@app.get("/v1/ai/models")
def ai_models(client: AppClient = Depends(require_app), db: Session = Depends(get_db)):
    del client  # authentication is the purpose of this dependency
    rows = db.scalars(select(ModelPrice).where(
        ModelPrice.provider == "openai", ModelPrice.active.is_(True)
    ).order_by(ModelPrice.model)).all()
    return [{
        "model": x.model,
        "input_rm_per_million": micros_to_rm(x.input_micros_per_million),
        "output_rm_per_million": micros_to_rm(x.output_micros_per_million),
        "markup_percent": str(Decimal(x.markup_bps) / Decimal(100)),
    } for x in rows]


def _ai_chat_core(payload: AIChatIn, *, client: AppClient, wallet_app_id, db: Session):
    existing = db.scalar(select(AIRequest).where(
        AIRequest.app_id == client.id,
        AIRequest.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        return replay_ai_request(existing, db)

    if not payload.model:
        payload.model = get_default_ai_price(db).model
    price = get_active_price(db, "openai", payload.model)
    estimated_input = reservation_input_tokens(payload.input, payload.instructions)
    reserve_micros = charge_for_tokens(
        estimated_input, payload.max_output_tokens,
        input_rate=price.input_micros_per_million,
        output_rate=price.output_micros_per_million,
        markup_bps=price.markup_bps,
    )

    # Phase 1: reserve enough wallet credit before incurring provider cost.
    try:
        wallet_row = get_or_create_wallet(db, wallet_app_id, payload.external_user_id)
        if wallet_row.balance_micros < reserve_micros:
            current_balance = wallet_row.balance_micros
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "INSUFFICIENT_CREDIT",
                    "required_reserve_rm": micros_to_rm(reserve_micros),
                    "required_reserve_credits": micros_to_credits(reserve_micros),
                    "balance_rm": micros_to_rm(current_balance),
                    "balance_credits": micros_to_credits(current_balance),
                    "note": "Reserve is conservative; final charge uses actual OpenAI token usage.",
                },
            )

        wallet_row.balance_micros -= reserve_micros
        ai_req = AIRequest(
            app_id=client.id,
            wallet_id=wallet_row.id,
            external_user_id=payload.external_user_id,
            provider="openai",
            model=payload.model,
            idempotency_key=payload.idempotency_key,
            status="pending",
            reserved_micros=reserve_micros,
            input_micros_per_million=price.input_micros_per_million,
            output_micros_per_million=price.output_micros_per_million,
            markup_bps=price.markup_bps,
            metadata_json=payload.metadata,
        )
        db.add(ai_req)
        db.flush()
        db.add(LedgerEntry(
            app_id=client.id, wallet_id=wallet_row.id, kind="reserve",
            amount_micros=-reserve_micros, balance_after_micros=wallet_row.balance_micros,
            idempotency_key=ai_ledger_key(payload.idempotency_key, "reserve"),
            note=f"Reserve OpenAI {payload.model}",
        ))
        db.commit()
        db.refresh(ai_req)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(AIRequest).where(
            AIRequest.app_id == client.id,
            AIRequest.idempotency_key == payload.idempotency_key,
        ))
        if existing:
            return replay_ai_request(existing, db)
        raise

    # Phase 2: provider call happens outside a database transaction/row lock.
    try:
        result = create_response(
            model=payload.model,
            input_text=payload.input,
            instructions=payload.instructions,
            max_output_tokens=payload.max_output_tokens,
        )
    except OpenAIGatewayError as exc:
        req = db.scalar(select(AIRequest).where(AIRequest.id == ai_req.id).with_for_update())
        wallet_row = db.scalar(select(Wallet).where(Wallet.id == ai_req.wallet_id).with_for_update())
        if req and req.status == "pending":
            wallet_row.balance_micros += req.reserved_micros
            req.status = "failed"
            req.error_code = exc.code[:80]
            req.error_detail = exc.detail[:4000]
            req.completed_at = datetime.now(timezone.utc)
            db.add(LedgerEntry(
                app_id=client.id, wallet_id=wallet_row.id, kind="refund",
                amount_micros=req.reserved_micros, balance_after_micros=wallet_row.balance_micros,
                idempotency_key=ai_ledger_key(payload.idempotency_key, "provider-refund"),
                note=f"Refund failed OpenAI request: {exc.code}",
            ))
            db.commit()
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.detail})

    # Phase 3: settle against actual token usage and refund unused reservation.
    req = db.scalar(select(AIRequest).where(AIRequest.id == ai_req.id).with_for_update())
    wallet_row = db.scalar(select(Wallet).where(Wallet.id == ai_req.wallet_id).with_for_update())
    if not req or not wallet_row:
        db.rollback()
        raise HTTPException(status_code=500, detail="AI settlement record missing")

    actual_charge = charge_for_tokens(
        result.input_tokens, result.output_tokens,
        input_rate=req.input_micros_per_million,
        output_rate=req.output_micros_per_million,
        markup_bps=req.markup_bps,
    )
    adjustment = req.reserved_micros - actual_charge
    if adjustment > 0:
        wallet_row.balance_micros += adjustment
        db.add(LedgerEntry(
            app_id=client.id, wallet_id=wallet_row.id, kind="refund",
            amount_micros=adjustment, balance_after_micros=wallet_row.balance_micros,
            idempotency_key=ai_ledger_key(payload.idempotency_key, "settle-refund"),
            note=f"Unused AI reserve refund {payload.model}",
        ))
    elif adjustment < 0:
        # Conservative byte-based reservation should make this rare. If it occurs, the provider cost
        # has already been incurred, so record the extra amount even if the wallet becomes negative.
        extra = -adjustment
        wallet_row.balance_micros -= extra
        db.add(LedgerEntry(
            app_id=client.id, wallet_id=wallet_row.id, kind="charge",
            amount_micros=-extra, balance_after_micros=wallet_row.balance_micros,
            idempotency_key=ai_ledger_key(payload.idempotency_key, "settle-extra"),
            note=f"AI settlement extra charge {payload.model}",
        ))

    usage = UsageRecord(
        app_id=client.id, wallet_id=wallet_row.id,
        external_user_id=payload.external_user_id,
        provider="openai", model=payload.model,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        charged_micros=actual_charge, idempotency_key=payload.idempotency_key,
        metadata_json=payload.metadata,
    )
    db.add(usage)
    req.status = "completed"
    req.charged_micros = actual_charge
    req.input_tokens = result.input_tokens
    req.output_tokens = result.output_tokens
    req.response_id = result.response_id
    req.output_text = result.output_text
    req.completed_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "ok": True,
        "duplicate": False,
        "request_id": str(req.id),
        "openai_response_id": result.response_id,
        "model": payload.model,
        "output_text": result.output_text,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "charged_rm": micros_to_rm(actual_charge),
        "charged_credits": micros_to_credits(actual_charge),
        "balance_rm": micros_to_rm(wallet_row.balance_micros),
        "balance_credits": micros_to_credits(wallet_row.balance_micros),
    }


@app.post("/v1/ai/chat")
def ai_chat(payload: AIChatIn, client: AppClient = Depends(require_app), db: Session = Depends(get_db)):
    return _ai_chat_core(payload, client=client, wallet_app_id=client.id, db=db)
