from decimal import Decimal
from typing import Any
from pydantic import BaseModel, Field


class RegisterAppIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class RegisterAppOut(BaseModel):
    app_id: str
    name: str
    api_key: str


class TopUpIn(BaseModel):
    app_name: str
    external_user_id: str = Field(min_length=1, max_length=160)
    amount_rm: Decimal = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=160)
    note: str | None = None


class PricingIn(BaseModel):
    provider: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=120)
    input_rm_per_million: Decimal = Field(ge=0)
    output_rm_per_million: Decimal = Field(ge=0)
    markup_percent: Decimal = Field(default=Decimal("0"), ge=0, le=1000)
    active: bool = True


class ChargeIn(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=120)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=160)
    metadata: dict[str, Any] | None = None


class AIChatIn(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=160)
    model: str | None = Field(default=None, max_length=120)
    input: str = Field(min_length=1, max_length=200_000)
    instructions: str | None = Field(default=None, max_length=50_000)
    max_output_tokens: int = Field(default=2048, ge=16, le=32768)
    idempotency_key: str = Field(min_length=8, max_length=160)
    metadata: dict[str, Any] | None = None
    estimate_token: str | None = Field(default=None, max_length=4096)


class AIEstimateIn(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=160)
    model: str | None = Field(default=None, max_length=120)
    input: str = Field(min_length=1, max_length=200_000)
    instructions: str | None = Field(default=None, max_length=50_000)
    max_output_tokens: int = Field(default=2048, ge=16, le=32768)
    metadata: dict[str, Any] | None = None


class RegisterUserIn(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    device_id: str = Field(min_length=6, max_length=300)


class PublicRegisterUserIn(RegisterUserIn):
    app_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class PublicLoginUserIn(BaseModel):
    app_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    device_id: str = Field(min_length=6, max_length=300)


class ForgotPasswordIn(BaseModel):
    app_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)


class ChangePasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class AdminSetPasswordIn(BaseModel):
    app_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    new_password: str = Field(min_length=8, max_length=128)


class TopUpCreditsIn(BaseModel):
    app_name: str
    external_user_id: str = Field(min_length=1, max_length=160)
    credits: int = Field(gt=0, le=10_000_000)
    idempotency_key: str = Field(min_length=8, max_length=160)
    note: str | None = None


class DeviceBootstrapIn(BaseModel):
    app_name: str = Field(min_length=2, max_length=120)
    device_id: str = Field(min_length=6, max_length=300)


class DeviceTopUpCreditsIn(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=160)
    credits: int = Field(gt=0, le=10_000_000)
    idempotency_key: str = Field(min_length=8, max_length=160)
    note: str | None = None
