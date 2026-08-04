#!/usr/bin/env bash
# Hesaplık — geri yükleme izleyicisi (HOST'ta çalışır, container'da DEĞİL).
#
# TASLAK: mantığı tamamdır ama sunucuda HENÜZ kurulmadı ve uçtan uca
# denenmedi. İlk kurulumda önce boş/test bir veritabanında denenmeli
# (bkz. deployment/README.md).
#
# ------------------------------------------------------------------ neden
# Admin paneli veritabanını geri YÜKLEYEMEZ: API container'ında `docker`
# istemcisi yok ve restic deposu salt okunur bağlı. Bu yüzden panel yalnızca
# `restore_requests` tablosuna bir İSTEK yazar (status='bekliyor'); gerçek işi
# bu script yapar. Host'ta systemd timer ile dakikada bir çalıştırılır.
#
# ------------------------------------------------------------------ akış
#   1. status='bekliyor' en eski isteği al  → status='yedekleniyor'
#   2. MEVCUT veriyi yedekle (scripts/backup.sh restore-oncesi)
#      → alınan snapshot kimliği pre_backup_snapshot'a yazılır
#   3. status='yukleniyor' → restic dump | pg_restore --clean --if-exists
#   4. başarılı → status='tamamlandi', finished_at
#      herhangi bir adım hata → status='hata' + error_detail ve DUR
#
# ------------------------------------------------------------- güvenlik
# * pg_restore --clean --if-exists mevcut şemayı DÜŞÜRÜP yeniden kurar.
#   Bu yüzden 2. adım (güvenlik yedeği) BAŞARISIZ OLURSA 3. adıma GEÇİLMEZ.
# * Yarım geri yükleme yapılmaz: hata olursa durum 'hata' yazılır, sebep
#   kaydedilir, script durur. Sonraki çalıştırma o isteği tekrar denemez
#   (yalnızca 'bekliyor' olanı alır) — kullanıcı panelden yeni istek açar.
# * Aynı anda iki restore olamaz: tablodaki kısmi tekil indeks buna izin
#   vermez, ayrıca bu script flock ile tek örnek çalışır.
# * Kullanıcıdan gelen tek veri snapshot_id'dir; restic'e argüman olarak
#   verilmeden ÖNCE biçimi doğrulanır (yalnızca onaltılık karakter).
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

PRE_RESTORE_TAG="restore-oncesi"     # app/services/backup.py: PRE_RESTORE_TAG
DUMP_PATH="/hesaplik.dump"           # backup.sh --stdin-filename
LOG_FILE="./data/restore.log"
LOCK_FILE="./data/restore-apply.lock"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$1" | tee -a "$LOG_FILE" >&2
}

# Tek örnek: timer sık çalışır, uzun süren bir geri yüklemenin üstüne ikinci
# bir kopya binmemeli.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "başka bir restore-apply çalışıyor, çıkılıyor"
  exit 0
fi

# ---------------------------------------------------------------- veritabanı
# psql container üzerinden çağrılır (host'ta psql kurulu olmayabilir).
# -tA: başlıksız, hizalamasız — kabuk için ayrıştırması kolay çıktı.

psql_query() {
  docker compose exec -T db psql -tA -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"
}

# Serbest metin (hata mesajı) SQL'e gömülmeden önce tek tırnaklar ikilenir.
sql_quote() {
  printf "%s" "$1" | sed "s/'/''/g"
}

set_status() {
  local id="$1" status="$2" extra="${3:-}"
  psql_query "UPDATE restore_requests
                 SET status = '$(sql_quote "$status")' $extra
               WHERE id = $id;" >/dev/null
}

fail() {
  local id="$1" reason="$2"
  log "HATA istek #$id: $reason"
  set_status "$id" "hata" ", error_detail = '$(sql_quote "$reason")', finished_at = now()"
  exit 1
}

# ---------------------------------------------------------------- 1. istek al
# En eski 'bekliyor' istek. Aynı anda başka bir kopya almasın diye tek
# ifadede hem seçilir hem 'yedekleniyor' yapılır (atomik).

