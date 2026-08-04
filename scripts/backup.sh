#!/usr/bin/env bash
# Hesaplık — Postgres yedeği alır, restic deposuna yazar.
# Kullanım: scripts/backup.sh [etiket]   (veya just backup)
#
# Etiket boş bırakılırsa "hesaplik" kullanılır (zamanlayıcının aldığı normal
# yedekler). scripts/restore-apply.sh geri yüklemeden ÖNCE aldığı güvenlik
# yedeğini "restore-oncesi" etiketiyle alır. Ayrım kritik: aşağıdaki
# `restic forget` budaması YALNIZCA "hesaplik" etiketli yedeklere uygulanır,
# güvenlik yedekleri kendiliğinden silinmez.
set -euo pipefail

BACKUP_TAG="${1:-hesaplik}"
case "$BACKUP_TAG" in
  *[!a-zA-Z0-9_-]*)
    echo "Geçersiz etiket: $BACKUP_TAG (yalnızca harf, rakam, - ve _)" >&2
    exit 2
    ;;
esac

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
  printf '%s [%s] %s\n' "$(date -Iseconds)" "$BACKUP_TAG" "$1" >>"$LOG_FILE"
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
  | restic backup --tag "$BACKUP_TAG" --stdin --stdin-filename hesaplik.dump --host hesaplik

# --tag hesaplik: budama YALNIZCA normal yedeklere uygulanır. Geri yükleme
# öncesi alınan "restore-oncesi" yedekleri politikanın dışında kalır —
# keep-last 288 (24 saat) onları da kapsasaydı, bir geri yüklemeden bir gün
# sonra "eski hâle dön" imkânı sessizce yok olurdu.
restic forget --tag hesaplik \
  --keep-last "$RESTIC_KEEP_LAST" \
  --keep-daily "$RESTIC_KEEP_DAILY" \
  --keep-weekly "$RESTIC_KEEP_WEEKLY" \
  --keep-monthly "$RESTIC_KEEP_MONTHLY" \
  --prune >/dev/null

trap - ERR
duration=$(( $(date +%s) - start_ts ))
log "OK yedek alındı (${duration}s)"

# Son satır: restore-apply.sh bu satırdan snapshot kimliğini okur (jq her
# sunucuda kurulu olmayabilir, o yüzden grep ile).
son_id="$(restic snapshots --tag "$BACKUP_TAG" --latest 1 --json 2>/dev/null \
  | grep -o '"short_id":"[^"]*"' | tail -n1 | cut -d'"' -f4)"
log "snapshot=${son_id:-bilinmiyor}"
echo "Yedek tamamlandı (${duration}s) snapshot=${son_id:-bilinmiyor}"
