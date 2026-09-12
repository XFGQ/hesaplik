# Hesaplık — proje hafızası

## Hesaplık Nedir

Hesaplık, Türkiye ve Bosna'da esnaf ve tüccarın tuttuğu **cari hesap
(veresiye) defterinin** dijital karşılığıdır. Kimin ne kadar borcu var, kim
ne kadar mal aldı, kim peşin ödeyip malını bekliyor — bunların hepsi kağıt
defterde tutuluyordu: sayfa yıpranır, rakam silinir, "geçen ay ne yazmıştık"
tartışması çıkar.

**Çözdüğü sorun:** kayıt girmek için ekran doldurmak, form açmak, kutu
işaretlemek gerekmiyor. Kullanıcı ne diyorsa onu yazıyor ya da söylüyor —
Telegram'dan ("ahmete 30 balya saman verdim 5000 tl") ya da web
uygulamasının sohbet panelinden, isterse mikrofona konuşarak. Sistem cümleyi
çözer, kişiyi eşleştirir, deftere işler ve onaylatır. Anlamadığında
varsaymaz, sorar.

**Kimin için:** tarladan/depodan mal satan, müşterisiyle vadeli çalışan,
elinde onlarca cari hesap olan esnaf. Hedef kullanıcı teknoloji uzmanı
değil; arayüz büyük dokunma hedefleriyle, sade dille ve tek elle
kullanılacak şekilde tasarlanır.

**Neden güvenilir:** defter append-only'dir (yazılan kayıt değişmez,
silinmez — düzeltme ters kayıtla ya da arşivlemeyle olur), para matematiği
tek bir serviste ve `Decimal` ile yapılır, para hesabı ile mal hesabı ayrı
tutulur, kişi eşleştirmesinde en ufak şüphede otomatik seçim yapılmaz. Bu
kurallar aşağıda "Kesinlikle Bozulmaması Gereken Kurallar" başlığında
kayıtlıdır ve pazarlık konusu değildir.

**Özgün proje özeti** (Faz 0'da yazıldı, olduğu gibi korunuyor — Bosna'daki
GPU/vLLM katmanı bu satırdan sonra bağlandı, bkz. "LLM Altyapısı"):

> Sesli/yazılı komutla çalışan cari hesap takip sistemi. Backend FastAPI +
> PostgreSQL, frontend React + TS + Vite (PWA). İki sunucu: İzmir (7/24,
> kaynak doğruluk) ve Bosna (GPU, LLM/Whisper — henüz bağlanmadı).

## Telif Hakkı

© 2026 Furkan Duman. Tüm hakları saklıdır.

Bu depodaki kaynak kod, veritabanı şeması, arayüz tasarımı ve dokümantasyon
Furkan Duman'a aittir. İzinsiz kopyalanamaz, dağıtılamaz, ticari olarak
kullanılamaz.

## Özellikler

Bugüne kadar tamamlanan işlerin tam listesi. Her başlığın ayrıntısı ve
arkasındaki karar gerekçesi ilgili bölümde yazılıdır.

### Cari Hesap Defteri

- Borç (DEBIT) ve tahsilat (CREDIT) kaydı; her kayıt append-only, düzeltme
  ters kayıt ya da arşivleme ile.
- **Para ve mal iki ayrı hesap.** Bir kişi hem TL bazında alacaklı hem mal
  bazında borçlu olabilir; biri diğerini sıfırlamaz.
- Ürün kalemli kayıt: adet + birim + tutar. Birim fiyat tutardan türetilir,
  fiyat listesi tutarı bağlamaz — tek istisna saman (aşağıda).
- **Varsayılan saman fiyatı:** sol paneldeki "Güncel saman fiyatı" kartından
  ayarlanır (`settings.saman_birim_fiyat`, değişiklik audit_log'a). Saman
  için tutar yazılmazsa adet × bu fiyat; yazılırsa kullanıcınınki. Belirsiz
  cümle ("furkan 20") kayıttan önce sorulur. Diğer ürünler etkilenmez.
- Bakiye hiçbir zaman kolonda tutulmaz, onaylı hareketlerin `SUM`'ıdır.
- Kişi kartı: ad soyad, telefon, il, ilçe, adres, not. İlçeye göre filtre.
- Kişi defteri: tarih / ürün / adet / birim fiyat / tutar sütunlu hareket
  tablosu, satır başına "Düzelt" ve "Sil".
- Silme = arşivleme. `archived_transactions` ve `archived_persons` her şeyi
  kim/ne zaman bilgisiyle saklar; borçlu kişi ayrıca silinemez (422).
- Ürün kataloğu serbest metinle çalışır: Türkçe "İ/I" normalizasyonu, alias,
  pg_trgm fuzzy **öneri** (otomatik bağlama yok).

### Telegram Botu

- Doğal dille borç/tahsilat kaydı, bakiye sorgusu, liste ve rapor komutları
  — regex birincil, LLM son çare.
- **Sesli mesaj:** Telegram ses kaydı → Groq `whisper-large-v3` (Türkçe) →
  metin → yazılı mesajla aynı akış.
- Her gelen mesaj işlenmeden ÖNCE `raw_messages`'a yazılır; `update_id`
  tekil indeksi aynı mesajın iki kez işlenmesini engeller.
- Kişi eşleştirme güvenliği: birebir eşleşme ya da belirgin tek aday yoksa
  "hangisi?" diye sorar, asla varsaymaz.
- Adım adım yeni kişi ekleme (telefon/il/ilçe, her adımda "Geç").
- Kişi düzenleme (ad, telefon, il, ilçe, adres, not) — eski değeri göstererek.
- Kişi silme = arşivleme; yazarak onay (işletme adının ilk kelimesi).
- Tek mesajda birden çok işlem: kalıcı `pending_requests` kuyruğu ile sırayla.
- Uzun süren işlemlerde "yazıyor..." göstergesi; admin komutları
  (`/engine`, `/queue`, `/logs`) yalnızca `TELEGRAM_ADMIN_IDS` için.
- **"/" komut menüsü:** `/borc`, `/tahsilat`, `/kisiekle` adım adım sorar;
  `/bakiye`, `/kisi`, `/koy` tek adımda çalışır; `/yedek` (yalnızca yönetici)
  veritabanını Telegram'a gönderir. Hepsi mevcut akışları tetikler.

### Web Uygulaması

- Tek hesap + JWT girişi (bcrypt hash; üçü de boşsa sistem fail-closed).
- React + TS + Vite; üretimde aynı FastAPI portundan servis edilir.
- Koyu tema esas, açık tema seçeneği (`data-theme`, tercih localStorage'ta).
- Mobil düzen: 720px altında hamburger çekmece panel, sabit alt eylem barı,
  HCI alt sınırı 48px dokunma hedefleri.
- **Akıllı borç/tahsilat formu:** tek "Ürün ve adet" alanı — "20", "20 kg
  arpa", "arpa 20 kilo", "20kg arpa" hepsi anlaşılır; birim yazıdan algılanır.
- **Koşan format:** "70-20-50" = 70 vardı, 20 değişti, 50 oldu. Yön ilk/son
  karşılaştırmasından çıkar, matematik tutmuyorsa kayıt kilitlenir.
- Kayıtlı birim fiyattan otomatik tutar hesabı (varsayılan KAPALI; samanda
  varsayılan saman fiyatıyla AÇIK başlar).
- Toast bildirimleri, modal akışları, yazarak silme onayı — hepsi kendi
  bileşenlerimiz, harici kütüphane yok.

### Web Sohbet Asistanı

- Sağ alttan açılan panel; Telegram botuyla AYNI anlama akışını kullanır.
- **Sesli mesaj:** WhatsApp tarzı kayıt çubuğu — mikrofon, canlı ses dalgası,
  süre, iptal/gönder; `POST /api/chat/voice` → Groq STT → aynı metin akışı.
- Teyit akışı: belirsizlikte buton ("hangisi?", "bunu mu demek istediniz?").
- Mesaj düzenleme (kalem): balon ve sonrasındaki dal silinir, yeniden gönderilir.
- Durdurma (⏹): süren istek `AbortController` ile kesilir,
  `POST /api/chat/cancel` sunucudaki bekleyen soruyu ve kuyruğu düşürür.
- Panel dışına tıklayınca küçülür (geçmiş korunur), kapalıyken periyodik
  hatırlatma animasyonu.

### LLM Altyapısı

- **Dört katman, öncelik sırasıyla:** NVIDIA NIM (bulut) → vLLM (Bosna, GPU)
  → Ollama (İzmir, yedek) → regex kural parser (her zaman açık taban).
- Katmanlar `LLMProvider` Protocol'ü arkasında; hangisinin kullanılacağı
  koddan değil configten/DB'den belirlenir, `auto` modda sağlıksız katman
  atlanır (429 rate limit → bir alt katman).
- LLM asla son sözü söylemez: çıktısındaki her alan kod tarafından doğrulanır
  (isim halüsinasyonu kontrolü, kişi pg_trgm, ürün catalog, tutar sayı mı).
- Türkçe ek temizleme LLM'e bırakılmaz, kodda yapılır (`name_utils`).
- Panelden vLLM aç/kapat ("Yol B"): panel DB'ye tercih yazar, Bosna'daki
  `scripts/vllm-control.sh` `GET /api/vllm-desired` ile çeker ve uygular.
- Hiçbir sağlayıcı erişilemezse sistem ÇÖKMEZ, regex + elle giriş ile devam eder.

### Admin Paneli (`/admin`)

- **İşlem Akışı:** müşteri ne dedi → sistem ne algıladı (regex mi LLM mi,
  kaç ms) → ne yaptı. Filtre + sayfalama.
- **Sistem Sağlığı:** veritabanı, LLM, Telegram botu (dolaylı ölçüm),
  yedekleme, API — beş bileşen, 30 sn'de bir kendiliğinden yenilenir.
- **LLM İzleme/Yönetimi:** çağrılar, süreler, başarı oranı; sağlayıcı seçimi
  ve vLLM aç/kapat.
- **Yedekleme:** restic snapshot listesi, "şimdi yedekle", "ana veri yap"
  (geri yükleme isteği — panel İSTER, host UYGULAR).
- **İstek Kuyruğu:** `pending_requests` durumları.
- **Loglar:** `audit_log` denetim kaydı.
- **Kişiler & İşlemler:** salt okunur veri gezgini + arşiv.

### Raporlama (PDF)

- **Günlük rapor:** o günün hareketleri, özet kutuları (kayıt sayısı, toplam
  borç, toplam tahsilat, net).
- **Genel durum:** tüm kişiler, borçlu çoktan aza, ilçe ve açık kalem sütunlu.
- **Kişi ekstresi:** bir kişinin tüm hareketleri + yürüyen bakiye sütunu.
- Ortak şablon (reportlab + DejaVu), Türkçe para biçimi; hem web'den indirilir
  hem Telegram'a dosya olarak gönderilir.

## Mimari

**Backend:** FastAPI + PostgreSQL 16 (SQLAlchemy 2 async + asyncpg). Para
mantığı `app/services/ledger.py`, anlama `parser.py` → `llm_provider.py` →
`intent_resolver.py` → `message_processor.py` hattında.

**Frontend:** React 19 + TypeScript + Vite (PWA). Geliştirmede Vite dev
sunucusu 5173'te, `/api`yi 8000'e proxy'ler. Üretimde `web/dist` aynı
FastAPI sürecinden servis edilir — **tek port**, tek imaj (`Dockerfile`
çok aşamalı: node build → restic ikili → python runtime).

**Telegram botu ayrı servis:** aynı imaj, `python -m app.bot.main`
komutuyla. Yerelde long polling, üretimde webhook. API çökse bot, bot çökse
API ayakta kalır; ikisi de aynı veritabanına yazar.

**Orkestrasyon:** Docker Compose. Yerelde yalnızca `db` (bkz. `just db`),
üretimde `docker-compose.prod.yml` ile `db + api + bot + ollama`.

**Şema:** `db/schema.sql` kanoniktir (tüm tablolar, tetikleyiciler,
indeksler); `db/migrations/*.sql` yalnızca MEVCUT bir veritabanını yeni
sürüme taşır.

### Mimari kararlar

- Bakiye asla kolonda tutulmaz, `SUM` ile hesaplanır (`ledger.balance_of`).
- `raw_messages` tablosu dokunulmaz kayıt: her gelen mesaj (Telegram/web)
  işlenmeden önce buraya yazılır. LLM yanlış anlasa bile orijinal metin
  kaybolmaz.
- LLM/STT provider'ları `LLMProvider`/`STTProvider` Protocol arkasında
  soyutlanacak (Faz 4-5, henüz yazılmadı). Router `inference.yml`'dan okur,
  Bosna/İzmir hangisi kullanılacak koddan değil configten belirlenir.
- Yerel geliştirmede Postgres Docker'da (`docker compose up -d db`),
  named volume kullanılıyor (bind mount SELinux/Fedora'da izin sorunu
  çıkardı).

## Kesinlikle Bozulmaması Gereken Kurallar

1. **`transactions` append-only.** DB tetikleyicisi UPDATE/DELETE'i reddeder.
   Düzeltme = ters kayıt (`ledger.reverse()`), asla direkt değişiklik değil.
2. **Para matematiği yalnızca `app/services/ledger.py` içinde, `Decimal` ile.**
   float asla kullanılmaz.
3. **Para ve mal iki ayrı hesaptır, birbirini ezmez.** Örnek: kişi 30 balya
   saman aldı (borç), sonra 50 balyanın parasını verdi (tahsilat + kalem).
   Sonuç: para −1.500 TL (ona borçlusun), mal −20 balya ("20 balya saman
   alacaklı" — o senden alacaklı, çünkü peşin ödedi). Bunlar birbirinden
   bağımsız, biri diğerini sıfırlamaz. Kalem işareti: DEBIT +qty (kişi mal
   aldı, borçlu), CREDIT −qty (kişi parasını verdi, malı bekliyor).
4. **Tutarı kullanıcı yazar, fiyat listesi bağlamaz.** Pazarlık gerçeği.
   Birim fiyat tutardan türetilir (`line_total / qty`), tersi değil.
   **Tek istisna: saman.** Tutar YAZILMAMIŞSA ve ürün saman, birim balya ise
   tutar = adet × varsayılan saman fiyatı (`settings.saman_birim_fiyat`,
   arayüzden ayarlanır). Tutar yazılmışsa her zaman kullanıcınınki geçerlidir.
   Saman dışındaki hiçbir ürünün fiyatı (price_history dahil) tutarı
   belirlemez. Ayrıntı: "Varsayılan saman fiyatı".
5. **Ürün serbest metindir.** `app/services/catalog.py` eşleştirir
   (büyük/küçük harf + Türkçe "İ/I" normalize + alias). Bulamazsa yeni ürün
   açar. Fuzzy eşleştirme KASTEN otomatik değil — yanlış ürüne sessizce
   bağlamak daha tehlikeli, öneri sunulur (`suggest_products`), otomatik
   bağlanmaz.
6. **Borçlu kişi silinemez.** `DELETE /persons/{id}` bakiye ≠ 0 ise 422 döner.

## Kurulum — Geliştirme Ortamı

### Gereksinimler

| Araç | Sürüm / not |
|---|---|
| Python | 3.13 (`just setup` `/usr/bin/python3.13` kullanır; `pyproject` en az 3.12 ister) |
| Node.js | 20+ (üretim imajı Node 22 ile derler) |
| Docker + Compose | Postgres 16 container'ı için |
| `just` | görev koşucusu — `sudo dnf install -y just` |
| restic | opsiyonel, yalnızca yedek komutları için |

Fedora'da tek satır:

    sudo dnf install -y python3.13 nodejs docker docker-compose-plugin just restic

### Repoyu Çekme

    git clone <repo-url>
    cd hesaplik

### Tek Komutla Kurulum

    just setup

Yaptıkları (`Justfile` > `setup`):

1. `/usr/bin/python3.13 -m venv .venv` — sanal ortam,
2. `.venv/bin/pip install -U pip`,
3. `.venv/bin/pip install -e ".[dev]"` — proje + test/lint bağımlılıkları
   (pytest, pytest-asyncio, ruff),
4. `cd web && npm install` — arayüz paketleri.

Sıfırdan bir makinede veritabanı ve `.env` de dahil tam kurulum için
aşağıdaki **`just quickstart`** bölümüne bakın.

### Ortam Değişkenleri

    cp .env.example .env

`.env.example` her alanın ne işe yaradığını satır satır anlatır. Özet:

**Zorunlu (yerelde bile):**

- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` — Postgres container'ı.
- `DATABASE_URL` — `postgresql+asyncpg://kullanici:parola@localhost:5432/hesaplik`.
- `TEST_DSN` — testlerin kullandığı ayrı veritabanı (`hesaplik_test`,
  `db/init-test-db.sh` ilk açılışta oluşturur).

**Giriş (üçü de boşsa sistem fail-closed: giriş reddedilir, tüm uçlar 401):**

- `AUTH_USERNAME`
- `AUTH_PASSWORD_HASH` — düz metin DEĞİL, bcrypt:

      .venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'sifreniz', bcrypt.gensalt()).decode())"

- `JWT_SECRET` — uzun/rastgele:

      .venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"

- `JWT_EXPIRE_HOURS` (varsayılan 24). `JWT_SECRET` değişince tüm oturumlar düşer.

> **Uyarı (yalnızca docker compose ile deploy):** bcrypt hash'i `$` içerir.
> Compose `.env`i kendi içinde değişken ikamesi için tarar — compose'a giden
> `.env`de her `$` işaretini `$$` yapın. `just dev`/`just api` (uvicorn `.env`i
> doğrudan okur) buna gerek duymaz.

**Opsiyonel — hiçbiri zorunlu değil, yoksa sistem regex + elle girişe düşer:**

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS` — bot; token yoksa bot başlamaz,
  API çalışmaya devam eder.
- `TELEGRAM_ADMIN_CHAT_ID` — Telegram'a yedeğin gideceği kişisel chat id
  (`@userinfobot`); boşsa Telegram yedeği gönderilmez.
- `LLM_PROVIDER` (`ollama` | `none`), `OLLAMA_URL`, `LLM_MODEL`, `LLM_TIMEOUT`.
- `NVIDIA_URL`, `NVIDIA_API_KEY`, `NVIDIA_MODEL` — bulut katmanı; key boşsa
  hiç denenmez.
- `VLLM_URL`, `VLLM_MODEL`, `VLLM_TIMEOUT`, `VLLM_CONTROL_TOKEN` — GPU katmanı
  ve panelden aç/kapat ucu.
- `GROQ_API_KEY`, `GROQ_STT_URL`, `GROQ_STT_MODEL`, `GROQ_STT_TIMEOUT` — sesli
  mesaj; key boşsa sesli mesaj kapalıdır, kullanıcıdan yazması istenir.
- `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `RESTIC_KEEP_*` — yedekleme.
- `DOMAIN`, `LOG_LEVEL`.

### Veritabanı

    just db          # docker compose up -d db  (Postgres 16, 127.0.0.1:5432)

**Taze bir volume'de şema kendiliğinden kurulur.** Compose, `db/schema.sql`
ve `db/init-test-db.sh` dosyalarını container'ın
`/docker-entrypoint-initdb.d` dizinine bağlar; Postgres ilk açılışta bunları
çalıştırır. `schema.sql` kanoniktir — persons, products, transactions,
transaction_lines, archived_*, raw_messages, audit_log, settings,
pending_requests, web_chat_pending, restore_requests ve append-only
tetikleyicisi dahil her şey içindedir. Yani sıfırdan kurulumda migration
çalıştırmak şart değildir.

**Mevcut bir veritabanını güncellemek için** `db/migrations/*.sql`:

    just migrate     # dosyaları sırayla uygular

Migration'ların hepsi idempotent yazılmıştır (`IF NOT EXISTS`,
`ON CONFLICT DO NOTHING`, `ADD VALUE IF NOT EXISTS`), bu yüzden taze
kurulumda çalıştırmak da güvenlidir — "zaten var, atlanıyor" uyarıları
verip geçer. Elle uygulamak isterseniz:

    docker compose exec -T db psql -U hesaplik -d hesaplik < db/migrations/012_web_voice_source.sql