row="$(psql_query "
  UPDATE restore_requests
     SET status = 'yedekleniyor', started_at = now()
   WHERE id = (SELECT id FROM restore_requests
                WHERE status = 'bekliyor'
                ORDER BY requested_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED)
  RETURNING id || '|' || snapshot_id;
" | tr -d '[:space:]')"

if [ -z "$row" ]; then
  exit 0            # bekleyen istek yok, sessizce çık (timer sık çalışır)
fi

REQ_ID="${row%%|*}"
SNAPSHOT_ID="${row##*|}"
log "istek #$REQ_ID alındı, hedef snapshot: $SNAPSHOT_ID"

# Panelden gelen kimlik restic'e argüman olacak: biçimini burada doğrula.
case "$SNAPSHOT_ID" in
  *[!a-f0-9]*|"") fail "$REQ_ID" "geçersiz snapshot kimliği: $SNAPSHOT_ID" ;;
esac

# Hedef snapshot gerçekten var mı? Yoksa güvenlik yedeği alıp boşuna
# uğraşmadan dur.
if ! restic snapshots "$SNAPSHOT_ID" --no-lock >/dev/null 2>&1; then
  fail "$REQ_ID" "snapshot depoda bulunamadı: $SNAPSHOT_ID"
fi

# --------------------------------------------- 2. mevcut veriyi yedekle (şart)
# Bu adım başarısızsa GERİ YÜKLEME YAPILMAZ: geri dönüşü olmayan bir işlemi
# ağ altında güvenlik ağı olmadan yapmayız.

log "istek #$REQ_ID: mevcut veri yedekleniyor ($PRE_RESTORE_TAG)"
if ! backup_out="$(./scripts/backup.sh "$PRE_RESTORE_TAG" 2>&1)"; then
  fail "$REQ_ID" "geri yükleme öncesi yedek alınamadı: $(printf '%s' "$backup_out" | tail -n1)"
fi

# backup.sh son satırda "snapshot=<short_id>" yazar.
PRE_ID="$(printf '%s' "$backup_out" | grep -o 'snapshot=[a-f0-9]*' | tail -n1 | cut -d= -f2)"
if [ -z "$PRE_ID" ]; then
  fail "$REQ_ID" "güvenlik yedeği alındı ama kimliği okunamadı"
fi
log "istek #$REQ_ID: güvenlik yedeği $PRE_ID"
set_status "$REQ_ID" "yedekleniyor" ", pre_backup_snapshot = '$(sql_quote "$PRE_ID")'"

# ------------------------------------------------------------- 3. geri yükle
# restic dump snapshot'ı stdout'a verir; pg_restore doğrudan onu okur (diske
# ara dosya yazılmaz). --clean --if-exists: mevcut nesneler düşürülüp yeniden
# kurulur, yani BU ADIMDAN SONRA eski veri yalnızca $PRE_ID yedeğindedir.

log "istek #$REQ_ID: geri yükleniyor ($SNAPSHOT_ID)"
set_status "$REQ_ID" "yukleniyor"

# DİKKAT: restic'in stderr'i BORUYA KARIŞTIRILMAZ (2>&1 yok) — karışsaydı
# hata metni dump verisinin içine girip pg_restore'a bozuk arşiv giderdi.
# Hata çıktısı ayrı dosyaya alınır. pipefail açık: iki taraftan biri hata
# verirse tamamı hata sayılır.
restic_err="$(mktemp)"
if ! restore_out="$(restic dump --no-lock "$SNAPSHOT_ID" "$DUMP_PATH" 2>"$restic_err" \
    | docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        --clean --if-exists --no-owner 2>&1)"; then
  hata="$(tail -n2 "$restic_err" | tr '\n' ' ')$(printf '%s' "$restore_out" | tail -n3 | tr '\n' ' ')"
  rm -f "$restic_err"
  fail "$REQ_ID" "geri yükleme başarısız: $hata"
fi
rm -f "$restic_err"

# ---------------------------------------------------------------- 4. bitir
set_status "$REQ_ID" "tamamlandi" ", finished_at = now()"
log "istek #$REQ_ID: TAMAMLANDI (yüklenen: $SNAPSHOT_ID, öncesi: $PRE_ID)"
