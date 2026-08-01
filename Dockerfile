# --------------------------------------------------- Aşama 1: web (React/Vite)
FROM node:22-alpine AS web

WORKDIR /web
# Önce yalnızca kilit dosyaları: kaynak değişince npm ci katmanı yeniden
# çalışmasın (Docker katman önbelleği).
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ------------------------------------------------- Aşama 2: API + web/dist
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# PDF raporlar DejaVu Sans ister (app/services/report.py); slim imajda font yok.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY db ./db
COPY scripts ./scripts

# Aşama 1'in çıktısı. Tek image: hem API hem arayüz.
COPY --from=web /web/dist ./web/dist

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
