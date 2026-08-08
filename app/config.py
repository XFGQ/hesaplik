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
    # parser asla kaldırılmaz. Hangi kaynağın (vLLM/Ollama/none)
    # kullanılacağı artık bu alandan DEĞİL, DB'deki settings.llm_primary'den
    # (runtime, admin panelden değiştirilebilir) belirlenir — bkz.
    # app/services/llm_provider.py > get_active_provider ve CLAUDE.md >
    # "Dinamik LLM geçişi". Bu alan yalnızca geriye dönük uyumluluk için
    # durur, seçim mantığında okunmaz.
    llm_provider: str = "none"
    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b"
    # İşlemcide model ~9 sn/cümle sürüyor, ama Ollama modeli boştayken
    # bellekten düşürüyor (varsayılan keep_alive ~5 dk) ve ilk istek modeli
    # yeniden yüklemek zorunda kalıyor — soğuk başlangıçta 60sn+ ölçüldü
    # (CLAUDE.md > "Bot yazıyor... göstergesi"). Bot başlangıcında ısınma
    # çağrısı bunu büyük ölçüde önlüyor, ama pay bırakmak için 90 sn.
    llm_timeout: float = 90.0

    # vLLM (Bosna, 2080 Super, WireGuard tüneli üzerinden). BİRİNCİL kaynak
    # — boşsa (varsayılan) vLLM hiç denenmez, sistem Ollama/none'a düşer.
    # OpenAI-uyumlu API: {vllm_url}/v1/chat/completions.
    vllm_url: str = ""
    vllm_model: str = "Qwen/Qwen2.5-7B-Instruct-AWQ"
    vllm_timeout: float = 30.0
    # Sağlık kontrolü (get_active_provider "auto"/"vllm" modunda) kısa
    # tutulur ki her mesajda uzun timeout beklenmesin; sonucu 10 sn
    # önbelleklenir (bkz. llm_provider._cached_health).
    llm_health_timeout: float = 3.0

    # Admin panel (/admin) şifresi. Boşsa panel tamamen kapalıdır (503) —
    # yanlışlıkla açık admin uç noktası kalmasın diye varsayılan boş.
    admin_password: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_admin_ids_list(self) -> list[int]:
        return [int(x.strip()) for x in self.telegram_admin_ids.split(",") if x.strip()]


settings = Settings()
