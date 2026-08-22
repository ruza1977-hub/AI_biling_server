from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    master_admin_key: str
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = 120.0
    service_name: str = "AI Billing Server"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