Her şeyi silip sıfırdan başlamak (**TÜM VERİ GİDER**):

    just reset-db

### Çalıştırma

    just dev       # api :8000 + web :5173 tek terminalde (Ctrl+C ikisini kapatır)
    just api       # sadece backend (--reload-dir app)
    just web       # sadece frontend
    just web-host  # frontend'i ağa aç (telefondan test için)
    just bot       # Telegram botu (yerelde uzun yoklama)
    just seed      # örnek ürünler (saman, arpa)

    just test      # pytest (docker db'yi otomatik ayağa kaldırır)
    just web-test  # arayüz testleri (node --test, ek bağımlılık yok)
    just lint      # ruff + tsc --noEmit

    just backup       # restic ile yedek al
    just telegram-yedek            # DB'yi gzip'leyip admin'in Telegram'ına gönder
    just telegram-yedek --gonderme # aynısı, göndermeden (dök + doğrula)
    just backup-list  # depodaki yedekleri listele
    just restore-test # en son yedeği geçici DB'ye açıp doğrula

## Kurulum — Üretim (Sunucu)

Tüm servisler tek komutla ayağa kalkar:

    docker compose --env-file .env -f docker-compose.prod.yml up -d --build

`docker-compose.prod.yml` dört servis çalıştırır:

| Servis | İş |
|---|---|
| `db` | Postgres 16, named volume (`pgdata`), dışarı port AÇMAZ |
| `api` | FastAPI + derlenmiş `web/dist` — tek port, `127.0.0.1:8100` ve WireGuard adresi `10.100.0.1:8100` |
| `bot` | Aynı imaj, `python -m app.bot.main` |
| `ollama` | Yerel LLM yedeği (`ollama_data` volume) |

Önünde Nginx/Caddy TLS sonlandırır ve `8100`e proxy'ler
(`duman.rinnesoft.com.conf`, `Caddyfile`).

**Üretim `.env`i geliştirmedekiyle aynı yapıdadır**, farkı: bazı alanlar
compose tarafından `:?` ile ZORUNLU kılınmıştır — eksikse deploy başlamadan
durur (sistemin farkında olmadan kilitli ya da yanlış yapılandırmayla açık
kalması yerine):

- `POSTGRES_PASSWORD`
- `AUTH_USERNAME`, `AUTH_PASSWORD_HASH`, `JWT_SECRET` (api)
- `TELEGRAM_BOT_TOKEN` (bot)

Ayrıca üretime özgü: `RESTIC_REPOSITORY` (api'ye **salt okunur** bağlanır),
`RESTIC_PASSWORD`, `VLLM_CONTROL_TOKEN`, `NVIDIA_API_KEY`, `GROQ_API_KEY`.
`$` işaretlerini `$$` olarak escape etmeyi unutmayın (yukarıdaki uyarı).

**Deploy otomatiktir.** `main`e push → GitHub Actions
(`.github/workflows/deploy.yml`) → SSH → `git pull` →
`docker compose ... up -d --build` →
`docker image prune -f` → `GET /api/health` ile sağlık kontrolü. Gereken
secret'lar: `SERVER_HOST`, `SERVER_USER`, `SSH_PRIVATE_KEY`. `feat/*`
branch'leri deploy etmez.

Yedekleme/geri yükleme/vLLM kontrol timer'ları systemd ile host'ta çalışır:
`deployment/` altındaki `.service`/`.timer` dosyaları ve
`deployment/README.md`. Canlıya alırken yapılacakların tam listesi için
aşağıdaki "Canlıya alırken yapılacaklar" bölümüne bakın.

## Otomatik Tek-Komut Kurulum (Yeni Makine)

Depoyu çektikten sonra tek komut:

    just quickstart

Sırasıyla:

1. `.env` yoksa `.env.example`tan kopyalar (varsa **dokunmaz**),
2. `just setup` — venv + Python bağımlılıkları + `npm install`,
3. `just migrate` — `just db` ile Postgres'i başlatır, hazır olmasını
   bekler (`pg_isready`, en fazla 60 sn), `db/migrations/*.sql` dosyalarını
   sırayla uygular,
4. ekrana sıradaki adımı yazar: `.env`i doldur (giriş bilgisi + hash/secret
   üretme komutları dahil), sonra `just dev`.

Komut **idempotenttir**: ikinci kez çalıştırmak veriyi bozmaz, mevcut
`.env`i ezmez, migration'lar "zaten var" deyip geçer.

`just migrate` `.env`i `source` ETMEZ — `AUTH_PASSWORD_HASH` içindeki
`$2b$12$...` kabuk tarafından değişken sanılıp patlıyordu; yalnızca
`POSTGRES_USER` ve `POSTGRES_DB` satırları okunur.

Kurulumdan sonra:

    just dev       # api :8000 + web :5173
    just seed      # örnek ürünler
    just test      # her şey yeşil mi

## Geliştirme Kuralları

### Komutlar

    just dev       # api + web tek terminalde (Ctrl+C ikisini kapatır)
    just api       # sadece backend, --reload-dir app (node_modules izlemez)
    just web       # sadece frontend
    just test      # pytest, docker db'yi otomatik ayağa kaldırır
    just seed      # örnek ürünler (saman, arpa)

### Test disiplini

Her ledger/catalog değişikliğinde `tests/test_ledger.py` veya
`tests/test_catalog.py`'a karşılık gelen test eklenir. Özellikle:
kuruş yuvarlama, append-only tetikleyici, ters kayıt, para/mal ayrımı.
`just test` önce yeşil olmadan commit atma.

### Branch ve commit

`main` (üretim, korumalı değil ama disiplinli çalış) / `develop` /
`feat,fix,chore,docs,test`+`/açıklama`. Conventional Commits, Türkçe özet,
küçük harf başlangıç, nokta yok. Detay: `CONTRIBUTING.md`.

## Klasör Yapısı

    app/                    Backend (FastAPI)
      api/                  HTTP uçları: routes, auth, admin, chat
      bot/                  Telegram botu (ayrı servis, aynı imaj)
      services/             İş mantığı
        ledger.py             PARA MATEMATİĞİ — tek yer, Decimal
        catalog.py            ürün eşleştirme (normalize + alias + fuzzy öneri)
        saman_fiyat.py        varsayılan saman balya fiyatı (kural 4'ün istisnası)
        parser.py             regex/kural parser — BİRİNCİL anlama
        llm_provider.py       NVIDIA / vLLM / Ollama katmanları + doğrulama
        llm_prompt.py         few-shot sistem prompt'u
        intent_resolver.py    kişi/ürün çözümleme, güvenlik eşikleri
        message_processor.py  niyet → defter işlemi + onay akışları
        message_splitter.py   tek mesajda çoklu istek bölme
        request_queue.py      kalıcı istek kuyruğu (pending_requests)
        web_chat*.py          web sohbeti + durum, telegram_intake / web_intake
        person_archive.py     kişi arşivleme (silme)
        person_edit.py        kişi alanı düzenleme + audit
        report.py             PDF raporlar (reportlab)
        backup.py restore.py  restic listeleme, geri yükleme kuyruğu
        stt.py                Groq Whisper (sesli mesaj)
        health.py queries.py name_utils.py message_trace.py vllm_control.py
      models.py schemas.py db.py config.py main.py

    web/                    Frontend (React + TS + Vite)
      src/pages/            People, PersonDetail, PersonForm, AddEntry, Admin, Login
      src/components/       Layout, modallar, ChatWidget, admin/ bölümleri
      src/lib/              DOM'suz saf mantık + testleri (running, goods,
                            voiceWave, chatNudge, sideDrawer, actionBar, format)
      src/api/              istemci, tipler, admin çağrıları

    db/
      schema.sql            KANONİK şema (taze kurulumda otomatik çalışır)
      migrations/           mevcut veritabanını taşıyan idempotent adımlar
      init-test-db.sh       hesaplik_test veritabanını açar

    scripts/                Host tarafı operasyon: backup.sh, restore-apply.sh,
                            restore-test.sh, vllm-control.sh
    deployment/             systemd .service/.timer dosyaları + README
    tests/                  pytest (ledger, parser, bot akışları, api, stt...)
    .github/workflows/      ci.yml, security.yml, deploy.yml
    Justfile                tüm geliştirme komutları
    docker-compose.yml      yerel db (+ server/gpu profilleri)
    docker-compose.prod.yml üretim: db + api + bot + ollama

## Defter ve Veri Güvenliği Kararları

### Silme = arşive taşı (kullanıcı kararı, 2026-07)

Kullanıcı "Sil" dediğinde kayıt YOK EDİLMEZ, ayrı arşiv tablosuna taşınır:

- `archived_transactions` tablosu: transactions'ın tüm kolonları + kalemleri
  (JSONB) + arşiv meta (archived_by, archived_at, archive_reason).
- Silme akışı: (1) kaydı + kalemlerini archived_transactions'a kopyala,
  (2) canlı transactions'tan gerçekten sil. Böylece canlı defter sade
  kalır, bakiye silineni saymaz; silinen her şey arşivde kim/ne zaman
  bilgisiyle durur.
- Append-only tetikleyicisi hâlâ geçerli: rastgele DELETE yasak. Yalnızca
  "arşivle-ve-sil" servis fonksiyonu, session-local bir işaret
  (SET LOCAL app.archiving = 'on') ile tetikleyiciye izin verir; işaret
  yoksa DELETE reddedilir. Böylece kod hatası defteri sessizce bozamaz,
  ama kasıtlı arşiv silmesi çalışır.
- "Düzelt": eskiyi arşive taşı (sil) + yeni değerle yeni kayıt aç. Kullanıcıya
  tek işlem gibi görünür.
- Ayrıca 5 dakikada bir tüm veritabanının otomatik yedeği alınır (Faz 2,
  pgBackRest/restic). Arşiv tablosu felaket kurtarma değil, günlük
  "sildim/düzelttim" işlemlerinin izini tutar; ikisi farklı amaç.

### Yazarak onay her silmede

Yazarak onay yalnızca kişi silmede değil, HAREKET (borç/tahsilat) silmede
de uygulanır. Ortak bir ConfirmDeleteModal bileşeni kullanılır; onay
kelimesi her yerde aynıdır: settings.business_name'in ilk kelimesi, Türkçe
kurallarla büyük harfe çevrilmiş (örn. "DUMAN").

Ayrım kasıtlıdır:
- **Düzelt** → yazı istemez. Para kaybolmaz, değeri değişir (eskisi arşive
  gider, yenisi açılır). Sık kullanılan, düşük riskli işlem.
- **Sil** → yazı ister. Kayıt defterden tamamen kalkar. Nadir, yüksek
  riskli işlem.

### Kişi eşleştirme güvenliği (2026-07-25 — kritik bug düzeltmesi)

Telegram'da yanlış kişiye para yazma hatası oldu: "furkan yılmaz" yazıldı,
sistem "furkan duman"a ekledi. Sebep: pg_trgm eşiği gevşekti, farklı
soyadlı iki isim "tek net eşleşme" sayıldı.

Kurallar:
- **Net eşleşme = yalnızca birebir (normalize) ad eşleşmesi VEYA belirgin
  şekilde tek yakın aday.** İki aday arasındaki benzerlik farkı küçükse
  (ör. ikisi de eşiğin üstünde) ASLA otomatik seçme — "hangisi?" diye sor.
- **Soyad ayırt edicidir.** "furkan yılmaz" ile "furkan duman" NET eşleşme
  değildir; girdi iki kelimeyse (ad+soyad) ve tam eşleşen kişi yoksa,
  kısmi eşleşmeleri aday olarak sun, otomatik bağlama.
- **Tek kelime girdi ("furkan") birden çok kişiye uyuyorsa** → hepsini
  aday göster, "hangisi?" diye sor. Otomatik seçme.
- Eşik değerleri kod içinde sabit sihirli sayı olarak değil, adlandırılmış
  sabit olarak tanımlansın (SIMILARITY_STRONG, SIMILARITY_CANDIDATE) ki
  ayarlanabilsin.
- Para yazan hiçbir işlemde "muhtemelen bu kişidir" varsayımı yapılmaz.
  Şüphe varsa sor. Yanlış kişiye borç yazmak, bir soru sormaktan çok daha
  pahalıdır.
- Bu mantık testlerle korunsun: "furkan yılmaz vs furkan duman" senaryosu
  ve "tek kelime iki adaya uyuyor" senaryosu tests/test_intent_resolver.py'de
  bulunmalı.

## Web Arayüzü Kararları

### Arayüz tasarım kuralları (Faz 1 sonrası)

- **Koyu mod statik.** Light mod yok. Zemin #0e1013, panel/kart #15181d,
  girdi #1c2026, kenarlık #2a2f37, metin #f2f0ea, soluk metin #8b93a1.
  Borç kırmızı #f0857d (koyu üzerinde), tahsilat mavi #6aa9f0. Tutarlar
  monospace + tabular-nums.
- **İki kolonlu düzen (masaüstü ≥720px):** solda sabit panel (230px),
  sağda içerik. Panelde: "DUMAN HOLDING" başlık, altında metrik kartları
  (Kişi sayısı, Toplam alacak, Toplam borç), en altta Yenile ve Ayarlar
  butonları + "Güncellendi · saat" bilgisi. Dar ekranda panel üste taşınır,
  metrikler yatay şerit, Yenile/Ayarlar ikon.
- **Ana ekran tablosu:** sütunlar Ad Soyad / Borç-Alacak (eski "Kalemler")
  / Bakiye / Son işlem. Her satır sonunda üç nokta (ti-dots) menüsü:
  Düzenle, Sil. Alt buton tek: siyah "Kişi ekle". Borç/Tahsilat ana
  ekranda YOK.
- **Kişi defteri (detay):** Borç ekle + Tahsilat ekle butonları burada,
  ikisi de modal (pop-up) açar — ayrı sayfaya gitmez. Modal koyu, ortalı,
  X ile kapanır, arka plan koyu yarı saydam. Borç modalı: tarih, ürün,
  adet+birim, tutar. Tahsilat modalı: tarih, ürün (opsiyonel — boşsa düz
  para), adet+birim (ürün doluysa), tutar. Kaydedince modal kapanır.
- **Bilgilendirme (toast):** her işlemden sonra üstte kısa şerit, birkaç
  saniye sonra kaybolur. "Ahmet Yılmaz'a 20 balya saman borç eklendi",
  "2.000 TL tahsilat eklendi", "Sistem güncellendi" gibi. Yeşil/nötr ton.
- **Yenile butonu:** sunucudan tüm sorguları invalidate eder, bitince
  "Güncellendi" toast'ı ve paneldeki saat güncellenir.
- Toast ve modal için harici kütüphane kullanma, kendi basit
  bileşenlerini yaz (React state + setTimeout). localStorage YOK.

### Kalıcı düzen ve kişi silme (2026-07-24 kararı)

**Sol panel her sayfada kalır.** People, PersonDetail — hepsinde solda
sabit panel görünür. Bunun için ortak bir Layout bileşeni (React Router
Outlet ile) kullanılır; her sayfa kendi barını çizmez. "Defter'e dön"
butonu sol panelde belirgin şekilde durur.

**Kişi silme üç nokta menüsünden.** Kişi listesinde (ana ekran tablosu) her
satırın sağında üç nokta: Düzenle / Sil. Kişi detayındaki "Hesap kapanınca
kaldırılabilir" butonu KALDIRILIR (kafa karıştırıcıydı; bakiye sıfır
değilken pasif duran bir butondu).

