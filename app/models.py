import uuid
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class AppClient(Base):
    __tablename__ = "app_clients"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("app_id", "external_user_id", name="uq_wallet_app_user"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_clients.id", ondelete="CASCADE"), index=True)
    external_user_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    balance_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (UniqueConstraint("app_id", "idempotency_key", name="uq_ledger_app_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_clients.id", ondelete="CASCADE"), index=True)
    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # topup | charge | reserve | refund | adjustment
    amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ModelPrice(Base):
    __tablename__ = "model_prices"
    __table_args__ = (UniqueConstraint("provider", "model", name="uq_provider_model"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_micros_per_million: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_micros_per_million: Mapped[int] = mapped_column(BigInteger, nullable=False)
    markup_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("app_id", "idempotency_key", name="uq_usage_app_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_clients.id", ondelete="CASCADE"), index=True)
    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), index=True)
    external_user_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    charged_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AIRequest(Base):
    __tablename__ = "ai_requests"
    __table_args__ = (UniqueConstraint("app_id", "idempotency_key", name="uq_ai_request_app_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_clients.id", ondelete="CASCADE"), index=True)
    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), index=True)
    external_user_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), default="openai", nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    reserved_micros: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    input_micros_per_million: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_micros_per_million: Mapped[int] = mapped_column(BigInteger, nullable=False)
    markup_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    charged_micros: Mapped[int | None] = mapped_column(BigInteger)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    response_id: Mapped[str | None] = mapped_column(String(160))
    output_text: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRegistration(Base):
    __tablename__ = "user_registrations"
    __table_args__ = (
        UniqueConstraint("app_id", "email_normalized", name="uq_registration_app_email"),
        UniqueConstraint("app_id", "device_id_hash", name="uq_registration_app_device"),
        UniqueConstraint("app_id", "external_user_id", name="uq_registration_app_user"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_clients.id", ondelete="CASCADE"), index=True)
    email_normalized: Mapped[str] = mapped_column(String(254), index=True, nullable=False)
    device_id_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    trial_granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trial_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_must_change: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_reset_hash: Mapped[str | None] = mapped_column(String(255))
    password_reset_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserDevice(Base):
    __tablename__ = "user_devices"
    __table_args__ = (
        UniqueConstraint("app_id", "external_user_id", "device_id_hash", name="uq_user_device_link"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_clients.id", ondelete="CASCADE"), index=True)
    external_user_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    device_id_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeviceAccount(Base):
    __tablename__ = "device_accounts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    email_normalized: Mapped[str | None] = mapped_column(String(254), unique=True, index=True)
    trial_granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trial_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    migrated_from_legacy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeviceAppLink(Base):
    __tablename__ = "device_app_links"
    __table_args__ = (UniqueConstraint("device_account_id", "app_id", name="uq_device_app_link"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("device_accounts.id", ondelete="CASCADE"), index=True)
    app_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_clients.id", ondelete="CASCADE"), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
