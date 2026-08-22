from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    master_admin_key: str
    service_name: str = "AI Billing Server"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