**Silme onayı yazarak.** Kişi silinirken window.confirm yetmez: modal açılır,
kullanıcı işletme adının ilk kelimesini (settings.business_name'in ilk
kelimesi, örn. "DUMAN") yazmadan Sil butonu aktifleşmez. Bakiye sıfır
değilse modalda bu açıkça uyarı olarak gösterilir ("Bu kişinin 10.000 TL
borcu var") ama kullanıcı yazarak onaylarsa işlem yapılır — kayıtlar zaten
soft-delete ve arşivle korunuyor.

**Hareket tablosu sabit.** Kişi defterindeki tablo, içerik azken bile
alanı doldurur; alt buton/eylemler sayfanın en altında sabit durur, tablo
uzadıkça yukarı kaymaz.

### Kişi defteri hareket tablosu (PersonDetail)

Hareketler liste değil TABLO olarak gösterilir. Sütunlar:
Tarih / Ürün / Adet / Birim fiyat / Tutar / (üç nokta menü).

- Ürün sütunu: kalem varsa ürün adı (borçlu/alacaklı etiketi ile), yoksa
  not veya "tahsilat"/"borç". Adet ve birim fiyat kalem yoksa boş (—).
- Tutar: borç kırmızı +, tahsilat mavi −, tabular-nums monospace.
- İptal edilmiş (arşive taşınmış zaten listede yok) veya ters kayıt satırı
  görsel olarak ayırt edilsin.
- Her satır sonunda üç nokta (ti-dots) menü: "Düzelt" ve "Sil".
  - Sil: DELETE /api/transactions/{id} (arşive taşır), onay iste, "Kayıt
    silindi" toast'ı.
  - Düzelt: kaydın değerleriyle dolu bir düzenleme modalı açar; kaydedince
    eskiyi sil (arşivle) + yeni değerle yeni kayıt aç, "Kayıt güncellendi"
    toast'ı. Kullanıcıya tek işlem gibi görünür.
- Dar ekranda (mobil) tablo yatay kaydırılabilir ya da satır düzenine
  düşebilir, ama masaüstünde net sütun/satır tablo.

### Ayarlar menüsü (yapılacak)

Sol paneldeki Ayarlar butonu bir ayarlar ekranı/modalı açar. İçinde
(Faz 2'de doldurulacak): yedekleme durumu, "şimdi yedekle", "yedekten dön",
arşivlenen kayıtları görüntüle. Şimdilik yerini aç, altını sonra doldur.

### İşletme ayarları (settings tablosu)

Sol paneldeki işletme adı sabit değil, Ayarlar'dan değiştirilebilir ve
sunucuda saklanır (her cihazda aynı görünür, yenileyince kaybolmaz).

- `settings` tablosu: key TEXT PRIMARY KEY, value TEXT, updated_at.
  Basit anahtar-değer. İlk anahtar: business_name (varsayılan "Hesaplık").
- API: GET /api/settings (tüm ayarları döner), PUT /api/settings/{key}.
- Sol panel başlığı business_name'den okur. Ayarlar modalında düzenlenir,
  kaydedilince "Ayarlar güncellendi" toast'ı ve panel başlığı yenilenir.
- İleride buraya başka ayarlar da eklenebilir (para birimi, yedekleme
  aralığı vb.) — key-value olduğu için şema değişmeden büyür.

### Borç/tahsilat formu — tek akıllı ürün alanı (2026-09-05)

Ayrı ürün / adet / birim alanları KALDIRILDI. DebtModal ve PaymentModal'da
tek bir alan var ("Ürün ve adet"); ayrıştırma `web/src/lib/goods.ts`'te,
DOM'suz ve testli (`web/src/lib/goods.test.ts`).

    "20"                     -> 20 balya saman   (ürün yazılmazsa saman)
    "20 kg arpa"             -> 20 kilo arpa
    "500 balya saman"        -> 500 balya saman
    "arpa 20 kilo"           -> sıra serbest
    "20kg arpa"              -> bitişik yazım ayrılır
    "70-20-50"               -> koşan format (running.ts) — 20 balya saman
    "20 balya saman 5000 tl" -> tutar alanına 5000 ÖNERİLİR

**Birim yazıdan algılanır**, dropdown'la uğraştırılmaz: kg/kilo/kilogram →
kilo, gr → gram, lt → litre, cuval → çuval... (parser.py'deki UNITS
kümesinin form karşılığı). Birimler AYRI tutulur: 20 balya ≠ 20 kilo.
Öncelik **yazı > dropdown yedeği > ürünün base_unit'i > balya**. Dropdown
silinmedi ama katlandı: alanın altında "Birim: balya — değiştir" bağlantısı,
tıklanınca açılır. Yazıda birim geçtiği anda yedek düşer — yazı kazanır.

**Alanın altında ne kaydedileceğinin yankısı durur** (`.goods-echo`,
ipuçlarından büyük): "→ 20 balya saman", koşan formatta "→ 70 → 50 · 20
balya saman (borç)". Yeni ürün açılacaksa ve yön modalla çelişiyorsa ayrıca
uyarılır (çelişki engellemez, modalı kullanıcı seçti).

**Tutar HER ZAMAN zorunlu.** `checkForm` tek karar noktasıdır; Kaydet pasifse
butonun üstünde sebebi yazar ("Tutar girin", "Adet anlaşılmadı", koşan format
matematik hatası). Boş/sıfır tutarla kayıt yok.

**Otomatik fiyat tiki.** Ürünün kayıtlı birim fiyatı varsa (products →
PriceHistory, `unit_price`) "Kayıtlı fiyattan hesapla (75,00 ₺/balya)"
kutusu çıkar; işaretlenince tutar = adet × birim fiyat (koşan formatta FARK
üzerinden), alan salt okunur olur. **Tik varsayılan olarak KAPALIDIR** —
fiyat listesi tutarı bağlamaz (kural 4), kullanıcı isterse hesaplatır.
**İstisna saman** (2026-09-11): ürün saman ve birim balya ise fiyat, ürünün
kayıtlı fiyatı yerine varsayılan saman fiyatıdır ve tik AÇIK başlar
("Varsayılan saman fiyatından hesapla") — bkz. "Varsayılan saman fiyatı".
Birim ürünün kendi birimiyle tutmuyorsa ("20 kg saman", fiyat balya başına)
tik PASİF: yanlış birimle çarpım yapılmaz, sebebi yazılır.

**Belirsizlik sessizce yutulmaz.** İkinci bir çıplak sayı ürün adına
karışmaz ("20 saman 15" → "Anlaşılmadı: 15"), adet yazılmamışsa uydurulmaz,
"... 5000 tl" önerisi kullanıcının ELLE yazdığı tutarı ezmez.

Tahsilat formunda alan boş bırakılabilir (düz para); doluysa borçla birebir
aynı mantık. EditTxModal ve AddEntry sayfası şimdilik eski ayrı alanlarda
kaldı (sonraki iş).

### Web chat durdurma ve mesaj düzenleme (2026-09-02)

Web sohbeti normal AI arayüzleri gibi davranır: süren/bekleyen işi durdurma
ve önceki mesajı düzenleyip yeniden gönderme. İş ağırlıklı FRONTEND'dedir
(`web/src/components/ChatWidget.tsx`); sunucuda tek yeni uç var.

**Durdurma (⏹).** Buton, gönder okunun yerinde ya da yanında durur:
- İstek sürerken (`sending`) → gönder butonunun YERİNE geçer; `AbortController`
  ile /api/chat isteğini keser, "İptal edildi." notu yazılır (toast YOK,
  kullanıcı bilerek durdurdu).
- Bot bir şey sormuşken (son cevapta buton var ya da `awaits_text`) → gönder
  butonunun YANINDA durur; gönder erişilebilir kalmalı ki kullanıcı isterse
  soruyu yazarak da cevaplayabilsin.
İki durumda da `POST /api/chat/cancel` çağrılır.

**`POST /api/chat/cancel` (JWT korumalı, gövdesiz).** İptal edilecek durum
zaten oturumun kendisine ait (`actor` = chat_id). Yaptığı iki şey:
`web_chat_state.clear_pending` + kuyrukta `beklemede`/`isleniyor` kalan
parçaları `iptal` işaretlemek. İkincisi olmazsa bir sonraki komut, iptal
edilen batch'in kalanını sürükler. **Deftere dokunmaz** — hiçbir kayıt
silinmez/arşivlenmez, `undo` (60 sn "Geri al") alanına KASTEN dokunulmaz:
o, kaydedilmiş bir işlemin ayrı penceresidir, bekleyen bir soru değil.

Bilinen sınır: yoldaki istek sunucuda iptalden SONRA biterse yeni bir
bekleyen soru bırakabilir; buton yeniden görünür, ikinci basış temizler.
Sessiz bozulma değil, görünür ve tekrarlanabilir.

**Mesaj düzenleme (kalem).** Her KULLANICI balonunun solunda kalem ikonu
(hover'a saklanmaz — dokunmatikte hover yok). Tıklayınca metin input'a gelir,
düzenleme moduna girilir (üstte "Mesaj düzenleniyor" + Vazgeç, Esc de vazgeçer).
Gönderilince o balon ve ONDAN SONRAKİ TÜM balonlar (botun ona verdiği cevap
dahil) silinir, düzenlenmiş metin yeniden gönderilir — sohbet oradan yeniden
başlar. Silinen dalın sunucuda bıraktığı bekleyen soru da `cancel` ile
düşürülür, yoksa yeni metin o soruya "cevap" sanılır.

### Chat balonu: dışa tıkla küçült + hatırlatma animasyonu (2026-09-03)

**Boşluğa tıkla → küçült.** Panel açıkken panelin DIŞINA (`pointerdown`,
`document` üzerinde) tıklamak paneli küçültür; başlıktaki ✕ de kalır. İkisi
de yalnızca `open`'ı false yapar: `bubbles` ChatWidget'ta yaşadığı ve bileşen
Layout'ta monte kaldığı için geçmiş KORUNUR, balona basınca aynı sohbet
geri gelir. Sunucudaki bekleyen soru da düşürülmez — küçültmek "iptal"
değildir, iptal ⏹ butonudur. Panelin içi (girdi, teyit butonları, mesajlar)
asla küçültmez. Dinleyici `pointerdown`da (click değil) ki tıklanan öğe
kaybolsa bile hedef hâlâ panelin içinde sayılsın. Dar ekranda panel zaten tam
ekran olduğu için "dış" yoktur, davranış değişmez. Arka planda karartma
(backdrop) YOK: uygulama panel açıkken de kullanılabilir kalır.

**Balonun hatırlatma animasyonu.** Panel KAPALIYKEN sağ alttaki balon
~10 saniyede bir 1,4 saniyelik kısa bir hareket yapar (hafif zıplama +
%4 büyüme). Oturumda BİR KEZ, ilk açılıştan 2,5 sn sonra biraz daha belirgin
hâli oynar (halka + %8). Panel açıkken animasyon yok. İpucu balonu ("yardım
lazım mı?") kasten eklenmedi — 60 yaş kullanıcı için hareket az ve sade
olmalı. `prefers-reduced-motion` açıksa index.css'in genel kuralı tüm
animasyonları kapatır.

Zamanlayıcı ve "dışarısı mı?" kararı bileşenden ayrı: `web/src/lib/
chatNudge.ts` (süre sabitleri burada, CSS'teki 1,4 sn ile aynı olmalı).
Böylece DOM'suz test edilir: `just web-test` → `node --test` (Node kendi
koşucusu, ek npm bağımlılığı yok; `*.test.ts` tsconfig'de hariç tutulur).

### Mobil düzen: çekmece panel + çakışmayan balon (2026-09-03)

Masaüstü tasarımı dar ekranda bozuluyordu: 230px'lik sol panel telefonda
tüm ekranı yiyordu ve sohbet balonu "Kişi ekle" butonunun üstüne biniyordu.
Eşik **720px** — zaten tablo/defter satırı ayrımının kullandığı eşik, ikinci
bir kırılma noktası açılmadı. Masaüstü (≥720px) HİÇ değişmedi; her şey
`@media (max-width: 719px)` bloğunda.

**Sol panel mobilde çekmece.** Varsayılan gizli (`visibility: hidden` +
`translateX(-100%)`, display:none DEĞİL — kayma animasyonu bozulmasın ve
kapalıyken sekme sırasına girmesin). Üstte yapışkan bir şerit (`.mobile-bar`,
56px): solda ☰ (48px dokunma alanı), yanında işletme adı. ☰ paneli soldan
kaydırarak açar; arka plan karartılır. Kapatma: ☰'ye tekrar basmak,
karartıya tıklamak, paneldeki ✕, Esc, ya da menüden bir yere gitmek.
Kural bileşende değil `web/src/lib/sideDrawer.ts`'te (`nextDrawerState`):
yalnızca ☰ AÇAR, diğer her olay kapatır — DOM'suz test edilir
(`just web-test`). Panel üstüne binen bir katman (fixed), içerik kaymaz.
PersonDetail'in kendi yapışkan başlığı mobil barın altına yapışır
(`.side-main .bar { top: 56px }`).

**Karartma sohbet balonunun da üstünde** (z-index: karartma 160, çekmece 170,
sohbet 150). Menü açıkken ekranın tek işi menüdür; balona denk gelen yere
dokunmak menüyü kapatır, yanlışlıkla sohbet açmaz.

**Sohbet balonu artık alt eylem barının ÜSTÜNDE.** (GÜNCELLENDİ 2026-09-04:
balon artık barın üstünde değil, barın İÇİNDE — aşağıdaki "Alt eylem barı"
bölümüne bak. Yüzer konum, üst üste binme sorununu tam çözmüyordu.)

**Sohbet paneli mobilde tam ekran.** Yüzen panele geçiş eşiği 480px'ten
720px'e çekildi: 360px'lik telefonda 380px'lik yüzen panel okunmuyordu.
Tam ekranda "dışarısı" olmadığı için dışa-tıkla küçültme mobilde doğal
olarak devre dışı; ✕ ile kapatılır (geçmiş yine korunur).

**Okunabilirlik (60 yaş).** Dar ekranda 13px'lik yardımcı metinler
(`.row-sub`, `.muted`, `.chip`, `.hint`) 14px'e, sohbet balonu 15px'e
çıkar; metrik kartları çekmecede alt alta durur (yatay kaydırma yok);
kişi defteri başlığındaki butonlar sığmazsa alt satıra taşar (isim
kırpılmaz). Dokunma hedefleri `--tap` (52px) ve ☰ için 48px.

### Alt eylem barı — tek opak şerit (2026-09-04, HCI)

Gerçek telefonda dört sorun çıktı: (1) kaydırınca kişi isimleri "Kişi ekle"
butonunun arkasından geçip gidiyordu, (2) buton telefonda gereğinden büyüktü,
(3) sohbet balonu butonun üstüne biniyordu, (4) "← Defter" o kadar küçüktü ki
basmaya çalışırken kişi adına ya da "Ekstre (PDF)"ye deniyordu.

**Kök sebep: her eylem kendi `position: fixed` butonuydu.** Yüzen butonların
arkası boştur; içerik altlarından akar ve birbirlerinin yerini bilmezler.
Çözüm tek bir bardır: `.action-bar` (Layout'ta, fixed, OPAK `--panel` zemin +
üst çizgi + yukarı gölge), içinde iki yuva — `main` esner, `side` 56px sabit.

**Butonlar sayfada kalır, yeri bar olur.** Mantık People/PersonDetail/
ChatWidget'ta; yalnızca çizildikleri yer değişir (`lib/actionBar.tsx`, context
+ `createPortal`). Yuvalar DOM düğümü olarak paylaşılır, Layout onları
callback ref ile state'e koyar; ilk render'da null dönmek normaldir.

**İçerik barın arkasına GİRMEZ.** `--bar-h` barın TAM yüksekliği (1px üst
çizgi dahil: masaüstü 81px, <720px 73px), `--bar-space` buna safe-area ekler.
`.side-main` bu kadar alt boşluk bırakır, `.page`in min-height'ı bu kadar
kısalır. 1px'i unutmak son satırı barın kenarlığına sokuyordu (ölçümle
yakalandı).

**Sıra: "Kişi ekle" solda esner, sohbet balonu sağda 56px kare sabit**,
aralarında 12px (mobilde 14px) — yanlış tıklamayı önleyecek kadar. Panel
açıkken yuva boşalır ama yeri ayrılı kalır, bar zıplamaz. Toast (z-index 155)
ve masaüstündeki yüzen sohbet paneli barın üstünde başlar.

**Mobil override'lar dosyanın EN SONUNDA.** `@media` özgüllük eklemez; aynı
özgüllükte sonra gelen kazanır. Mobil blok `.fab`/`.row-menu-trigger`/
`.chat-btn` temel tanımlarından önce durursa CSS'te doğru görünür ama
tarayıcıda uygulanmaz — bir kez tam olarak bu oldu (48px yazıyordu, 56px
çiziliyordu). `actionBar.test.ts` bunu sıra kontrolüyle koruyor.

**Dokunma hedefleri (HCI alt sınırı 48px).** "Kişi ekle" mobilde 56→48px
küçülür ama altına inmez. "← Defter" her iki ekranda da çerçeveli 52px'lik bir
tuş (metin bağlantı değil), kişi adından 18px ayrık. Üç nokta menüsü 44px
(mobilde 48), menü seçenekleri 48px, bot şıkları 44/48px, yan panel ikonları
ve ✕'ler 48px, `.link` (Düzenle / Ekstre PDF) `--tap` yüksekliğinde — ikon
görsel olarak küçük kalır, dokunma alanı büyür.

**Test:** `web/src/lib/actionBar.test.ts` (`just web-test`) index.css ve
Layout.tsx metnini okuyup sözleşmeyi doğrular: bar opak mı, `--bar-space`
türetilmiş mi, butonlar hâlâ fixed mi, balon sağda mı, hedefler 48px mi,
mobil override sırası doğru mu. DOM koşucusu yok; geometri ayrıca headless
Chrome ile 345/500/768/1400px'te ölçülerek doğrulandı.

### Web sohbetinde sesli mesaj (2026-09-06, Faz 5'in web ayağı)

Telegram'daki sesli mesajın web karşılığı. **Ses yalnızca bir ÖN ADIMDIR:**
mikrofon → Groq whisper → METİN → yazılı mesajla AYNI akış (parser → gerekirse
LLM → intent_resolver). İkinci bir "sesli mesaj mantığı" YOK; ara onay da yok
(net cümle doğrudan deftere geçer, Telegram'daki gibi).

**`POST /api/chat/voice` (JWT korumalı, multipart).** `web_chat.handle_voice`:
ham kayıt Groq'a GİTMEDEN önce açılır ve COMMIT edilir (`web_intake.
save_web_voice_message`, kanal `web_voice`) — çeviri başarısız olsa bile
"kullanıcı burada bir şey söyledi" izi durur. Çeviri gelince aynı satıra
`voice_transcript` yazılır ve `handle_text(..., raw=raw)` çağrılır: tek ham
kayıt, iki iz. **Ham ses SAKLANMAZ**, payload yalnızca boyut/biçim tutar.

- STT kapalı (GROQ_API_KEY yok) ya da ses anlaşılmadı → **200** + sohbet
  balonu ("yazarak gönderir misin?"). Bu bir arıza değil bir cevaptır;
  arayüzde tek yol olsun diye hata kodu döndürülmez. Kullanıcıya "STT/Groq/
  model" gibi teknik kelime gösterilmez. Metinler bottan import edilir
  (`VOICE_KAPALI_METNI`, `VOICE_ANLASILAMADI_METNI`) — iki mantık olmaz.
- Yanıt `transcript` de taşır; arayüz bunu KULLANICI balonu olarak yazar.
  Yanlış anlaşıldıysa kullanıcı o balonu mevcut kalem düğmesiyle düzeltip
  yeniden gönderir — ayrı bir "onaylıyor musun?" adımı eklenmedi.
- Kaynak izi: `TxSource.WEB_VOICE` (yeni enum değeri, `db/migrations/
  012_web_voice_source.sql`). `_tx_source_for` KANALA önce bakar — web sesli
  mesajında `voice_transcript` de doludur, sıra ters olsa kayıt
  TELEGRAM_VOICE görünürdü.
- Biçim: tarayıcı MediaRecorder'ı Chrome/Firefox'ta webm/opus, iOS Safari'de
  mp4 üretir. Groq biçimi DOSYA ADININ uzantısından anlar; uzantı gerçek
  `recorder.mimeType`tan türetilir (`voiceWave.filenameForMime`) ve sunucuda
  allowlist'ten geçer (`chat._voice_filename`). 10 MB üstü 413.

**Arayüz — WhatsApp tarzı kayıt çubuğu** (`ChatWidget.tsx`). Mikrofon, yazı
alanı boşken gönderin YERİNİ alır (yan yana iki "asıl eylem" olmaz). Basınca
yazı satırının yerine kayıt çubuğu gelir: 🗑 iptal · yanıp sönen kırmızı nokta
+ süre · canlı ses dalgası · yeşil gönder. Tıkla-başlat / tıkla-gönder
(bas-tut YOK — 60 yaş kullanıcı ve dokunmatik için basılı tutmak zordur).
Çubuk yazı satırıyla aynı yükseklikte (73px) — kayda basınca panel zıplamaz.

- Dalga bir SÜStür, veri değil: AnalyserNode → RMS → çubuk yüksekliği, sola
  kayar. Analiz kurulamazsa dalga düz kalır, kayıt etkilenmez.
- 1 sn'den kısa kayıt gönderilmez (kazara dokunuş), 120 sn'de kendiliğinden
  durup gönderir (cepte unutulan mikrofon).
- **Panel küçülürse kayıt İPTAL edilir** — görünmeyen bir mikrofon açık
  kalamaz. Temizlik tek yerde (`useVoiceRecorder.teardown`): gönder, iptal,
  hata, bileşen sökümü hepsi oradan geçer.
- İzin reddi ile "cihazda mikrofon yok" ayrı mesaj alır; ikisinde de yazarak
  gönderme yolu açık kalır. Sesli mesaj bir kolaylıktır, tek yol değildir.
- Saf mantık `web/src/lib/voiceWave.ts`te (süre biçimi, seviye→çubuk, mime→
  dosya adı) ve DOM'suz test edilir; tarayıcı API'leri `useVoiceRecorder.ts`te.

Testler: `tests/test_chat_voice_api.py` (ses→metin→akış, STT hatası, allowlist,
onay akışı paritesi), `tests/test_stt.py`, `web/src/lib/voiceWave.test.ts`.

## Telegram Botu Kararları

### Faz 3 — Telegram bot

**Mesaj asla kaybolmaz.** Webhook/polling ile gelen her güncelleme, işleme
girmeden ÖNCE `raw_messages` tablosuna yazılır ve hemen 200 dönülür.
İşleme ayrı adımda yapılır. Böylece parser çökse, deploy yapılsa, sunucu
yeniden başlasa bile mesaj durur ve sonra işlenir.
- Idempotency: `raw_messages(channel, external_id)` tekil indeksi
  (external_id = Telegram update_id). Aynı güncelleme iki kez işlenmez.

**LLM'den önce kural parser.** Mesajların çoğu düzenli kalıptadır ve regex
ile 15 ms'de çözülür; LLM 300 ms sürer. Kural parser önce dener,
çözemezse (Faz 4'te) LLM'e devreder. `RuleProvider` her zaman açıktır ve
asla kapanmaz — GPU yoksa sistem yine çalışır, sadece daha çok soru sorar.

**Yerelde polling, sunucuda webhook.** Yerel geliştirmede tünel gerekmesin
diye long polling; üretimde Caddy arkasında webhook. Aynı işleme kodu,
farklı besleme.

**Admin/müşteri ayrımı.** `/engine`, `/queue`, `/logs` gibi komutlar sadece
`TELEGRAM_ADMIN_IDS` içindeki chat_id'lere yanıt verir. Yetkisiz kişi bu
komutları yazarsa HİÇ cevap verilmez (komutun varlığı bile sızmasın).
`setMyCommands` scope ile müşteriye yalnızca /start ve /yardim gösterilir.

**Onay akışı.** Güven yüksek ve eşleşme netse: anında kaydet + 60 sn "Geri
al" butonu. Belirsizse TEK soru sor (inline keyboard butonlarıyla, yazı
yazdırma). Asla iki soru üst üste sorma, asla "formatı şöyle yazın" deme.

**Müşteriye teknik detay gösterilmez.** Provider adı, güven skoru, hata
kodu, "LLM/AI/model" kelimeleri asla görünmez.

### Telegram sorgu komutları (regex, Faz 3 devamı)

Kayıt kadar sorgulama da Telegram'dan yapılır. Kural tabanlı (LLM yok),
sonuç Telegram'a METİN liste olarak döner.

Desteklenecek sorgular (esnek kalıp, ek/yazım toleranslı):
- "kişileri listele" / "kişileri sırala" → tüm kişiler + bakiye + kısa kalem
- "borçluları listele" → bakiyesi + olanlar (kişi sana borçlu)
- "alacaklıları listele" → bakiyesi − olanlar (sen borçlusun / peşin ödeyen)
- "{ilçe}lileri listele" → o ilçedeki kişiler (ör. "bergamalıları listele",
  "ahmetbeylerlileri listele"). İlçe adı Türkçe ekle çekimli gelebilir
  (-li/-lı/-lu/-lü + -leri/-ları); eki soyup persons.district ile eşleştir.
- "{isim} borcunu söyle" / "{isim} borcu ne kadar" → tek kişi bakiye+kalem
  (mevcut bakiye sorgusu, kişi eşleştirme güvenlik kurallarıyla)

Sıralama: bakiye büyükten küçüğe (en çok borçlu üstte). Liste uzunsa
Telegram mesaj sınırına (4096 karakter) dikkat, gerekirse parçala.
Format: mono hizalı değil, sade okunur — "Ad Soyad — 1.500 TL borçlu".
Para tutarları Türkçe biçim (1.500,00 TL). Boş sonuç → "Kimse yok" tarzı
nazik mesaj.

Backend: bu filtreler API'de de işe yarar, ledger/servis katmanına
eklensin (ilçeye göre, borçlu/alacaklı filtresi), bot ve web ikisi de
kullanabilsin.

### "/" komut menüsü (2026-09-13)

Telegram'da "/" yazınca çıkan öneri listesi (`setMyCommands`). Komutlar
KENDİ defter mantıklarını kurmaz, mevcut olanı TETİKLER: kişi eşleştirme
`find_person_match`, "hangisi?" `_candidates_keyboard`, yeni kişi
`_new_person_flow_baslat`, kaydın kendisi `message_processor.process_intent`
→ `handle_resolved`. Böylece varsayılan saman fiyatı, koşan format, ürün
önerisi, çoklu istek kuyruğu ve 60 sn "Geri al" komut yolunda da çalışır.

**DİREKT (tek adım):**
- `/yedek` — veritabanını gzip'leyip aynı sohbete gönderir
  (`telegram_yedek.yedek_gonder`). YALNIZCA `TELEGRAM_ADMIN_CHAT_ID`;
  başkasına "Bu komut sadece yönetici içindir." denir (/durum'un sessizliğinden
  farklı — komut menüde zaten yalnızca yöneticide görünüyor). Chat id tanımsızsa
  kimseye çalışmaz (fail-closed). Her deneme audit_log'a yazılır.
- `/bakiye` — defter toplamı; `/bakiye ahmet` — kişinin bakiye tablosu.
- `/kisi ahmet`, `/koy bergama` — mevcut arama / ilçe listesi.

**ADIM ADIM (eksik bilgiyi sorar):** `/borc`, `/tahsilat`, `/kisiekle` ve
argümansız `/kisi`, `/koy`. Bekleyen soru `chat_data["komut_akisi"]`nda durur,
cevabını `on_text` yakalar (diğer bekleyen akışlardan sonra, düz metin
işlemeden önce).

`/borc` akışı: kişi sorulur → `find_person_match` (LLM'e SORULMAZ: kullanıcı
zaten isim yazmak için sorulan soruyu cevaplıyor) → aday çoksa `komut:pick:`
butonları, kişi yoksa mevcut "Ekleyeyim mi? → ad soyad → telefon/il/ilçe"
akışı (`pending["komut_kind"]` bunu işaretler, `_complete_new_person` kişi
açılınca mal sorusuna döner) → mal/tutar sorulur. Cevap NORMAL ayrıştırmadan
geçer (kişinin adıyla birleştirilip `parser.parse`), kişi yeniden
EŞLEŞTİRİLMEZ (`resolve(..., known_person=...)`).

**Cevapta sıra önemli:** önce "cevabın TAMAMI bir tutar mı?" bakılır
(`parse_amount_reply` — koşan formatın tutar sorusuyla aynı çözücü). Komut
zaten "ne kadar?" diye sorduğundan tek başına bir sayı PARADIR ("Para vs
adet": fiil bağlamındaki çıplak sayı TL'dir, buradaki fiili komut veriyor).
Parser'a bırakılsa aynı sayı fiilsiz saman kalıbına düşerdi ("furkan 20" =
20 balya saman) ve `/tahsilat`'ta "5000 balya saman tahsilat mı?" diye
sorulurdu. Adet kastedilen cevapta birim ya da ürün zaten yazılır ("20
balya", "20 balya saman 5000 tl") — o cevap normal ayrıştırmaya düşer ve
saman/koşan format kuralları aynen işler. Yönü KOMUT söyler — cevaptaki fiil
değiştirmez ve yön artık varsayım olmadığından `assumed_kind` düşürülür
(saman kaydı "borç mu?" diye ikinci kez sorulmaz). Anlaşılmayan cevapta akış
AÇIK kalır, soru tekrarlanır, hiçbir şey kaydedilmez.

Yeni bir komut yarım kalmış eski komut sorusunu düşürür (`_komut_temizle`) —
"/borc" deyip "/bakiye" yazanın sonraki mesajı yanlış akışa gitmez.

**Menü kapsamı:** müşteriye `/start`, `/borc`, `/tahsilat`, `/bakiye`,
`/kisi`, `/koy`, `/kisiekle`, `/yardim`; yönetici sohbetlerine ayrıca
`/durum` ve `/yedek` (`BotCommandScopeChat`) — komutun VARLIĞI müşteriye
sızmaz. Yönetici sohbetleri = `TELEGRAM_ADMIN_IDS` ∪ `TELEGRAM_ADMIN_CHAT_ID`
(ikisi farklı ayar, biri diğerini kapsamayabilir).

**Panelde aynı iş:** `POST /api/admin/yedek-gonder` (JWT) — "Şimdi Yedek Al
ve Telegram'a Gönder" düğmesi. Geri yüklemenin aksine host'a iş devredilmez;
pg_dump container'dan çalışır, sonuç panelde anında görünür (409 meşgul,
503 yapılandırma yok, 502 döküm/Telegram hatası). Restic deposuna snapshot
EKLEMEZ, o yüzden yedek listesi tazelenmez.

Testler: `tests/test_bot_komutlar.py`, `tests/test_admin_yedek.py`.

### Bot "yazıyor..." göstergesi

LLM işlemcide yavaş (~13 sn). Kullanıcı beklerken bot donmuş görünmesin:
- Uzun sürebilecek her işlemde (LLM parse, rapor üretimi) Telegram'a
  "typing" chat action gönder (send_chat_action ACTION_TYPING). Cevap
  gelene kadar tekrarlanır (Telegram typing ~5 sn sürer, uzun işlemde
  periyodik yenilenmeli).
- Regex'in anında çözdüğü komutlarda gösterge gereksiz (zaten hızlı), ama
  zararı yok; basitlik için LLM'e düşen ve rapor üreten yollarda göster.
- PDF gönderirken "upload_document" action kullanılabilir.

### Telegram'dan kişi eklerken detay sorma

Telegram'dan yeni kişi oluşturulunca (borç/tahsilat sırasında "yeni kişi
ekle" seçilince) sadece isim kaydediliyordu; il/ilçe/telefon boş kalıyordu.
Kişi kartı eksik olunca ilçe filtreleri ("bergamalıları listele") çalışmaz.

**Akış:** yeni kişi eklenirken adım adım (conversational) sor:
1. Ad soyad (zaten cümleden çıkarılan isim; onayla veya düzelt).
2. Telefon (opsiyonel, "geç" ile atlanabilir).
3. İl (opsiyonel).
4. İlçe (opsiyonel).
Her adımda "geç"/"atla" butonu olsun (zorunlu değil, sadece isim yeterli).
Bilgiler toplanınca kişi oluşturulur, SONRA bekleyen borç/tahsilat işlenir.

Basit tutulmalı: soru-cevap akışı, kullanıcı istemezse "geç" der, minimum
isimle kaydeder. Aşırı zorlama yok — hızlı giriş için "hepsini geç" imkânı.
State bot tarafında (ConversationHandler veya chat_data) tutulur.

### Bot sorgu anlama — kapsamlı genişletme (2026-07-28, Grup 1)

Kullanıcı botun HER sorgu türevini anlamasını istiyor. Regex birincil,
anlaşılmayan LLM'e. Tüm bu kalıplar REGEX'te olmalı (LLM'e gitmemeli):

**Bakiye (hepsi aynı sonuç — bakiye + açık kalemler tablosu):**
"furkan bakiye", "furkan borc/borç", "furkan borcu ne", "furkan durum",
"furkan durumu", "furkan durumu ne", "furkan durum ne", "furkan hesap",
"furkan cari/cariye", "furkan güncel bakiye", "furkan alacak/alacağı",
"furkan toplam borç", ve TERS SIRA: "durum furkan", "bakiye furkan".
Kelime sırası esnek: {isim} {anahtar} veya {anahtar} {isim}.

**Bakiye çıktısı TABLO halinde:** id/tarih/ürün/adet/fiyat/tutar sütunlu,
sonda güncel bakiye. Telegram'da monospace hizalı okunur liste.

**Bilgi menüsü (belirsiz):** "furkan bilgi" → "Ne bilgisi?" +
[Bakiye/borç] [Kişi bilgileri] [Ekstre PDF] butonları (zaten var, koru).

**Ekstre/döküm:** "furkan hesap dökümü", "furkan dökümanı", "furkan ekstre",
"furkan güncel bakiye pdf" → kişi ekstresi PDF.

**Listeler:** "kişiler"/"kişileri say"/"sistemdeki kişiler"/"tüm kişiler" →
herkes + güncel bakiye. "bergamalılar"/"bergama" (tek kelime) → o ilçe.

**Telegram arama gibi davransın:** tek kelime yazınca:
- "ahmet" (tek başına, komut yok) → tüm Ahmet'leri listele ("hangi ahmet?"
  değil, hepsini bakiyeleriyle göster — arama gibi)
- "duman" → soyadı Duman olanları listele
- "bergama" → Bergama ilçesindekiler
Yani tek kelime = arama: isimde/soyadda/ilçede eşleşenleri listele.

Anlaşılmayan her şey LLM'e (qwen) düşer. Regex kapsamı geniş olmalı ki
LLM nadiren devreye girsin (yavaş).

### Bot kayıt akışı — Grup 2 (2026-07-28)

**Kayıt onay mesajı önceki→güncel bakiye göstersin:**
Borç/tahsilat kaydedilince mesaj net olsun:
"✅ Furkan Duman
 30 balya saman · 5.000 TL borç eklendi
 Önceki bakiye: 10.000 TL
 Güncel bakiye: 15.000 TL borçlu"
Yani: ne eklendi + önceki bakiye + yeni bakiye. Kullanıcı değişimi görsün.

**Kayıt komut türevleri (regex):**
- "ahmet 30 saman 5000tl" → borç kaydı (kısa biçim, "aldı/borç" olmasa da
  isim+adet+ürün+tutar varsa borç varsay; belirsizse LLM'e).
- Çoklu Ahmet → "hangisi?" sor, seçilince kaydet, sonra onay mesajı.

**Yeni kişi oluşturma türevleri:**
"ahmet adında yeni kişi oluştur", "ahmet adında kişi kayıt et",
"ahmet duman kayıt et", "ahmet yıldırım oluştur", "ahmet yıldırım yeni isim/
kişi" → yeni kişi ekleme akışını başlat (SADECE kişi, borç yok).

**Kişi ekleme akışı bilgi sorma + skip (zaten var, koru/iyileştir):**
Yeni kişi eklenince adım adım: ad soyad (onayla) → telefon (Geç) → il (Geç)
→ ilçe (Geç). Her adımda [Geç] butonu. İlk adımda [Hepsini geç] = sadece
isimle hızlı kayıt. Borç/tahsilat sırasında kişi yoksa: önce bu akış, sonra
bekleyen işlem işlenir.

### Bot kişi silme = arşivleme — Grup 3 (2026-07-28, KRİTİK)

**HİÇBİR ŞEY GERÇEKTEN SİLİNMEZ.** "Sil" = arşivle. Tüm veri korunur,
yanlışlıkla silinirse geri getirilebilir. Bu para güvenliğiyle ilgili,
en dikkatli iş.

**"furkanı sil" / "furkanı sil yeniden oluştur" akışı:**
1. Furkan'ın TÜM bilgilerini arşiv tablosuna log'la:
   - kişi kartı (ad, telefon, il, ilçe, oluşturma tarihi)
   - TÜM işlemleri (borç/tahsilat, ürün, adet, tutar, tarih)
   - o anki bakiye
   - arşivleyen (chat_id), arşiv tarihi, sebep
2. Sonra defterde kişiyi pasifleştir (is_active=false, soft delete) —
   satır DB'de kalır ama listede/bakiyede görünmez.
3. "yeniden oluştur" varyasyonu: arşivle + AYNI isimle temiz yeni kişi aç
   (bakiye sıfır, borç/alacak yok). "sadece sil" varyasyonu: arşivle + pasifle,
   yeni açma.
Kullanıcıya teknik detay gösterme ("arşivlendi" yeter, "log tablosu" deme).

**Onay:** silme YAZARAK onay ister (mevcut kural): işletme adının ilk
kelimesi (örn "DUMAN"). Bakiye sıfır değilse mesajda uyar ("Furkan'ın
10.000 TL borcu var, arşivlenecek"). Onaysız silinmez.

**Geri getirme:** arşivden geri getirme SADECE komut satırı/web admin
(kaza riski). Bot'tan geri getirme YOK (yanlışlıkla tetiklenmesin).
Arşiv tablosu tüm veriyi tuttuğu için istenirse elle geri yüklenebilir.

**Mevcut altyapı:** archived_transactions tablosu zaten var (hareket
arşivi için). Kişi arşivi için archived_persons tablosu eklenir (kişi
kartı + o anki bakiye + tüm işlemlerin snapshot'ı JSONB). Kişi silmede
hem kişi hem işlemleri arşivlenir.

**Türevler (regex):** "furkanı sil", "furkan sil", "furkanı kaldır",
"furkanı arşivle", "furkanı sil yeniden oluştur", "furkanı sıfırla".
Çoklu kişi → "hangisi?" (güvenlik). Kişi eşleştirme kurallarıyla.

### Silme mesajı + kişi düzenleme — Grup 4 (2026-07-30)

**"Arşivlendi" yerine "silindi" de:** Kullanıcıya arka plan teknik detayı
gösterme. Arşivleme onayı ve sonucu "silindi" dilini kullansın:
- Onay: "{kişi} silinecek. Bakiyesi {X} TL. Onaylıyorsan {ONAY} yaz."
- Sonuç: "{kişi} silindi." (arka planda arşivleniyor, kullanıcı bilmez)
Kod içi mantık ve tablo adları "arşiv" kalır (doğru terim), sadece
KULLANICIYA GÖSTERİLEN metin "silindi" olur.

**Kişi düzenleme komutları (yeni):**
"{kişi} düzenle", "{kişi} adlı kişiyi düzenle", "{kişi} isim değiştir/
düzenle/değişiklik", "{kişi} telefon düzenle/değişiklik", "{kişi} ilçe {X}
yap", "{kişi} isim {yeni} yap", "{kişinin} ismi {yeni} yap" → düzenleme.

İki mod:
1. NET komut ("mehmet ilçe ahmetbeyler yap", "mehmetin ismi akif yap") →
   o alanı güncelle, onay iste veya direkt yap + "güncellendi".
2. BELİRSİZ ("mehmet düzenle", "mehmet isim değiştir") → ne düzenleneceğini
   sor: [Ad soyad] [Telefon] [İl] [İlçe] [Adres] butonları, seçince yeni
   değeri iste, güncelle.

Düzenlenebilir alanlar: full_name, phone, city, district, address, note.
Kişi eşleştirme güvenlik kurallarıyla (çoklu kişi → hangisi?). Değişiklik
audit_log'a (kim, ne zaman, alan, eski→yeni). Yazım hatası toleransı:
"düzenlee", "dğeişiklik" gibi hataları da yakala.

### Düzenleme mesajları — eski değer göster, ne değişti belirt (2026-07-30)

**Güncelleme sonucu net olsun:** "güncellendi" yetmez, hangi alan ne oldu
söylensin:
- "Mehmet Kaya'nın ilçesi Ahmetbeyler olarak güncellendi."
- "Mehmet Kaya'nın telefonu 555... olarak güncellendi."
- İsim değişiminde: "Mehmet Kaya'nın adı Akif olarak güncellendi."
Format: "{kişi}'nin {alan} {yeni değer} olarak güncellendi."

**Düzenlerken eski değeri göster:** Belirsiz düzenleme menüsünden bir alan
seçilince, yeni değeri sormadan ÖNCE mevcut değeri göster:
- "İlçe bilgisi: Bergama
   Yeni ilçe için yazın:"
- "Telefon: 535556578
   Yeni telefon için yazın:"
- Alan boşsa: "İlçe bilgisi: (boş)\nYeni ilçe için yazın:"
Kullanıcı eski değeri görüp ona göre yenisini yazar. Her alan için geçerli
(ad soyad, telefon, il, ilçe, adres, not).

## Doğal Dil Anlama — Regex ve LLM

### LLM son çare, regex birincil (2026-07-27 mimari kararı)

Test sonucu: LLM işlemcide çok yavaş (2 dk) ve hatalı — "aldım"ı borç
sandı, "3bin"i 3.000.000 yaptı, "verdim"i anlamadı. Bu kalıplar DÜZENLİ,
regex'le anında ve DOĞRU çözülmeli. LLM'e sadece gerçekten serbest/belirsiz
cümlelerde başvurulur.

**Kural: Mümkün olan HER kalıbı regex'e ekle. LLM son çare.**
Kayıt, tahsilat, bakiye, liste, rapor komutlarının yaygın tüm biçimleri
regex'te olmalı. LLM yalnızca regex'in tamamen çözemediği (None döndürdüğü)
serbest ifadeler için devreye girer.

**Regex'in kesin çözmesi gerekenler (LLM'e gitmemeli):**
- Yön: "kişiDEN aldım/tahsil ettim" = TAHSİLAT (para bana geldi).
  "kişiYE verdim/borç/sattım/çıktı" = BORÇ (mal/para ona gitti).
  "kişi X aldı" (3. şahıs) = BORÇ (o aldı, bana borçlandı).
  Bu ayrım koddadır, LLM'e bırakılmaz — para yönü kritik.
- Türkçe sayı: "3bin"/"3 bin"=3000, "5bin"=5000, "10bin"=10000,
  "yüz"=100, "ikiyüz"=200, "bin beşyüz"=1500, "2buçuk"=2.5.
  Bitişik/ayrık yazımlar, "bin/yüz/milyon" çarpanları. Kod parse eder.
- Para vs adet: "3bin lira/tl" = tutar, "20 balya/kilo" = adet+birim.

**Donanım stratejisi (provider seçimi config'ten):**
- Yerel Ollama (laptop/sunucu işlemci): yavaş yedek.
- 2080 Super vLLM (host): hızlı birincil, hazır olduğunda.
- Öncelik: 2080 Super çalışıyorsa onu kullan; timeout/erişilemezse yerel
  Ollama'ya düş; o da olmazsa regex + "elle gir". Katmanlı fallback.
- Kod değişmez, provider config'ten seçilir (mevcut soyutlama).

### Faz 4 — LLM (Ollama, sonra vLLM)

Regex'in sınırına gelindi. "anlamazsa sor", serbest cümle, esnek varyasyon
= LLM işi. Kural parser KALDIRILMAZ — hızlı yol olarak kalır, LLM fallback
olur.

**Akış:** mesaj → kural parser dener (15 ms) → çözerse kaydet → çözemezse
LLM'e gönder (Ollama/vLLM) → LLM JSON çıkarır → BİZİM KOD doğrular
(kişi pg_trgm, ürün catalog, tutar sayı mı) → net ve güvenliyse kaydet,
değilse "bunu mu demek istediniz?" diye sorar. LLM asla son sözü söylemez,
öneri sunar; karar kod + kullanıcıda.

**Provider soyutlaması (kritik):**
- LLMProvider Protocol: parse(text) -> ParsedIntent. Somut sınıflar:
  OllamaProvider (base_url, model), sonra VllmProvider, ve RuleProvider
  (her zaman açık taban).
- Hangi provider kullanılacağı config'ten (inference.yml veya .env), koddan
  DEĞİL. Laptop→Ollama, 2080 Super→vLLM geçişi tek satır config.
- Model/URL .env'den: LLM_PROVIDER=ollama, OLLAMA_URL=http://localhost:11434,
  LLM_MODEL=qwen2.5:7b. Ollama yoksa/erişilemezse sistem kural parser +
  "elle gir" ile çalışmaya devam eder, ÇÖKMEZ.

**Prompt:** few-shot (örnekli) sistem prompt'u. Kişi adı eklerini temizle,
birim ile para birimini karıştırma, emin olmadığını null bırak. Ollama
--format json ile JSON zorlanır. Prompt kod içinde sabit bir dosyada
(app/services/llm_prompt.py veya .txt), kolay düzenlenebilir.

**Güven ve onay:** LLM çıktısındaki her alan kod tarafından doğrulanır.
Kişi eşleşmezse veya çok aday varsa → sor (mevcut NEEDS_CONFIRMATION).
Ürün/tutar/tür belirsizse → "bunu mu demek istediniz: ... ?" + Evet/düzelt.
Kural parser güvenli sayılır (doğrudan kaydeder), LLM sonucu düşük güven
sayılır (kritik alanlarda onay ister).

**Hız:** işlemcide 3-8 sn/cümle (model bellekte kalırsa). 2080 Super +
vLLM'de ~0.3 sn. Kural parser'ın çözdüğü mesajlar LLM'e hiç gitmez, o
yüzden çoğu mesaj yine hızlı.

**Ses (Faz 5, sonra):** aynı GPU'ya faster-whisper. Ses → metin → yukarıdaki
akış. Whisper de provider (STTProvider) arkasında. GPU gelince eklenecek.

### KRİTİK — LLM isim bozuyor (2026-07-27)

Ciddi buglar tespit edildi, hepsi LLM'in Türkçe ek temizlemesinden:
1. "mehmetten 5000 aldım" → LLM ismi "mehtap" olarak uydurdu. İSİM
   DEĞİŞTİRME kabul edilemez, para/kişi güvenliği ihlali.
2. "ahmetin durumu ne" → "ahmetin" eki temizlenemedi, kişi bulunamadı.
   Ama "ahmet in" (boşluklu) çalıştı. Tutarsız.
3. Aynı isim bazen bulunuyor bazen "hangisini?" soruyor — kararsız.

**Kural: İsim temizlemeyi LLM'E BIRAKMA.** LLM ham metinden ismi
çıkarabilir ama Türkçe ekleri (iyelik -in/-ın/-nin, ayrılma -den/-dan/-ten/
-tan, yönelme -e/-a/-ye/-ya) KOD tarafından temizlenir, LLM tarafından
değil. LLM'in döndürdüğü isim, kod içinde normalize edilip pg_trgm ile
eşleştirilir. LLM asla harf ekleyip çıkaramaz (mehmet→mehtap gibi).

**Uygulama:**
- app/services/catalog.py veya yeni bir isim-normalize modülü: Türkçe ek
  soyma fonksiyonu. Kişi adının son kelimesindeki yaygın ekleri sök
  (ahmetten→ahmet, mehmetin→mehmet, aliye→ali). Ek listesini kapsamlı tut.
- intent_resolver kişi eşleştirmede bu normalize'i HEM regex HEM LLM
  sonucuna uygular. LLM ne döndürürse döndürsün, kod ekini temizler.
- Ünsüz yumuşaması dikkat: "mehmedin"→"mehmet" (d→t), "ahmedin"→"ahmet".
- Eşleştirme yine pg_trgm güvenlik kurallarıyla (soyad ayrımı, çoklu aday).

### Kişi bilgi sorgusu + hitap kelimeleri

**"{kişi} bilgi ver / bilgileri / kim / kimdir"** → kişinin kartını göster:
ad soyad, telefon, il/ilçe, güncel bakiye, açık kalemler. Bakiye
sorgusundan farkı: iletişim/konum bilgisi de gösterilir. Yeni niyet:
person_info. Kişi eşleştirme güvenlik kurallarıyla (çoklu aday → sor).

**Hitap kelimeleri isimden ayıklanır:** "abla, abi/ağabey, bey, hanım,
amca, dayı, teyze, hala, usta, hoca, efendi, kardeş" gibi hitaplar isim
değildir. "esma abla" → "esma", "ahmet usta" → "ahmet", "mehmet bey" →
"mehmet". Bu ayıklama kişi adı çıkarımında (hem regex hem LLM sonrası)
yapılır, "hesabının/durumu" gibi bağlam kelimeleriyle aynı temizleme
katmanında. Dikkat: gerçek isimle karışmasın (nadiren isim olabilir ama
hitap olarak kullanımı baskın; sona geldiğinde ayıkla).

### DÜZELTME — "bilgi ver" belirsiz, SOR (2026-07-28)

Önceki karar "bilgi ver → kişi kartı" yanlıştı. "bilgi ver" belirsiz:
kullanıcı bakiye/borç de kastedebilir, iletişim bilgisi de. Bot VARSAYMAZ,
sorar (anlamıyorsa sor ilkesi):

"{kişi} bilgi ver / bilgi / bilgileri" → belirsiz → bot sorar:
  "Ne bilgisi?" + butonlar:
  [Bakiye / borç]  [Kişi bilgileri]  [Ekstre (PDF)]
Seçime göre:
  - Bakiye/borç → mevcut bakiye sorgusu (bakiye + açık kalemler)
  - Kişi bilgileri → ad/telefon/il/ilçe kartı
  - Ekstre → kişi ekstresi PDF

Net niyetler doğrudan çalışır (sormadan):
  "{kişi} bakiyesi/borcu/durumu" → bakiye
  "{kişi} telefonu/numarası/adresi/nerede" → kişi bilgileri
  "{kişi} ekstresi/dökümü" → ekstre PDF
Sadece belirsiz "bilgi" için seçim sorulur.

### Tek mesajda birden çok istek

Kullanıcı bir mesajda birden çok işlem yazabilir ("mehmetten 5000 aldım
ali veliye 500 mal gitti"). Sistem:
- Mesajı cümlelere/işlemlere böl (satır sonu, "ve", ayrı fiiller).
- Her işlemi SIRAYLA işle, her biri için AYRI cevap/onay gönder.
- İlk işlemi yap + bilgilendir, sonra ikinciyi yap + bilgilendir.
- Bölme belirsizse tek işlem sayıp normal akışa devam et (aşırı bölme
  yapma, yanlış bölmektense tek bırak).

### Tek mesajda çoklu istek + ürün yazım düzeltme — Grup 5 (2026-07-30)

**Tek mesajda birden çok işlem:**
Kullanıcı bir mesajda birden çok işlem yazabilir:
"mehmetten 5000 aldım aliye 500 mal gitti" veya satır satır.
Sistem:
- Mesajı işlemlere böl: satır sonu (\n), " ve ", ya da art arda gelen
  ayrı isim+işlem kalıpları.
- Her işlemi SIRAYLA işle, her biri için AYRI cevap/onay gönder.
- İlki: işle + bilgilendir. Sonra ikincisi: işle + bilgilendir.
- Bölme belirsizse (emin değilse) TEK işlem say, normal akış. Aşırı bölme
  yapma — yanlış bölmektense tek bırak (yanlış bölme para hatası yapar).
- Her işlem kişi eşleştirme güvenlik kurallarından ayrı ayrı geçer; biri
  "hangisi?" sorarsa o cevaplanınca devam.
- Çoklu tespit yalnızca NET, kesin ayrılabilen durumlarda. Şüphe → tek.

**Ürün yazım düzeltme (fuzzy):**
"samaan", "saman 15", "smaan" gibi hatalı ürün adları YENİ ürün olarak
kaydedilmemeli. Mevcut catalog fuzzy eşleştirme (pg_trgm) ile:
- Yeni ürün adı, mevcut bir ürüne yüksek benzerlikteyse (örn "samaan"→
  "saman") KULLANICIYA SOR: "samaan → saman mı demek istediniz? Evet/Hayır/
  Yeni ürün". Sessizce bağlama (yanlış ürün tehlikeli) ama sessizce yeni
  ürün de açma (çöp birikir).
- "saman 15" gibi içinde sayı/çöp olan adları temizle veya sor.
- Eşik: SIMILARITY_STRONG üstü → öneri sun, altı → yeni ürün onayı iste.
- Fuzzy eşleştirme KASITEN otomatik değil, öneri. Kullanıcı onaylar.

### Çoklu istek — kalıcı istek kuyruğu (2026-07-31, Grup 6)

Çoklu istek DB tabanlı kalıcı kuyrukla çalışır. Bellek (chat_data) yerine
tablo kullanılır ki bot durup soru sorsa, internet kopsa, bot yeniden
başlasa bile kaldığı yerden devam etsin.

**pending_requests tablosu:**
- id, chat_id, batch_id (aynı mesajdan gelen istekler aynı batch)
- raw_text (o işlemin metni), sira_no (batch içindeki sıra)
- durum: 'beklemede' | 'işleniyor' | 'tamamlandı' | 'başarısız' | 'iptal'
- sonuc (işlendiyse özet), hata (başarısızsa sebep)
- created_at, updated_at

**Akış:**
1. Çoklu mesaj gelince split_into_requests ile parçalara ayır.
2. Her parçayı pending_requests'e 'beklemede' olarak yaz (batch_id ortak).
3. Sırayla işle: ilk 'beklemede' isteği al → 'işleniyor' yap → process.
   - NET sonuç → kaydet, 'tamamlandı' işaretle, sonraki isteğe geç.
   - ONAY gerekiyorsa (hangisi/evet-hayır/ürün/silme) → o isteği 'işleniyor'
     bırak, kullanıcıya sor, DUR. Kullanıcı cevaplayınca o isteği bitir
     ('tamamlandı'), SONRA kuyruktaki bir sonraki 'beklemede'ye otomatik geç.
   - LLM gerekiyorsa (Ollama) → çağır; Ollama kapalıysa/erişilemezse o
     isteği 'başarısız' (hata: "anlaşılamadı") işaretle, DİĞERLERİNE devam et
     (biri LLM'e takılınca hepsi durmasın).
4. Batch bitince özet: "4 işlemden 3 tamamlandı, 1 anlaşılamadı: '...'".

**Dayanıklılık:** bot yeniden başlarsa, 'beklemede'/'işleniyor' kalan
istekler DB'de durur; istenirse devam ettirilebilir (ilk sürümde en azından
kaybolmaz, elle görülebilir). Onay bekleyen istek chat_data + DB'de izlenir.

**Önemli:** her istek kişi eşleştirme ve para güvenlik kurallarından AYRI
geçer. Bir istek yanlış giderse diğerlerini etkilemez. Kuyruk sıralı işler
(paralel değil) ki onay akışları karışmasın.

### Zeka + hız düzeltmeleri (2026-08-31)

**1. Yeni kişi eklerken LLM'e sorulmaz (hız).** `create_person` niyetinde
pg_trgm hiç aday bulamazsa artık LLM'e "en yakın kişi kim?" diye
SORULMAZ (`intent_resolver.NO_LLM_SUGGESTION_KINDS`). Amaç zaten yeni bir
kişi açmak; "böyle biri yok" cevabı kesin ve yeterli. Cevap ~5 sn yerine
<1 sn geliyor. LLM'in isim önerisi yalnızca MEVCUT bir kişiyle işlem
yapılırken (borç/tahsilat/bakiye/ekstre) devrede kalır.

**2. "sil" her zaman kişi silme değildir.** Cümlede silme fiiliyle birlikte
para/mal bağlamı da varsa ("furkan duman 20 saman borcunu ödedi sil") niyet
BELİRSİZ sayılır: `kind="delete_ambiguous"`. Bot/web hiçbir şey yapmadan üç
butonla sorar — [Tahsilat gir] [Kişiyi sil] [İptal].
- "Kişiyi sil" → normal yazarak-onay akışı (kısayol YOK).
- "Tahsilat gir" → silme fiili ayıklanmış metin (`parser.strip_delete_words`)
  NORMAL akıştan (regex → gerekirse LLM) yeniden geçirilir; ikinci bir
  mantık yazılmaz.
- Para/mal bağlamı yoksa ("furkanı sil", "furkanın hesabını sil") davranış
  aynen eskisi gibi: doğrudan `archive_person`.
- İsim güvenle çıkarılamıyorsa uydurulmaz, cümle LLM'e devredilir.
Eskiden bu cümlede TÜM cümle kişi adı sanılıyordu; para güvenliği açısından
"sessizce kişi silme sanmak" kabul edilemez.

**3. Toplam bakiye niyeti.** "tüm bakiye", "toplam borç", "toplam alacak",
"genel bakiye", "sistemdeki toplam borç", "total borç", "güncel toplam" →
`kind="total_balance"`: defterin TAMAMININ özeti (kişi sayısı, toplam
alacak, toplam borç, net). Eskiden "tüm"/"total" kişi adı sanılıp "defterde
yok" deniyordu. Sorgu `queries.total_balance` — bakiye yine kolonda
tutulmaz, `list_persons_with_balance`ın SUM'ından türetilir.
Kalıp kasten DAR: cümlenin tamamı nitelik/isim/dolgu kelimesi olmalı, araya
kişi adı karışıyorsa ("furkan toplam borç") bu tek kişinin bakiyesidir.
"genel durum" bilerek dışarıda — o hâlâ genel durum RAPORU (PDF).

### Anlama kapasitesi genişletme (2026-08-31, 9 boşluk)

Test haritası regex'in kaçırdığı 9 kalıp gösterdi. Hepsi REGEX'e eklendi
(CLAUDE.md > "LLM son çare, regex birincil"); LLM prompt'una da few-shot
örnek konuldu ki kullanıcı kalıbı regex eşiğinin dışında bozarsa aynı
niyete varılsın.

**Regex'e eklenenler (`parser.py`):**
- **Yazım toleranslı liste komutları.** "kişler", "ksiler", "kişileer",
  "kişilerr" → `list_all`. Sözlük değil, Damerau-Levenshtein fuzzy
  (`_is_list_all_word`, düzenleme tetikleyicileriyle aynı yöntem). Kasten
  DAR: ilk harf aynı, uzunluk farkı ≤2, mesafe 2 yalnızca ≥6 harfte —
  "kiler"/"işler" gibi gerçek kelimeler liste komutu sanılmaz.
- **"{X} listesi".** "kişi/kişiler/müşteri listesi" → `list_all`,
  "borçlu listesi" → `list_debtors`, "alacaklı listesi" → `list_creditors`.
  (Eskiden "borçlu listesi" → `balance_query(person="listesi")` oluyordu.)
- **"borçlandı" = borç.** Fiilin kendisi yönü kodluyor, `DEBT_WORDS`'te.
- **"borcunu ödedi/kapattı" = TAHSİLAT, bakiye sorgusu değil.**
  `_strip_debt_closing` anahtar kelimeyi düşürür, kalan cümle normal
  tahsilat akışından geçer. Eskiden "bakiye kelimesi + kayıt fiili"
  çelişkisi sayılıp her seferinde (yavaş) LLM'e devrediliyordu.
- **"{isim} kaç para" / "kaç lira"** → `balance_query` (`BARE_BALANCE_TAILS`,
  "ne kadar" ile aynı kuyruk mantığı).
- **"{ilçe}den kimler var" / "{ilçe}deki kimler"** → `list_district`.
  Ayrılma/bulunma hâli eki (-dan/-den/-da/-de) YALNIZCA bu kalıpta soyulur,
  `_district_from_word`'e eklenmez — tek başına "aydan"/"sudan" gibi bir
  isim yanlış ilçeye dönerdi.

**Tutar söylenmemiş borç kapanışı.** "ali borcunu ödedi" — niyet net, tutar
yok. Tutar UYDURULMAZ: `ParsedIntent.close_debt` işaretlenir, kişi
çözülünce `message_processor` güncel bakiyeyi TEKLİF eder ve kullanıcıya
onaylatır (`ProcessOutcome.CLOSE_DEBT_CONFIRM`). Onay makinesi LLM
önizlemesiyle aynıdır (aynı `llm_confirm` bekleyen kaydı, aynı
Evet/Düzelt/İptal) — ikinci bir onay akışı yazılmaz. Borcu sıfır/negatifse
sahte tahsilat yazılmaz, kişinin güncel durumu gösterilir. LLM tarafında da
tutarsız her `payment` aynı şekilde `close_debt` sayılır.

**Ürün/stok sorgusu: tanınır ama DESTEKLENMEZ.** "toplam kaç saman
satıldı", "ne kadar arpa var", "saman fiyatı", "arpa stoğu" →
`kind="product_query"` → `ProcessOutcome.PRODUCT_QUERY_UNSUPPORTED`. Defter
cari hesap tutar; stok tutmaz, fiyat listesi de bağlayıcı değildir
(kural 4). Niyet yine de tanınır: tanınmasa cümle bir kişi adı ya da bir
kayıt sanılırdı. Bot açıkça "ürün sorguları henüz yok" der — sessizce
yanlış cevap vermez. Gerçek ürün/stok raporlaması sonraki iş.

**Prompt bütçesi.** `llm_prompt.py` ~9,5 bin karakter; her yeni örnek
soğuk model süresinden yenir. Yeni bir kalıp önce regex'e eklenmeye
çalışılır — regex'in çözdüğü cümle LLM'e hiç gitmez.

### Koşan format — hızlı adet girişi (2026-09-05)

Kullanıcının hızlı giriş biçimi. **Yalnızca MAL adedi hakkındadır, TL değil.**

    "70-20-50"  =  70 vardı, 20 değişti, 50 oldu

**Ayraç salt görseldir.** `-`, `+`, `*` aynı şeyi anlatır ("70-20-50",
"70+20+50", "70*20*50", "70 - 20 - 50"). Matematik işareti DEĞİL; parser
tokenlaştırmadan önce hepsini tek kanonik biçime toplar (`_collapse_running`).

**Yön ilk-son karşılaştırmasından çıkar.** İlk > son (azalış) = mal kişiye
gitti = BORÇ. İlk < son (artış) = mal geri geldi = TAHSİLAT. Cümledeki bağlam
kelimesi ("tahsilat"/"aldı"/"borç") yalnızca TEYİT eder; çeliştiğinde sayılar
kazanır — kullanıcı üç sayıyı bilerek yazmıştır.

**Kaydedilen sayı DEĞİŞİMDİR (fark).** "70-20-50" -> 20 balya. İlk ve son
sayı kullanıcının kendi doğrulaması içindir, deftere yazılmaz. Ürün
söylenmezse varsayılan "saman".

**Matematik kontrolü (yalnızca İÇ tutarlılık).** Orta sayı |ilk − son|'a EŞİT
olmalı. Değilse HİÇBİR ŞEY kaydedilmez, sorulur: "Sayılar tutmuyor: 70 → 50
için fark 20 olmalı ama 25 yazdınız." + [Fark 20 olsun] [İptal]. "Fark 20
olsun" denince cümle `parser.correct_running_text` ile düzeltilip NORMAL
akıştan yeniden geçirilir — ikinci bir kayıt mantığı yazılmaz (aynı desen:
"Tahsilat gir" seçeneği). "25 olsun" diye bir seçenek YOK: orta sayı doğruysa
hangi ucun yanlış olduğu bilinemez, kullanıcı yeniden yazar.
**Sistemdeki mevcut saman bakiyesiyle karşılaştırma KASTEN yapılmaz** (sonraki
iş) — yalnızca cümlenin kendi içindeki tutarlılık bakılır.

**TL ayrı girilir.** Aynı cümlede varsa kullanılır ("... saman 5000 tl").
Yoksa ve ürün saman (söylenmediyse zaten saman), birim balya ise tutar
varsayılan saman fiyatından hesaplanır ve kayıt doğrudan yazılır (onay
mesajı fiyatı gösterir; artış = tahsilat ise önce sorulur — bkz.
"Varsayılan saman fiyatı"). Diğer ürünlerde tutar UYDURULMAZ ve fiyat
listesinden de türetilmez (kural 4) — `ProcessOutcome.RUNNING_AMOUNT_NEEDED`
ile yazarak sorulur ("70 → 50 · 20 kilo arpa borç / Tutar kaç TL?"). Cevap Türkçe sayı olarak çözülür
("5000", "5 bin", "5000 tl"); çözülemezse kayıt yapılmaz, soru tekrarlanır.
Kişi belirsizse önce her zamanki "hangisi?" sorulur, tutar sorusu ondan
sonra gelir (`running` bayrağı bekleyen kayıtlarda taşınır).

**Yanlış tetiklenmeye karşı altı katman** (parser.py > "koşan format"):
tam üç parça (dördüncü sayı gelirse kalıp hiç eşleşmez — telefon elenir),
baştaki sıfır yasak, parça başına en çok 6 hane, tarih biçimleri (gg-aa-yyyy /
yyyy-aa-gg) iç tutarlılık sağlanmıyorsa elenir, mesajda tam olarak BİR üçlü,
kişi adı zorunlu. Ayrıca `parse()` içinde bu kontrol TÜM sorgu/komut
kalıplarından SONRA gelir: "sil", "düzenle", "rapor", "listele" her zaman
kazanır. Çıplak "70-20-50" artık bir kişi ARAMASI da sayılmaz — harf
içermeyen kelime isim/ilçe olamaz (`_try_single_word_search`).

**Nerede çalışır:** parser (regex + aritmetik, LLM'e GİTMEDEN), Telegram
botu, web sohbeti ve borç/tahsilat ekleme FORMU. Formda adet alanına
"70-20-50" yazılır; `web/src/lib/running.ts` (parser.py'nin ikizi — kural
değişirse İKİSİ birden değişir) farkı çözer, ilk → son özetini gösterir,
matematik tutmuyorsa Kaydet'i kilitler, yön modalla çelişiyorsa uyarır
(engellemez: modalı kullanıcı seçti).

Testler: `tests/test_parser.py` > "koşan format", `tests/test_chat_api.py`,
`tests/test_bot_running_format.py`, `web/src/lib/running.test.ts`.

### Varsayılan saman fiyatı (2026-09-11)

Kural 4'ün TEK istisnası. Saman fiyatı dalgalı ama gün içinde sabit; her
kayıtta aynı tutarı yazdırmak yerine ayarlanabilir bir balya fiyatı tutulur.
**Esnek:** tutar yazılırsa kullanıcınınki, yazılmazsa varsayılan fiyattan.
Kod: `app/services/saman_fiyat.py`.

**Nerede durur, nasıl değişir.** `settings.saman_birim_fiyat` (tohum
"180.00": `db/schema.sql` + `db/migrations/013_saman_birim_fiyat.sql`). Sol
panelde metrik kartlarının altında "Güncel saman fiyatı · 180,00 ₺ / balya"
kartı; kartın tamamı tek dokunma hedefi, "Değiştir" küçük bir modal açar
(`SamanFiyatModal`). API: `GET/POST /api/settings/saman-fiyat` (JWT).
Değişiklik `set_saman_price`tan geçer: 0 < fiyat ≤ 1.000.000, kuruşa
yuvarlanır, audit_log'a eski → yeni yazılır (`set_saman_price`). Genel
`PUT /api/settings/saman_birim_fiyat` da AYNI doğrulama + audit yolundan
geçer: tutar hesaplayan bir değer denetimsiz yazılamaz. Satır hiç yoksa
(migration çalışmamış) 180 kullanılır; satır bozuksa (sayı değil, ≤ 0) HİÇ
hesap yapılmaz, sistem eski davranışa (tutarı iste) düşer.

**Ne zaman uygulanır** (`is_candidate` parser çıktısında, `applies` çözülmüş
kayıtta): borç/tahsilat, tutar YOK, adet > 0, ürün saman, birim yazılmamış
ya da balya. "20 kilo saman" → uygulanmaz (fiyat balya başına, çarpılmaz).
Arpa vb. → uygulanmaz, tutar eskisi gibi istenir. Hesap `ledger.price_total`
(Decimal, kuruş). Birim yazılmadıysa resolve aşamasında balya sayılır —
saman kataloğda yoksa "adet" birimiyle açılmasın.

**Hızlı mı, sor mu?** (`ProcessOutcome.SAMAN_PRICE_CONFIRM`)
- **NET → doğrudan kaydedilir:** yön fiili + saman + adet ("furkan 20 saman
  aldı", "alper 30 saman borç", "alper altınpınar 20 saman aldı") ve koşan
  format ("ahmet 70-20-50"). Onay mesajında "Birim fiyat: 180,00 TL/balya
  (varsayılan saman fiyatı)" satırı durur; yanlışsa 60 sn "Geri al".
- **SOR → Evet/Düzelt/İptal** (LLM önizlemesiyle AYNI makine, `llm_confirm`):
  - yön fiili yok — "furkan 20 saman" → "Furkan'a 20 balya Saman (180,00
    TL/balya = 3.600,00 TL) borç ekleyeyim mi?"
  - ürün yok — "furkan 20", "furkan 20 balya aldı" → "Furkan'a 20 balya
    Saman borç mu demek istediniz? (180,00 TL/balya = 3.600,00 TL)"
  - tahsilat ("furkandan 20 saman aldım"): kişinin elden verdiği parayı
    biz bilemeyiz, hesap yalnızca teklif edilir.
  - niyet LLM'den geldiyse.
  Düzelt → "doğrusunu yazar mısın?"; kullanıcı "furkan 20 saman 4000 tl" yazar.
- Varsayım bayrakları (`assumed_product`, `assumed_kind`) bekleyen kayıtlarda
  TAŞINIR: "furkan 20" iki Furkan'a uyuyorsa önce "hangisi?", seçilince saman
  sorusu YİNE sorulur (yeni kişi akışında da). Bayrak düşerse varsayım sessizce
  kaydedilirdi.

**Fiilsiz kalıp kasten dar** (`parser._try_bare_saman_record`): isim + TEK
sayı + isteğe bağlı "balya" + isteğe bağlı "saman", başka kelime yok.
"furkan 20 arpa", "furkan 0532..." (baştaki sıfır), "furkan 20 saman 15",
"rapor 20" eşleşmez. Fiilli çıplak sayı hâlâ TL'dir ("furkana 3000 verdim"
→ 3.000 TL, "Para vs adet"). Çoklu istek bölücüsü fiilsiz parçayı bölme
sınırı SAYMAZ (`assumed_kind`); sayılsaydı "ahmet yılmaz 20 balya saman aldı
15000 tl" → "ahmet yılmaz 20" + "balya saman aldı ..." diye bölünürdü.

**Form (DebtModal).** `resolveGoods(..., samanPrice)`: ürün saman ve birim
balya ise varsayılan fiyat ürünün kayıtlı fiyatını ezer (`priceSource:
"saman"`) ve tik AÇIK başlar (`defaultAutoPrice`): tutar alanı adet × fiyatla
dolar, salt okunur. Başka tutar için tik kapatılır; tutar elle yazılmışsa ya
da cümlede "... 5000 tl" varsa tik kapalı kalır — kullanıcının tutarı kazanır.
PaymentModal, EditTxModal ve AddEntry değişmedi.

Testler: `tests/test_saman_fiyat.py` (ayar, audit, API, hesap, teyit),
`tests/test_parser.py` > "varsayılan saman fiyatı", `tests/test_chat_api.py`
> "varsayılan saman fiyatı", `tests/test_bot_saman_fiyat.py`,
`tests/test_message_splitter.py`, `web/src/lib/goods.test.ts`.

## Raporlama

### Faz 4b — PDF Raporlar

Üç rapor türü, ortak sabit şablon (hepsi aynı tarzda). reportlab + DejaVu
font (Türkçe). Kaynak taslak onaylandı, app/services/report.py olacak.

**Şablon (ReportDoc):** lacivert üst bant (#1e3a5f) — sol: işletme adı
(settings.business_name) + "Cari Hesap Raporu", sağ: rapor başlığı + alt
başlık. Kişi ekstresinde alt başlık = kişi adı, BÜYÜK ve beyaz (vurgu="alt").
Alt bilgi: oluşturulma tarihi, "Hesaplık", sayfa no. Renkler: NAVY başlık,
BORC #c0261d kırmızı, ALACAK #1d6b3c yeşil, ZEBRA satır. Tutarlar Türkçe
biçim (1.500,00 ₺). Az ama anlamlı renk.

**Üç rapor:**
1. Günlük: bugün (veya verilen gün) kaydedilen hareketler. Sütun: Saat/
   Kişi/Ürün/Miktar/Tür/Tutar. Özet kutuları: kayıt sayısı, toplam borç,
   toplam tahsilat, net.
2. Kişi ekstresi: bir kişinin tüm hareketleri + yürüyen bakiye sütunu.
   Üstte kişi bilgi şeridi (ad, tel, ilçe/il) + güncel bakiye + açık
   kalemler. Sütun: Tarih/Açıklama/Birim fiyat/Tür/Tutar/Bakiye.
3. Genel durum: tüm kişiler, borçlu çoktan aza sıralı. Sütun: Kişi/İlçe/
   Açık kalem/Durum/Bakiye. Özet: kişi sayısı, toplam alacak, toplam borç,
   net alacak.

**Nereden:**
- Telegram: "rapor ver" → günlük/genel butonla seç. "{isim} ekstresi" veya
  "{isim} raporu" → kişi ekstresi (kişi eşleştirme güvenlik kurallarıyla).
  PDF dosyası + KISA metin özet birlikte gönderilir.
- Web: her rapor için buton (ana ekranda veya kişi detayında), tıkla → PDF
  indir. Kişi ekstresi kişi detayında, diğerleri ana ekranda/ayarlarda.

**Teknik:** reportlab pyproject.toml'a. Font yolu sabit değil, birkaç
olası yol denensin (DejaVu Fedora/Debian'da farklı yerde olabilir), yoksa
anlamlı hata. PDF bellekte üretilip (BytesIO) Telegram'a gönderilir ve/veya
web'de indirilir; diske yazmak şart değil.

### Rapor komutları — gelişmiş anlama (regex + LLM)

Kullanıcı raporu çok farklı şekillerde ister; hepsi anlaşılmalı, anlaşılmazsa
LLM devreye girmeli.

**Üç rapor niyeti:**
- report_general: "genel rapor", "tüm zamanların raporu", "herkesin durumu",
  "genel durum raporu", "bütün müşteriler raporu" → genel durum PDF
- report_daily: "günün raporu", "bugünün raporu", "gün raporu", "bugün ne
  yaptık", "günlük rapor", "bugünkü hareketler" → günlük PDF
- report_person: "{isim} ekstresi/raporu/dökümü/hesap dökümü" → kişi ekstresi
- report_menu: sadece "rapor" / "rapor ver" (hangisi belli değil) → günlük mü
  genel mi diye butonla sor

**Regex:** yaygın kalıpları ve Türkçe ekleri tanı (rapor/raporu/raporunu,
ekstre/ekstresi/ekstresini, döküm/dökümü). Kesin çözülenler doğrudan, "rapor"
tek başına → menü.

**LLM:** regex çözemezse rapor niyetlerini de LLM tanımalı. llm_prompt.py'ye
rapor örnekleri eklensin: "bana genel bir rapor çıkar" → report_general,
"bugün neler olmuş göster" → report_daily, "ahmetin hesap dökümünü ver" →
report_person(ahmet). LLM islem alanına "rapor" ekle, tür (genel/gunluk/kisi)
ve kişi (varsa) döndürsün. Belirsizse ("rapor" tek başına) → menü.

Kişi ekstresi her zaman kişi eşleştirme güvenlik kurallarından geçer.

## Yedekleme, Geri Yükleme ve Canlıya Alma

### Yedekten geri dönme (komut satırı)

    set -a; source .env; set +a
    restic snapshots                       # ID seç
    just backup                            # önce mevcut hâli yedekle
    restic dump <ID> /hesaplik.dump > /tmp/geri.dump
    docker compose exec -T db pg_restore -U hesaplik -d hesaplik \
      --clean --if-exists --no-owner < /tmp/geri.dump

Tüm veritabanını o ana geri sarar. Bu el yordamı her zaman geçerli kalır
(felaket anında panel açılmayabilir).

### Telegram'a yedek — restic'e EK (2026-09-11)

Restic deposu şimdilik sunucunun kendi diskinde; sunucu/disk giderse yedek
de gider. Ek güvence: `scripts/telegram-yedek.sh` günde iki kez (04:00 ve
06:00, `Europe/Istanbul`) tüm veritabanını gzip'li düz SQL olarak admin'in
kişisel Telegram'ına gönderir (`TELEGRAM_ADMIN_CHAT_ID`). Restic'e dokunmaz,
yanında çalışır.

- **Düz SQL (`pg_dump --clean --if-exists --no-owner`), `-Fc` değil:** dosya
  telefondan bile okunur, geri yüklemek için yalnızca `psql` yeter; `--clean`
  sayesinde schema.sql'in kurduğu taze volume'e de yüklenir.
- **Gönderilmeden önce doğrulanır:** `gzip -t` + pg_dump'ın "dump complete"
  bitiş satırı. Yarım döküm "yedek alındı" diye gitmez.
- **45 MB sınırı** (Telegram bot API 50 MB): aşarsa GÖNDERİLMEZ, admin'e
  "Yedek 50MB'ı aştı, alternatif gerekli" yazılır. Sessiz başarısızlık yok:
  her hata admin'e sendMessage ile gider, çıkış kodu 1.
- **`.env` source edilmez** (bcrypt `$2b$...`, bkz. `just migrate`): yalnızca
  POSTGRES_USER/DB, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_ID grep'le okunur.
  Token curl'e komut satırından değil stdin'den verilir (`ps`'te görünmesin).
- Döküm yalnızca 0700 izinli geçici dizinde durur, her durumda silinir.
- Zamanlayıcı `deployment/hesaplik-telegram-yedek.{service,timer}`; servis
  `COMPOSE_FILE=docker-compose.prod.yml` ile üretim yığınını hedefler.
  Crontab alternatifi ve geri dönme komutu `deployment/README.md`'de.

### Panelden geri yükleme — "Yol A": panel İSTER, host UYGULAR (2026-08)

API container'ı veritabanını geri YÜKLEYEMEZ: içinde `docker`/`compose` yok,
restic deposu salt okunur bağlı. Bu yüzden iş ikiye ayrıldı ve **API'de
pg_restore çalıştıran kod yoktur**:

- **Panel/API:** `restore_requests` tablosuna `status='bekliyor'` bir İSTEK
  yazar (snapshot_id + kim istedi). Şifre modalda TEKRAR sorulur (yanlışsa
  403, oturum düşmez), audit_log'a da yazılır.
- **Host izleyici (`scripts/restore-apply.sh`, systemd timer):** kuyruğu
  okur → `yedekleniyor` (önce güvenlik yedeği) → `yukleniyor` (restic dump |
  pg_restore) → `tamamlandi`. Herhangi bir adım hata verirse `hata` +
  `error_detail` yazıp DURUR; yarım geri yükleme yapılmaz.

**Tek seferde tek aktif restore.** Uygulama 409 döner, asıl garanti kısmi
tekil indekstir (`uq_restore_tek_aktif`) — eşzamanlı iki istek veritabanınca
reddedilir.

**Güvenlik yedeği etiketle ayrılır.** Normal yedekler `hesaplik`, geri
yükleme öncesi alınanlar `restore-oncesi` etiketli (`scripts/backup.sh
[etiket]`). `restic forget` budaması YALNIZCA `hesaplik` etiketine uygulanır:
güvenlik yedeği 24 saat sonra kendiliğinden silinmez. Panel iki etiketi
birlikte listeler, güvenlik yedeklerini rozetle işaretler.

Host izleyici kurulu değilse istek `bekliyor` kalır — kaybolmaz; panel
birkaç dakika sonra "izleyici çalışmıyor olabilir" uyarısı gösterir
(dolaylı sinyal, kesin bilgi değil).

### Canlıya alırken yapılacaklar (HATIRLATMA)

Proje GitHub Actions ile İzmir sunucusuna deploy edilecek. Deployment günü
şunlar mutlaka güncellenmeli:

1. **Yedekleme hedefleri.** Yerelde `./data/backups` kullanılıyor. Sunucuda
   3-2-1 kuralı: (a) sunucu yerel diski, (b) Bosna'daki masaüstü (WireGuard
   üzerinden), (c) bulut (Cloudflare R2 veya Backblaze B2 ücretsiz katman).
   `RESTIC_REPOSITORY` bunlara göre çoğaltılır; her hedef için ayrı repo
   veya `rclone` backend.
2. `RESTIC_PASSWORD` GitHub Actions secret + sunucuda Docker secret olarak
   saklanır, asla repoya girmez.
3. systemd timer'ları sunucuda etkinleştir (`deployment/README.md`).
4. Haftalık restore testi sonucu Telegram'a bildirilsin (Faz 3'te bot
   gelince bağlanacak).
5. `.env`deki tüm parolalar üretim değerleriyle değiştirilir.
6. Postgres portu (`127.0.0.1:5432`) dışarı AÇILMAZ, sadece compose ağı.
7. Caddy ile TLS, `DOMAIN` gerçek alan adına ayarlanır.

## Yol Haritası ve Bilinen Eksikler

Aşağıdaki liste Faz 1 döneminde yazıldı ve **güncelliğini yitirdi** —
özgün hâliyle korunuyor. Bugünkü durum: yedekleme (Faz 2), Telegram bot +
kural parser (Faz 3), LLM router (Faz 4), sesli komut (Faz 5), PDF raporlar
(Faz 4b) ve admin paneli (Faz 7) tamamlandı; kişi bilgi paneli hâlâ düz
`meta` şeridi olarak duruyor. Sıradaki işler: EditTxModal/AddEntry'nin tek
akıllı ürün alanına geçirilmesi, ürün/stok raporlaması, koşan formatın
sistemdeki mal bakiyesiyle karşılaştırılması.

### Bilinen eksikler / sırada olan

- Kişi bilgi paneli (isme tıklayınca modal/panel) — henüz eklenmedi,
  şu an detay sayfasının üstünde düz `meta` şeridi olarak duruyor.
- Yedekleme (pgBackRest + restic) — Faz 2, henüz kurulmadı.
- Telegram bot + kural parser — Faz 3.
- LLM router + vLLM (Bosna) — Faz 4.
- Sesli komut (Whisper) — Faz 5.
