from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://hesaplik:hesaplik@localhost:5432/hesaplik"
    log_level: str = "info"


settings = Settings()
