# --------------------------------------------------- Aşama 1: web (React/Vite)
FROM node:22-alpine AS web

WORKDIR /web
# Önce yalnızca kilit dosyaları: kaynak değişince npm ci katmanı yeniden
# çalışmasın (Docker katman önbelleği).
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ------------------------------------------------------- Aşama 2: restic ikili
# API yedekleri listelerken restic'i çalıştırır (app/services/backup.py).
# Debian deposundaki sürüm eski kalabiliyor ve depoyu sunucuda yeni bir
# restic yazıyor; sürüm burada sabitlenip SHA256 ile doğrulanıyor.
FROM debian:bookworm-slim AS restic

ARG RESTIC_VERSION=0.19.1
# BuildKit otomatik doldurur; eski (klasik) yapıcıda boş kalır, o yüzden
# varsayılan amd64.
ARG TARGETARCH=amd64

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl bzip2; \
    case "$TARGETARCH" in \
      amd64) sha=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c ;; \
      arm64) sha=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465 ;; \
      *) echo "restic icin desteklenmeyen mimari: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/restic.bz2 \
      "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${TARGETARCH}.bz2"; \
    echo "$sha  /tmp/restic.bz2" | sha256sum -c -; \
    bunzip2 /tmp/restic.bz2; \
    install -m 0755 /tmp/restic /usr/local/bin/restic; \
    restic version; \
    rm -rf /var/lib/apt/lists/*

# ------------------------------------------------- Aşama 3: API + web/dist
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# PDF raporlar DejaVu Sans ister (app/services/report.py); slim imajda font yok.
# postgresql-client: /yedek ve panelden "Telegram'a gönder" pg_dump'ı buradan
# çalıştırır (app/services/telegram_yedek.py). pg_dump sunucudan ESKİ olamaz
# (döküm reddedilir); trixie tabanı 17 getirir, Postgres 16'yı sorunsuz döker.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Yedek listeleme (GET /api/backups) için. Depoya YAZMAZ: depo salt okunur
# bağlanır, listeleme `--no-lock` ile çalışır (bkz. app/services/backup.py).
COPY --from=restic /usr/local/bin/restic /usr/local/bin/restic

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY db ./db
COPY scripts ./scripts

# Aşama 1'in çıktısı. Tek image: hem API hem arayüz.
COPY --from=web /web/dist ./web/dist

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
