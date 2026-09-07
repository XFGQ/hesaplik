# Hesaplık

Türkiye ve Bosna'da esnaf ve tüccar için **sesli ve yazılı komutla çalışan
cari hesap (veresiye defteri) sistemi.** Kağıt deftere yazılan borç, tahsilat
ve mal hareketleri; Telegram'dan ya da web arayüzünden doğal cümleyle
("ahmete 30 balya saman verdim 5000 tl") kaydedilir — form doldurmadan,
kutu işaretlemeden. Sistem anlamadığında varsaymaz, sorar.

## Özellikler

**Cari hesap defteri**
- Borç (DEBIT) ve tahsilat (CREDIT) kaydı — append-only; düzeltme ters
  kayıt veya arşivleme ile yapılır.
- Ürün kalemli hareket: adet + birim + tutar. Birim fiyat tutardan türetilir.
- **Para ve mal iki ayrı hesap:** bir kişi hem TL bazında alacaklı hem mal
  bazında borçlu olabilir; biri diğerini sıfırlamaz.
- Koşan bakiye: hiçbir zaman kolonda tutulmaz, onaylı hareketlerin toplamıdır.
- Kişi kartı (ad, telefon, il, ilçe, adres, not), ilçeye göre filtre,
  sütunlu hareket tablosu, satır bazında düzeltme/silme.
- Silme = arşivleme (`archived_transactions`, `archived_persons`); borçlu
  kişi silinemez.

**Telegram botu**
- Doğal dille borç/tahsilat kaydı, bakiye sorgusu, liste ve rapor komutları.
- **Sesli mesaj:** ses kaydı → Groq Whisper (Türkçe) → metin → yazılı
  mesajla aynı akış.
- Adım adım kişi ekleme/düzenleme, yazarak onaylı silme, tek mesajda birden
  çok işlem (kalıcı istek kuyruğu).
- Her gelen mesaj işlenmeden önce `raw_messages`'a yazılır — mesaj kaybolmaz,
  aynı mesaj iki kez işlenmez.

**Web uygulaması**
- Tek hesap + JWT girişi (bcrypt hash).
- React + TypeScript + Vite (PWA); koyu tema esas, açık tema seçeneği.
- Mobil uyumlu: 720px altında hamburger çekmece panel, sabit alt eylem barı,
  48px alt sınırlı dokunma hedefleri.
- Akıllı borç/tahsilat formu: tek "ürün ve adet" alanı ("20 kg arpa",
  "arpa 20 kilo", "20kg arpa"), koşan format ("70-20-50" = 70 vardı,
  20 değişti, 50 oldu).

**Web sohbet asistanı**
- Sağ alttan açılan panel; Telegram botuyla **aynı** anlama akışını kullanır.
- **Sesli mesaj:** WhatsApp tarzı kayıt çubuğu (canlı ses dalgası, süre,
  iptal/gönder) → `POST /api/chat/voice` → Groq STT → aynı metin akışı.
- Belirsizlikte butonlu teyit akışı, mesaj düzenleme, süren isteği durdurma.

**LLM altyapısı — dört katman**
- Öncelik sırasıyla: **NVIDIA NIM** (bulut) → **vLLM** (GPU) → **Ollama**
  (yedek) → **regex kural parser** (her zaman açık taban).
- Katman seçimi koddan değil configten/DB'den belirlenir; sağlıksız katman
  atlanır. Hiçbir sağlayıcı erişilemezse sistem çökmez, regex + elle giriş
  ile devam eder.
- LLM'in ürettiği her alan kod tarafından doğrulanır (isim, kişi, ürün, tutar).

**Admin paneli (`/admin`)**
- İşlem akışı (müşteri ne dedi → sistem ne algıladı → ne yaptı), sistem
  sağlığı, LLM izleme/yönetimi, yedekleme, istek kuyruğu, denetim logları,
  salt okunur veri gezgini + arşiv.

**PDF raporlama**
- Günlük rapor, genel durum, kişi ekstresi (yürüyen bakiye sütunlu).
- Ortak şablon (reportlab + DejaVu), Türkçe para biçimi; hem web'den indirilir
  hem Telegram'a dosya olarak gönderilir.

## Tasarım İlkeleri

1. **Defter append-only.** `transactions` üzerinde UPDATE/DELETE veritabanı
   tetikleyicisiyle yasaklıdır. Düzeltme, karşıt kayıt (`reverses_id`) ile yapılır.
