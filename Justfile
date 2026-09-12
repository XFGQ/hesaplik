# Hesaplık — gelistirme komutlari.  Kurulum:  sudo dnf install -y just

# Veritabanini baslat
db:
    docker compose up -d db

# API (yalnizca app/ izlenir; node_modules degisiklikleri reload tetiklemesin)
api: db
    .venv/bin/uvicorn app.main:app --reload --reload-dir app

# PWA gelistirme sunucusu
web:
    cd web && npm run dev

# Testler
test: db
    .venv/bin/pytest -q

# Arayuz testleri (Node'un kendi kosucusu, ek bagimlilik yok)
web-test:
    cd web && node --test "src/**/*.test.ts"

# Lint
lint:
    .venv/bin/ruff check app tests
    cd web && npm run lint

# Ilk kurulum: venv, bagimliliklar, npm
setup:
    /usr/bin/python3.13 -m venv .venv
    .venv/bin/pip install -U pip
    .venv/bin/pip install -e ".[dev]"
    cd web && npm install

# Ornek urunler (bos veritabani icin)
seed:
    curl -sX POST localhost:8000/api/products -H 'Content-Type: application/json' \
      -d '{"name":"Saman","base_unit":"balya","unit_price":"75.00"}' || true
    curl -sX POST localhost:8000/api/products -H 'Content-Type: application/json' \
      -d '{"name":"Arpa","base_unit":"kg","unit_price":"18.50"}' || true

# Veritabanini sifirla (TUM VERI SILINIR)
reset-db:
    docker compose down -v
    docker compose up -d db

# API + PWA tek terminalde. Ctrl+C ikisini birden kapatir.
dev: db
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'kill 0' EXIT INT TERM
    .venv/bin/uvicorn app.main:app --reload --reload-dir app 2>&1 | sed 's/^/[api] /' &
    (cd web && npm run dev) 2>&1 | sed 's/^/[web] /' &
    wait

web-host:
    cd web && npm run dev -- --host

# Yedek al (restic)
backup: db
    ./scripts/backup.sh

# Veritabanini sikistirip admin'in Telegram'ina gonder (--gonderme: yalnizca dene)
telegram-yedek *args: db
    ./scripts/telegram-yedek.sh {{args}}

# Depodaki yedekleri listele
backup-list:
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; source .env; set +a
    : "${RESTIC_REPOSITORY:=./data/backups}"
    export RESTIC_REPOSITORY RESTIC_PASSWORD
    restic snapshots

# En son yedeği geçici DB'ye açıp doğrula
restore-test: db
    ./scripts/restore-test.sh

# Telegram bot (yerelde uzun yoklama)
bot: db
    .venv/bin/python -m app.bot.main

# Hepsi idempotent (IF NOT EXISTS / ON CONFLICT), taze kurulumda da guvenli:
# schema.sql kanoniktir, migration'lar MEVCUT veritabanini yeni surume tasir.
# db/migrations/*.sql dosyalarini sirayla uygula
migrate: db
    #!/usr/bin/env bash
    set -euo pipefail
    [ -f .env ] || { echo ".env yok — once: cp .env.example .env"; exit 1; }
    # .env source EDILMEZ: AUTH_PASSWORD_HASH bcrypt hash'i `$2b$12$...`
    # icerir, kabuk onu degisken sanip patlar. Gereken iki deger okunur.
    envdegeri() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
    pguser="$(envdegeri POSTGRES_USER)"; pguser="${pguser:-hesaplik}"
    pgdb="$(envdegeri POSTGRES_DB)";     pgdb="${pgdb:-hesaplik}"
    for i in $(seq 1 60); do
      docker compose exec -T db pg_isready -U "$pguser" >/dev/null 2>&1 && break
      [ "$i" = 60 ] && { echo "veritabani hazir olmadi"; exit 1; }
      sleep 1
    done
    for f in db/migrations/*.sql; do
      echo "[migrate] $f"
      docker compose exec -T db psql -q -v ON_ERROR_STOP=1 \
        -U "$pguser" -d "$pgdb" < "$f"
    done
    echo "[migrate] tamam"

# Sonrasinda tek is kalir: .env'i doldurup `just dev`.
# Yeni makinede sifirdan kurulum (.env + venv + npm + db + migration)
quickstart:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f .env ]; then
      echo "[quickstart] .env zaten var, dokunulmadi"
    else
      cp .env.example .env
      echo "[quickstart] .env olusturuldu (.env.example kopyasi)"
    fi
    just setup
    just migrate
    echo
    echo "======================================================================"
    echo " Kurulum tamam."
    echo
    echo " SIRADAKI ADIM: .env dosyasini doldur —"
    echo "   POSTGRES_PASSWORD / DATABASE_URL   (yerelde ornek deger yeterli)"
    echo "   AUTH_USERNAME, AUTH_PASSWORD_HASH, JWT_SECRET"
    echo "     hash:   .venv/bin/python -c \"import bcrypt; print(bcrypt.hashpw(b'sifreniz', bcrypt.gensalt()).decode())\""
    echo "     secret: .venv/bin/python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    echo "   (ucu de BOSSA sistem fail-closed: giris reddedilir, uclar 401 doner)"
    echo "   Opsiyonel: TELEGRAM_BOT_TOKEN, NVIDIA_API_KEY, GROQ_API_KEY"
    echo
    echo " Sonra:  just dev      # api :8000 + web :5173"
    echo "         just seed     # ornek urunler (saman, arpa)"
    echo "======================================================================"
