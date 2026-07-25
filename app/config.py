from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://hesaplik:hesaplik@localhost:5432/hesaplik"
    log_level: str = "info"
    # Vite dev sunucusu. Uretimde ayni origin oldugu icin bos birakilabilir.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    restic_repository: str = "./data/backups"
    restic_password: str | None = None

    # Token yoksa bot başlamaz ama API çalışmaya devam eder.
    telegram_bot_token: str | None = None
    telegram_admin_ids: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_admin_ids_list(self) -> list[int]:
        return [int(x.strip()) for x in self.telegram_admin_ids.split(",") if x.strip()]


settings = Settings()