2. **Para matematiği yalnızca `app/services/ledger.py` içinde ve `Decimal` ile.**
   float kullanımı yasak.
3. **Bakiye kolonda tutulmaz**, onaylı hareketlerin toplamıdır.
4. **Yapay zekâ hesap yapmaz.** Yalnızca niyet çıkarır; tutar, eşleştirme ve
   kayıt bu servisin işidir.

## Teknoloji Yığını

| Katman | Teknoloji |
|---|---|
| Backend | FastAPI + PostgreSQL 16 + SQLAlchemy 2 (async, asyncpg) |
| Frontend | React 19 + TypeScript + Vite (PWA) |
| Bot | python-telegram-bot |
| Orkestrasyon | Docker Compose |
| LLM | NVIDIA NIM / vLLM / Ollama (hepsi opsiyonel) |
| STT | Groq Whisper (`whisper-large-v3`) |
| Rapor | reportlab |

## Gereksinimler

| Araç | Sürüm / not |
|---|---|
| Python | 3.13 (`just setup` `/usr/bin/python3.13` kullanır; `pyproject` en az 3.12 ister) |
| Node.js | 20+ |
| Docker + Compose | PostgreSQL 16 container'ı için |
| `just` | görev koşucusu — `sudo dnf install -y just` |
| restic | opsiyonel, yalnızca yedekleme komutları için |

Fedora'da tek satır:

    sudo dnf install -y python3.13 nodejs docker docker-compose-plugin just restic

## Hızlı Başlangıç

    git clone <repo-url>
    cd hesaplik
    just quickstart
    # .env'i doldur, sonra:
    just dev

`just quickstart` idempotenttir: `.env` varsa dokunmaz, bağımlılıkları kurar,
veritabanını başlatır ve migration'ları uygular.

## Kurulum — Adım Adım

**1. Depoyu klonla**

    git clone <repo-url>
    cd hesaplik

**2. `.env` oluştur**

    cp .env.example .env

Zorunlu alanlar:

| Değişken | Açıklama |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Postgres container'ı |
| `DATABASE_URL` | `postgresql+asyncpg://kullanici:parola@localhost:5432/hesaplik` |
| `TEST_DSN` | testlerin kullandığı ayrı veritabanı (`hesaplik_test`) |
| `AUTH_USERNAME` | giriş kullanıcı adı |
| `AUTH_PASSWORD_HASH` | düz metin **değil**, bcrypt hash |
| `JWT_SECRET` | uzun ve rastgele |

Hash ve secret üretimi:

    .venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'sifreniz', bcrypt.gensalt()).decode())"
    .venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"

> `AUTH_USERNAME`, `AUTH_PASSWORD_HASH` ve `JWT_SECRET` üçü de boşsa sistem
> **fail-closed** çalışır: giriş reddedilir, tüm uçlar 401 döner.

> **Uyarı (yalnızca docker compose ile deploy):** bcrypt hash'i `$` içerir.
> Compose `.env`i kendi değişken ikamesi için tarar — compose'a giden `.env`de
> her `$` işaretini `$$` yapın. `just dev` / `just api` buna gerek duymaz.

Opsiyonel anahtarlar (yoksa sistem regex + elle girişe düşer, çalışmaya
devam eder): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `NVIDIA_API_KEY`,
`VLLM_URL`, `OLLAMA_URL`, `GROQ_API_KEY`, `RESTIC_REPOSITORY`.

**3. Bağımlılıkları kur**

    just setup      # venv + pip install -e ".[dev]" + npm install

**4. Veritabanını hazırla**

    just db         # PostgreSQL 16 container'ı (127.0.0.1:5432)
    just migrate    # db/migrations/*.sql dosyalarını sırayla uygular

**5. Çalıştır**

    just dev        # api :8000 + web :5173 tek terminalde
    just seed       # örnek ürünler (saman, arpa)

## Portlar

| Servis | Port | Not |
|---|---|---|
| API (FastAPI) | 8000 | geliştirmede `just api` / `just dev` |
| Web dev sunucusu (Vite) | 5173 | `/api` isteklerini 8000'e proxy'ler |
| PostgreSQL | 5432 | yalnızca `127.0.0.1`'e bağlanır |
| Ollama | 11434 | varsa; üretimde yalnızca compose ağında |

Üretimde **tek port**: derlenmiş `web/dist` aynı FastAPI sürecinden servis
edilir, container `127.0.0.1:8100`e açılır ve önündeki Nginx/Caddy TLS
sonlandırıp buraya proxy'ler.

