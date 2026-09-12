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

    # Tek hesap, JWT tabanlı giriş. Giriş yapan HER ŞEYE erişir (defter +
    # admin) — ayrı roller yok. Üçü de BOŞSA sistem tamamen kapalıdır:
    # /api/auth/login her zaman 503 döner, require_auth her zaman 401 —
    # yapılandırılmamış bir kurulum kazara açık kalmaz. Şifre düz metin
    # DEĞİL, bcrypt hash olarak saklanır (AUTH_PASSWORD_HASH). Hash üretmek
    # için: .venv/bin/python -c "import bcrypt;
    # print(bcrypt.hashpw(b'sifreniz', bcrypt.gensalt()).decode())"
    auth_username: str = ""
    auth_password_hash: str = ""
    # JWT imza anahtarı. Üretimde uzun/rastgele olsun: .venv/bin/python -c
    # "import secrets; print(secrets.token_urlsafe(48))". Değiştirilince
    # tüm açık oturumlar kendiliğinden geçersiz olur.
    jwt_secret: str = ""
    # Token ömrü (saat). Süre dolunca yeniden giriş istenir.
    jwt_expire_hours: int = 24

    restic_repository: str = "./data/backups"
    restic_password: str | None = None

    # Token yoksa bot başlamaz ama API çalışmaya devam eder.
    telegram_bot_token: str | None = None
    telegram_admin_ids: str = ""
    # Tüm veritabanı yedeğinin gönderildiği KİŞİSEL chat id (tek sayı): host'taki
    # scripts/telegram-yedek.sh, bot /yedek ve panel "Şimdi Yedek Al" buraya
    # gönderir; /yedek komutunu da YALNIZCA bu chat kullanabilir.
    telegram_admin_chat_id: str = ""

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

    # NVIDIA NIM (bulut, Faz 4c) — DÖRDÜNCÜ ve EN ÖNCELİKLİ katman. OpenAI
    # uyumlu API: {nvidia_url}/chat/completions, Authorization: Bearer
    # {nvidia_api_key}. api_key BOŞSA (varsayılan) NVIDIA hiç denenmez,
    # sistem vLLM/Ollama/none'a düşer (bkz. app/services/llm_provider.py >
    # select_source). api_key GİZLİDİR, hiçbir yerde loglanmaz.
    #
    # Model: Qwen2.5, Llama'nın aksine Türkçe'yi resmi olarak desteklenen
    # diller arasında listeliyor (29 dil) — bu yüzden qwen/qwen2.5-72b-
    # instruct varsayılan seçildi (mevcut vLLM/Ollama katmanlarıyla da aynı
    # aile, tutarlı davranış). NVIDIA rate limit'i (40 istek/dk) 429 ile
    # kendini gösterir; NVIDIAProvider bunu None döner ve auto modda bir
    # sonraki health check'te vLLM'e düşülür (bkz. nvidia_healthy).
    nvidia_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_api_key: str = ""
    nvidia_model: str = "openai/gpt-oss-20b"
    nvidia_timeout: float = 15.0

    # Groq STT (Faz 5 — sesli komut). OpenAI-uyumlu /audio/transcriptions
    # ucu: {groq_stt_url}/audio/transcriptions, Authorization: Bearer
    # {groq_api_key}. api_key BOŞSA (varsayılan) sesli mesaj desteği
    # tamamen kapalıdır — bot kullanıcıya yazmasını ister, ÇÖKMEZ (bkz.
    # app/services/stt.py). api_key GİZLİDİR, hiçbir yerde loglanmaz.
    groq_api_key: str = ""
    groq_stt_url: str = "https://api.groq.com/openai/v1"
    groq_stt_model: str = "whisper-large-v3"
    groq_stt_timeout: float = 30.0

    # vLLM uzaktan aç/kapat ("Yol B", bkz. app/services/vllm_control.py).
    # Bosna'daki host script'i (scripts/vllm-control.sh) GET /api/vllm-desired
    # ucunu bu tokenla çeker — admin şifresinden AYRI ve daha dar yetkili
    # (yalnızca bu tek uca erişir). BOŞ ise uç HER ZAMAN 401 döner (fail
    # closed) — yapılandırılmamış bir kurulumda kazara açık kalmaz.
    vllm_control_token: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_admin_ids_list(self) -> list[int]:
        return [int(x.strip()) for x in self.telegram_admin_ids.split(",") if x.strip()]

    @property
    def telegram_admin_chat_id_int(self) -> int | None:
        """Boş ya da sayı değilse None — yedek hiçbir yere gönderilmez,
        /yedek kimseye çalışmaz (yanlış yapılandırmada açık kalmasın)."""
        deger = self.telegram_admin_chat_id.strip()
        try:
            return int(deger) if deger else None
        except ValueError:
            return None


settings = Settings()
