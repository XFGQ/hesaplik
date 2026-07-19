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
