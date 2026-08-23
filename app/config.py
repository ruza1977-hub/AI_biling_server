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
    estimate_ttl_seconds: int = 300
    min_topup_rm: int = 10
    consumer_price_multiplier: int = 10
    default_ai_model: str = "gpt-5.6-luna"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
