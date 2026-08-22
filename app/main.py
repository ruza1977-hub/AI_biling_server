from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine
from .models import AIRequest, AppClient, LedgerEntry, ModelPrice, UsageRecord, Wallet
from .openai_gateway import OpenAIGatewayError, create_response
from .schemas import AIChatIn, ChargeIn, PricingIn, RegisterAppIn, RegisterAppOut, TopUpIn
from .security import generate_api_key, get_db, hash_api_key, require_admin, require_app

MICROS_PER_RM = 1_000_000
TOKENS_PER_MILLION = 1_000_000


@asynccontextmanager
async def lifespan(_: FastAPI):
    # v0.2 adds only a new ai_requests table, so create_all safely upgrades v0.1 databases.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.service_name, version="0.2.0", lifespan=lifespan)


def micros_to_rm(value: int) -> str:
    return str((Decimal(value) / Decimal(MICROS_PER_RM)).quantize(Decimal("0.000001")))


def rm_to_micros(value: Decimal) -> int:
    return int((value * MICROS_PER_RM).to_integral_value(rounding=ROUND_CEILING))


def charge_for_tokens(input_tokens: int, output_tokens: int, *, input_rate: int, output_rate: int, markup_bps: int) -> int:
    raw_cost = (
        Decimal(input_tokens) * Decimal(input_rate)
        + Decimal(output_tokens) * Decimal(output_rate)
    ) / Decimal(TOKENS_PER_MILLION)
    with_markup = raw_cost * (Decimal(10_000 + markup_bps) / Decimal(10_000))
    if input_tokens == 0 and output_tokens == 0:
        return 0
    return max(1, int(with_markup.to_integral_value(rounding=ROUND_CEILING)))


def reservation_input_tokens(input_text: str, instructions: str | None) -> int:
    # Conservative upper bound for normal UTF-8 text: one token cannot contain less than one byte.
    # The extra allowance covers request framing and model-side tokenization overhead.
    total_bytes = len(input_text.encode("utf-8")) + len((instructions or "").encode("utf-8"))
    return max(256, total_bytes + 2048)


def ai_ledger_key(idempotency_key: str, stage: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:48]
    return f"ai-{digest}-{stage}"


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
            "balance_rm": micros_to_rm(balance),
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
        "version": "0.2.0",
        "openai_configured": bool(settings.openai_api_key),
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
    client = db.scalar(select(AppClient).where(AppClient.name == payload.app_name, AppClient.active.is_(True)))
    if not client:
        raise HTTPException(status_code=404, detail="App not found")

    existing = db.scalar(select(LedgerEntry).where(
        LedgerEntry.app_id == client.id,
        LedgerEntry.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        return {"ok": True, "duplicate": True, "balance_rm": micros_to_rm(existing.balance_after_micros)}

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
        return {"ok": True, "duplicate": False, "balance_rm": micros_to_rm(wallet.balance_micros)}
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(LedgerEntry).where(
            LedgerEntry.app_id == client.id, LedgerEntry.idempotency_key == payload.idempotency_key))
        if existing:
            return {"ok": True, "duplicate": True, "balance_rm": micros_to_rm(existing.balance_after_micros)}
        raise


@app.get("/v1/admin/dashboard", dependencies=[Depends(require_admin)])
def admin_dashboard(db: Session = Depends(get_db)):
    apps = db.scalar(select(func.count()).select_from(AppClient)) or 0
    wallets = db.scalar(select(func.count()).select_from(Wallet)) or 0
    balance_micros = db.scalar(select(func.coalesce(func.sum(Wallet.balance_micros), 0))) or 0
    total_charged = db.scalar(select(func.coalesce(func.sum(UsageRecord.charged_micros), 0))) or 0
    ai_requests = db.scalar(select(func.count()).select_from(AIRequest)) or 0
    completed = db.scalar(select(func.count()).select_from(AIRequest).where(AIRequest.status == "completed")) or 0
    return {
        "apps": apps,
        "wallets": wallets,
        "total_balance_rm": micros_to_rm(balance_micros),
        "total_ai_charged_rm": micros_to_rm(total_charged),
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
        "balance_after_rm": micros_to_rm(entry.balance_after_micros),
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
        "charged_rm": micros_to_rm(u.charged_micros), "created_at": u.created_at,
    } for u, name in rows]


# ----------------------------- App Billing API -----------------------------

@app.get("/v1/wallet")
def wallet(external_user_id: str, client: AppClient = Depends(require_app), db: Session = Depends(get_db)):
    row = db.scalar(select(Wallet).where(Wallet.app_id == client.id, Wallet.external_user_id == external_user_id))
    return {
        "app": client.name,
        "external_user_id": external_user_id,
        "balance_rm": micros_to_rm(row.balance_micros if row else 0),
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
            "balance_rm": micros_to_rm(wallet_row.balance_micros),
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
            "balance_rm": micros_to_rm(wallet_row.balance_micros),
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
                "balance_rm": micros_to_rm(wallet_row.balance_micros),
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
        "charged_rm": micros_to_rm(x.charged_micros), "created_at": x.created_at,
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


@app.post("/v1/ai/chat")
def ai_chat(payload: AIChatIn, client: AppClient = Depends(require_app), db: Session = Depends(get_db)):
    existing = db.scalar(select(AIRequest).where(
        AIRequest.app_id == client.id,
        AIRequest.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        return replay_ai_request(existing, db)

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
        wallet_row = get_or_create_wallet(db, client.id, payload.external_user_id)
        if wallet_row.balance_micros < reserve_micros:
            current_balance = wallet_row.balance_micros
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "INSUFFICIENT_CREDIT",
                    "required_reserve_rm": micros_to_rm(reserve_micros),
                    "balance_rm": micros_to_rm(current_balance),
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
        "balance_rm": micros_to_rm(wallet_row.balance_micros),
    }