## Docker / Konteynerler

**Geliştirme —** `docker-compose.yml`. Varsayılan olarak yalnızca `db`
servisi çalışır (`just db`). `server` ve `gpu` profilleri altında api/caddy
ve vLLM tanımları da bulunur.

**Üretim —** `docker-compose.prod.yml`, dört servis:

| Servis | İş |
|---|---|
| `db` | PostgreSQL 16, named volume (`pgdata`), dışarı port açmaz |
| `api` | FastAPI + derlenmiş `web/dist` — tek port |
| `bot` | Aynı imaj, `python -m app.bot.main` |
| `ollama` | Yerel LLM yedeği (`ollama_data` volume) |

    docker compose --env-file .env -f docker-compose.prod.yml up -d --build

`Dockerfile` çok aşamalıdır: node build → restic ikili → python runtime.

## Veritabanı

PostgreSQL 16. `db/schema.sql` **kanoniktir** — tüm tablolar, tetikleyiciler
ve indeksler oradadır. Taze bir volume'de compose bu dosyayı container'ın
`/docker-entrypoint-initdb.d` dizinine bağlar, şema kendiliğinden kurulur.

Mevcut bir veritabanını yeni sürüme taşımak için `db/migrations/*.sql`:

    just migrate    # dosyaları sırayla uygular

Migration'ların hepsi idempotenttir (`IF NOT EXISTS`, `ON CONFLICT DO
NOTHING`), bu yüzden taze kurulumda çalıştırmak da güvenlidir.

Her şeyi silip sıfırdan başlamak (**tüm veri gider**):

    just reset-db

## Komutlar

| Komut | İş |
|---|---|
| `just quickstart` | yeni makinede sıfırdan kurulum (.env + venv + npm + db + migration) |
| `just setup` | venv, Python bağımlılıkları, `npm install` |
| `just db` | PostgreSQL container'ını başlat |
| `just migrate` | `db/migrations/*.sql` dosyalarını sırayla uygula |
| `just reset-db` | veritabanını sıfırla (**tüm veri silinir**) |
| `just dev` | api :8000 + web :5173 tek terminalde (Ctrl+C ikisini kapatır) |
| `just api` | yalnızca backend (`--reload-dir app`) |
| `just web` | yalnızca frontend |
| `just web-host` | frontend'i ağa aç (telefondan test için) |
| `just bot` | Telegram botu (yerelde uzun yoklama) |
| `just seed` | örnek ürünler (saman, arpa) |
| `just test` | backend testleri (docker db'yi otomatik ayağa kaldırır) |
| `just web-test` | arayüz testleri (`node --test`, ek bağımlılık yok) |
| `just lint` | `ruff check` + `tsc -b --noEmit` |
| `just backup` | restic ile yedek al |
| `just backup-list` | depodaki yedekleri listele |
| `just restore-test` | en son yedeği geçici DB'ye açıp doğrula |

## Testler

    just test       # backend  (pytest)
    just web-test   # frontend (node --test)

Backend testleri `TEST_DSN`'deki ayrı veritabanını (`hesaplik_test`)
kullanır; `db/init-test-db.sh` bu veritabanını ilk açılışta oluşturur.
Arayüz testleri `web/src/lib/` altındaki DOM'suz saf mantığı doğrular ve
ek bir npm bağımlılığı gerektirmez.

## Dağıtım

Deploy otomatiktir: `main` branch'ine push → GitHub Actions
(`.github/workflows/deploy.yml`) → SSH ile sunucu → `git pull` →
`docker compose --env-file .env -f docker-compose.prod.yml up -d --build` →
`docker image prune -f` → `GET /api/health` ile sağlık kontrolü.

Gereken GitHub secret'ları: `SERVER_HOST`, `SERVER_USER`, `SSH_PRIVATE_KEY`.
`feat/*` branch'leri deploy etmez.

Yedekleme, geri yükleme ve vLLM kontrol timer'ları host'ta systemd ile
çalışır — `deployment/` altındaki `.service`/`.timer` dosyaları ve
`deployment/README.md`.

## Lisans

© 2026 Furkan Duman. Tüm hakları saklıdır.

Bu depodaki kaynak kod, veritabanı şeması, arayüz tasarımı ve dokümantasyon
Furkan Duman'a aittir. İzinsiz kopyalanamaz, dağıtılamaz, ticari olarak
kullanılamaz.
