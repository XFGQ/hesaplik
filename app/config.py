from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://hesaplik:hesaplik@localhost:5432/hesaplik"
    log_level: str = "info"
    # Vite dev sunucusu. Uretimde ayni origin oldugu icin bos birakilabilir.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Uretimde web ve API tek porttan servis edilir: FastAPI, `npm run build`
    # ciktisini (web/dist) statik olarak sunar. Goreli yol proje kokune gore
    # cozulur. Dizin yoksa (gelistirmede build alinmamissa) yalnizca API calisir.
    web_dist: str = "web/dist"

    # Admin paneli (/admin) tek şifreyle korunur. BOŞ ise panel tamamen
    # kapalıdır: giriş denemesi de /api/admin/* uçları da reddedilir —
    # yapılandırılmamış bir kurulumda panel kazara açık kalmaz.
    admin_password: str = ""
    # Admin oturumunun ömrü (saat). Süre dolunca yeniden şifre istenir.
    admin_session_hours: int = 12

    restic_repository: str = "./data/backups"
    restic_password: str | None = None

    # Token yoksa bot başlamaz ama API çalışmaya devam eder.
    telegram_bot_token: str | None = None
    telegram_admin_ids: str = ""

    # LLM fallback (Faz 4). Kural parser çözemezse devreye girer, kural
    # parser asla kaldırılmaz. "none" ise LLM hiç çağrılmaz — Ollama kurulu
    # olmasa/erişilemese de sistem çökmeden kural parser + "elle gir" ile
    # çalışmaya devam eder.
    llm_provider: str = "none"
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b"
    # İşlemcide model ~9 sn/cümle sürüyor, ama Ollama modeli boştayken
    # bellekten düşürüyor (varsayılan keep_alive ~5 dk) ve ilk istek modeli
    # yeniden yüklemek zorunda kalıyor — soğuk başlangıçta 60sn+ ölçüldü
    # (CLAUDE.md > "Bot yazıyor... göstergesi"). Bot başlangıcında ısınma
    # çağrısı bunu büyük ölçüde önlüyor, ama pay bırakmak için 90 sn.
    # GPU (vLLM) gelince düşürülür.
    llm_timeout: float = 90.0

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_admin_ids_list(self) -> list[int]:
        return [int(x.strip()) for x in self.telegram_admin_ids.split(",") if x.strip()]


settings = Settings()
