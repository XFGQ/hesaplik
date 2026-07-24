#!/usr/bin/env bash
# Hesaplık — en son yedeği geçici bir veritabanına açıp doğrular.
# Test edilmemiş yedek, yedek değildir.
# Kullanım: scripts/restore-test.sh  (veya just restore-test)
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

export RESTIC_REPOSITORY RESTIC_PASSWORD

TEST_DB="${POSTGRES_DB}_restore_test"
LOG_FILE="./data/backup.log"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$1" >>"$LOG_FILE"
}

fail() {
  local duration=$(( $(date +%s) - start_ts ))
  log "HATA restore-test başarısız (${duration}s): $1"
  echo "Restore test başarısız: $1" >&2
  exit 1
}

drop_test_db() {
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS ${TEST_DB};" >/dev/null 2>&1 || true
}

start_ts=$(date +%s)
trap 'fail "beklenmeyen hata (satır $LINENO)"' ERR
trap drop_test_db EXIT

docker compose up -d db >/dev/null

SNAP_COUNT=$(restic snapshots --tag hesaplik --json | jq 'length')
if [ "$SNAP_COUNT" -eq 0 ]; then
  fail "depoda hiç yedek yok"
fi

drop_test_db
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -c "CREATE DATABASE ${TEST_DB} OWNER ${POSTGRES_USER};" >/dev/null

restic dump latest /hesaplik.dump --tag hesaplik \
  | docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$TEST_DB" --no-owner --no-acl

read -r PERSON_COUNT TX_COUNT < <(
  docker compose exec -T db psql -tA -U "$POSTGRES_USER" -d "$TEST_DB" -c \
    "SELECT (SELECT count(*) FROM persons), (SELECT count(*) FROM transactions);" \
    | tr -d '\r' | tr '|' ' '
)

if [ "$PERSON_COUNT" -eq 0 ] || [ "$TX_COUNT" -eq 0 ]; then
  fail "geri yüklenen veritabanı boş (persons=$PERSON_COUNT, transactions=$TX_COUNT)"
fi

LIVE_BAL=$(docker compose exec -T db psql -tA -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT COALESCE(SUM(balance_try), 0) FROM v_person_balance;" | tr -d '\r[:space:]')
TEST_BAL=$(docker compose exec -T db psql -tA -U "$POSTGRES_USER" -d "$TEST_DB" -c \
  "SELECT COALESCE(SUM(balance_try), 0) FROM v_person_balance;" | tr -d '\r[:space:]')

if [ "$LIVE_BAL" != "$TEST_BAL" ]; then
  fail "bakiye toplamı uyuşmuyor (canlı=$LIVE_BAL, geri yüklenen=$TEST_BAL)"
fi

trap - ERR
duration=$(( $(date +%s) - start_ts ))
log "OK restore-test doğrulandı persons=$PERSON_COUNT tx=$TX_COUNT bakiye=$LIVE_BAL (${duration}s)"
echo "Restore test başarılı: persons=$PERSON_COUNT transactions=$TX_COUNT bakiye=${LIVE_BAL} TL (${duration}s)"
