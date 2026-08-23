from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    master_admin_key: str
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = 120.0
    service_name: str = "AI Billing Server"
    trial_credits: int = 50
    device_hash_secret: str = ""
    user_token_secret: str = ""
    public_opr_app_name: str = "OPR Android"
    device_wallet_hub_app_name: str = "__DEVICE_WALLET__"
    public_device_apps: str = "OPR Android,GBK Digital,SPK,StudyMate"
    estimate_ttl_seconds: int = 300
    min_topup_rm: int = 10
    consumer_price_multiplier: int = 10
    default_ai_model: str = "gpt-5.6-luna"
    user_token_days: int = 30
    login_max_attempts: int = 5
    login_lock_minutes: int = 15
    password_reset_expiry_minutes: int = 30
    password_reset_cooldown_minutes: int = 3
    # E-mel transaksi melalui SendGrid Web API (HTTPS/443).
    sendgrid_api_key: str = ""
    sendgrid_api_base: str = "https://api.sendgrid.com"
    email_from_email: str = ""
    email_from_name: str = "OPR Android"
    email_reply_to: str = ""
    email_timeout_seconds: float = 20.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
