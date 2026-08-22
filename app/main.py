from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_CEILING
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .config import settings
from .db import Base, engine
from .models import AppClient, LedgerEntry, ModelPrice, UsageRecord, Wallet
from .schemas import ChargeIn, PricingIn, RegisterAppIn, RegisterAppOut, TopUpIn
from .security import generate_api_key, get_db, hash_api_key, require_admin, require_app

MICROS_PER_RM = 1_000_000

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.service_name, version="0.1.0", lifespan=lifespan)


def micros_to_rm(value: int) -> str:
    return str((Decimal(value) / Decimal(MICROS_PER_RM)).quantize(Decimal("0.000001")))


def rm_to_micros(value: Decimal) -> int:
    return int((value * MICROS_PER_RM).to_integral_value(rounding=ROUND_CEILING))


def get_or_create_wallet(db: Session, app_id, external_user_id: str) -> Wallet:
    # PostgreSQL upsert avoids a race if two requests create the same wallet simultaneously.
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


@app.get("/health")
def health():
    return {"ok": True, "service": settings.service_name, "version": "0.1.0"}


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


@app.post("/v1/admin/pricing", dependencies=[Depends(require_admin)])
def upsert_pricing(payload: PricingIn, db: Session = Depends(get_db)):
    row = db.scalar(select(ModelPrice).where(ModelPrice.provider == payload.provider, ModelPrice.model == payload.model))
    markup_bps = int((payload.markup_percent * 100).to_integral_value(rounding=ROUND_CEILING))
    if row is None:
        row = ModelPrice(provider=payload.provider, model=payload.model,
                         input_micros_per_million=rm_to_micros(payload.input_rm_per_million),
                         output_micros_per_million=rm_to_micros(payload.output_rm_per_million),
                         markup_bps=markup_bps, active=payload.active)
        db.add(row)
    else:
        row.input_micros_per_million = rm_to_micros(payload.input_rm_per_million)
        row.output_micros_per_million = rm_to_micros(payload.output_rm_per_million)
        row.markup_bps = markup_bps
        row.active = payload.active
    db.commit()
    return {"ok": True, "provider": row.provider, "model": row.model}


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
        db.add(LedgerEntry(app_id=client.id, wallet_id=wallet.id, kind="topup", amount_micros=amount,
                           balance_after_micros=wallet.balance_micros, idempotency_key=payload.idempotency_key,
                           note=payload.note))
        db.commit()
        return {"ok": True, "duplicate": False, "balance_rm": micros_to_rm(wallet.balance_micros)}
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(LedgerEntry).where(
            LedgerEntry.app_id == client.id, LedgerEntry.idempotency_key == payload.idempotency_key))
        if existing:
            return {"ok": True, "duplicate": True, "balance_rm": micros_to_rm(existing.balance_after_micros)}
        raise


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
    existing = db.scalar(select(UsageRecord).where(
        UsageRecord.app_id == client.id,
        UsageRecord.idempotency_key == payload.idempotency_key,
    ))
    if existing:
        wallet_row = db.get(Wallet, existing.wallet_id)
        return {"ok": True, "duplicate": True, "charged_rm": micros_to_rm(existing.charged_micros),
                "balance_rm": micros_to_rm(wallet_row.balance_micros)}

    price = db.scalar(select(ModelPrice).where(
        ModelPrice.provider == payload.provider,
        ModelPrice.model == payload.model,
        ModelPrice.active.is_(True),
    ))
    if not price:
        raise HTTPException(status_code=400, detail="Pricing not configured for this provider/model")

    raw_cost = (
        Decimal(payload.input_tokens) * Decimal(price.input_micros_per_million)
        + Decimal(payload.output_tokens) * Decimal(price.output_micros_per_million)
    ) / Decimal(1_000_000)
    with_markup = raw_cost * (Decimal(10_000 + price.markup_bps) / Decimal(10_000))
    charge_micros = max(1, int(with_markup.to_integral_value(rounding=ROUND_CEILING)))

    try:
        wallet_row = get_or_create_wallet(db, client.id, payload.external_user_id)
        if wallet_row.balance_micros < charge_micros:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"code": "INSUFFICIENT_CREDIT", "required_rm": micros_to_rm(charge_micros),
                        "balance_rm": micros_to_rm(wallet_row.balance_micros)},
            )

        wallet_row.balance_micros -= charge_micros
        usage = UsageRecord(app_id=client.id, wallet_id=wallet_row.id, external_user_id=payload.external_user_id,
                            provider=payload.provider, model=payload.model, input_tokens=payload.input_tokens,
                            output_tokens=payload.output_tokens, charged_micros=charge_micros,
                            idempotency_key=payload.idempotency_key, metadata_json=payload.metadata)
        db.add(usage)
        db.flush()
        db.add(LedgerEntry(app_id=client.id, wallet_id=wallet_row.id, kind="charge", amount_micros=-charge_micros,
                           balance_after_micros=wallet_row.balance_micros,
                           idempotency_key=payload.idempotency_key, note=f"AI usage {payload.provider}/{payload.model}"))
        db.commit()
        return {"ok": True, "duplicate": False, "charged_rm": micros_to_rm(charge_micros),
                "balance_rm": micros_to_rm(wallet_row.balance_micros)}
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(UsageRecord).where(
            UsageRecord.app_id == client.id, UsageRecord.idempotency_key == payload.idempotency_key))
        if existing:
            wallet_row = db.get(Wallet, existing.wallet_id)
            return {"ok": True, "duplicate": True, "charged_rm": micros_to_rm(existing.charged_micros),
                    "balance_rm": micros_to_rm(wallet_row.balance_micros)}
        raise


@app.get("/v1/usage/recent")
def recent_usage(external_user_id: str, limit: int = 50,
                 client: AppClient = Depends(require_app), db: Session = Depends(get_db)):
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
