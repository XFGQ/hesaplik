#!/usr/bin/env bash
# Hesaplık — Postgres yedeği alır, restic deposuna yazar.
# Kullanım: scripts/backup.sh  (veya just backup)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${POSTGRES_USER:=hesaplik}"
: "${POSTGRES_DB:=hesaplik}"
: "${RESTIC_REPOSITORY:=./data/backups}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD .env içinde tanımlı olmalı}"

# 5 dakikada bir yedek alınıyor; --keep-hourly saatte tek yedek bırakıp
# geri kalan 11'ini anında budar. --keep-last ile son 24 saatin tüm
# 5-dakikalık yedekleri korunur (288 = 24*60/5). Dedup sayesinde her
# snapshot ~8.5 KiB, depolama sorun değil.
: "${RESTIC_KEEP_LAST:=288}"
: "${RESTIC_KEEP_DAILY:=30}"
: "${RESTIC_KEEP_WEEKLY:=12}"
: "${RESTIC_KEEP_MONTHLY:=12}"

export RESTIC_REPOSITORY RESTIC_PASSWORD

LOG_FILE="./data/backup.log"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$1" >>"$LOG_FILE"
}

fail() {
  local duration=$(( $(date +%s) - start_ts ))
  log "HATA yedek alma başarısız (${duration}s): $1"
  echo "Yedek alma başarısız: $1" >&2
  exit 1
}

start_ts=$(date +%s)
trap 'fail "beklenmeyen hata (satır $LINENO)"' ERR

docker compose up -d db >/dev/null

if ! restic snapshots >/dev/null 2>&1; then
  echo "Restic deposu bulunamadı, oluşturuluyor: $RESTIC_REPOSITORY"
  restic init
fi

docker compose exec -T db pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | restic backup --tag hesaplik --stdin --stdin-filename hesaplik.dump --host hesaplik

restic forget \
  --keep-last "$RESTIC_KEEP_LAST" \
  --keep-daily "$RESTIC_KEEP_DAILY" \
  --keep-weekly "$RESTIC_KEEP_WEEKLY" \
  --keep-monthly "$RESTIC_KEEP_MONTHLY" \
  --prune >/dev/null

trap - ERR
duration=$(( $(date +%s) - start_ts ))
log "OK yedek alındı (${duration}s)"
echo "Yedek tamamlandı (${duration}s)"
