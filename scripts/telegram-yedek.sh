#!/usr/bin/env bash
# Hesaplık — tüm veritabanını sıkıştırıp admin'in kişisel Telegram'ına gönderir.
# Kullanım: scripts/telegram-yedek.sh             hemen bir yedek gönderir
#           scripts/telegram-yedek.sh --gonderme  dök + sıkıştır + doğrula, GÖNDERME
#
# Restic disk yedeğinin (scripts/backup.sh, 5 dk'da bir) YERİNE DEĞİL, EK
# olarak çalışır: sunucu/disk giderse son yedek Telegram'da durur.
# Zamanlayıcı: deployment/hesaplik-telegram-yedek.timer (04:00 ve 06:00).
#
# ------------------------------------------------------------------ akış
#   pg_dump (düz SQL) → gzip → bütünlük kontrolü → boyut kontrolü (45 MB)
#   → sendDocument → geçici dizin silinir (başarıda da hatada da).
#
# Sessiz başarısızlık yok: döküm, doğrulama ya da gönderim başarısız olursa,
# ya da dosya Telegram sınırını aşarsa admin'e sendMessage ile yazılır ve
# script sıfırdan farklı kodla çıkar (systemd'de "failed" görünür).
#
# ------------------------------------------------------------- güvenlik
# * .env SOURCE EDİLMEZ: AUTH_PASSWORD_HASH bcrypt hash'i `$2b$12$...` içerir,
#   kabuk onu değişken sanıp bozar (set -u ile patlar). Yalnızca gereken
#   anahtarlar grep ile okunur. Ortamda zaten tanımlı olan değer .env'dekini
#   ezer (systemd Environment=, elle test için VAR=... önekli çağrı).
# * Bot token'ı komut satırına yazılmaz (`ps`'te görünürdü): URL curl'e
#   stdin'den yapılandırma olarak verilir.
# * Döküm yalnızca 0700 izinli geçici dizinde durur; sunucuda kopya kalmaz.
set -Eeuo pipefail
umask 077

GONDER=1
case "${1:-}" in
  "") ;;
  --gonderme) GONDER=0 ;;
  -h|--help)
    sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "Bilinmeyen argüman: $1 (yalnızca --gonderme)" >&2
    exit 2
    ;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Telegram bot API'si 50 MB üstü dosya kabul etmez; pay bırakmak için 45 MB.
MAX_BAYT=$((45 * 1024 * 1024))
# Dosya adı ve başlık Türkiye saatiyle — sunucu UTC'de olsa bile "06:00"
# yedeği gerçekten sabah 6'nın yedeği olarak görünsün (timer da aynı dilimde).
ZAMAN_DILIMI="Europe/Istanbul"
LOG_FILE="./data/telegram-yedek.log"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$1" | tee -a "$LOG_FILE" >&2
}

# .env'den TEK bir anahtarı okur; ortamda tanımlıysa ona dokunmaz.
# `export AD=...`, tırnaklı değer ve tırnaksız değerdeki ` # yorum` desteklenir.
env_oku() {
  local ad="$1" deger
  [ -n "${!ad:-}" ] && return 0
  [ -f .env ] || return 0
  deger="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${ad}=" .env | tail -n1 | cut -d= -f2- || true)"
  deger="${deger%$'\r'}"
  case "$deger" in
    \"*\") deger="${deger:1:-1}" ;;
    \'*\') deger="${deger:1:-1}" ;;
    *) deger="${deger%%[[:space:]]#*}" ;;
  esac
  printf -v "$ad" '%s' "$deger"
}

for ad in POSTGRES_USER POSTGRES_DB TELEGRAM_BOT_TOKEN TELEGRAM_ADMIN_CHAT_ID; do
  env_oku "$ad"
done
: "${POSTGRES_USER:=hesaplik}"
: "${POSTGRES_DB:=hesaplik}"
: "${TELEGRAM_BOT_TOKEN:=}"
: "${TELEGRAM_ADMIN_CHAT_ID:=}"

# Ayar eksikse Telegram'a zaten yazılamaz: log + hata koduyla çık.
if [ "$GONDER" = 1 ]; then
  if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    log "HATA TELEGRAM_BOT_TOKEN tanımlı değil (.env), yedek gönderilemez"
    exit 1
  fi
  if ! [[ $TELEGRAM_ADMIN_CHAT_ID =~ ^-?[0-9]+$ ]]; then
    log "HATA TELEGRAM_ADMIN_CHAT_ID tanımlı değil ya da sayı değil (.env) — bkz. .env.example"
    exit 1
  fi
fi

baslik_zaman="$(TZ="$ZAMAN_DILIMI" date +'%d.%m.%Y %H:%M')"
dosya_zaman="$(TZ="$ZAMAN_DILIMI" date +'%Y-%m-%d_%H%M')"
gecici="$(mktemp -d "${TMPDIR:-/tmp}/hesaplik-telegram-yedek.XXXXXX")"
trap 'rm -rf "$gecici"' EXIT

# ------------------------------------------------------------ telegram
# Başarıda 0 döner; başarısızlıkta sebebi TG_HATA'ya yazar. Telegram'ın kendi
# hata açıklaması ("chat not found" gibi) curl -f ile yutulmasın diye gövde
# okunur. --retry geçici ağ hatalarını ve 429/5xx'i yeniden dener.
TG_HATA=""
telegram_api() {
  local metod="$1" kod
  shift
  kod="$(curl -sS --max-time 300 --retry 3 --retry-delay 15 \
      -o "$gecici/cevap.json" -w '%{http_code}' -K - "$@" \
      2>"$gecici/curl.err" \
      <<<"url = \"https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/${metod}\"")" || {
    TG_HATA="Telegram'a bağlanılamadı: $(tail -n1 "$gecici/curl.err")"
    return 1
  }
  if [ "$kod" = "200" ] && grep -q '"ok":true' "$gecici/cevap.json"; then
    return 0
  fi
  TG_HATA="Telegram reddetti (HTTP $kod): $(grep -o '"description":"[^"]*"' "$gecici/cevap.json" | cut -d'"' -f4 || true)"
  return 1
}

# chat_id de --form-string: -F ile "@" ya da "<" ile başlayan değer dosya sanılır.
bildir() {
  [ "$GONDER" = 1 ] || return 0
  telegram_api sendMessage \
    --form-string "chat_id=${TELEGRAM_ADMIN_CHAT_ID}" \
    --form-string "text=$1" \
    || log "admin'e bildirim de gönderilemedi: $TG_HATA"
}

hata() {
  trap - ERR
  log "HATA $1"
  bildir "⚠️ Hesaplık Telegram yedeği ALINAMADI (${baslik_zaman})"$'\n'"$1"
  exit 1
}
trap 'hata "beklenmeyen hata (satır $LINENO)"' ERR

# ------------------------------------------------------------- döküm
# Düz SQL (-Fc değil): telefondan bile açılıp okunur, geri yüklemek için
# yalnızca psql yeter. --clean --if-exists: schema.sql'in kurduğu taze
# volume'e de, dolu bir veritabanına da aynı dosya yüklenir.
dosya="$gecici/hesaplik_${dosya_zaman}.sql.gz"
log "başladı ($POSTGRES_DB)"

if ! docker compose exec -T db \
    pg_dump --clean --if-exists --no-owner -U "$POSTGRES_USER" "$POSTGRES_DB" \
    2>"$gecici/pg_dump.err" | gzip -9 >"$dosya"; then
  sebep="$(tail -n1 "$gecici/pg_dump.err")"
  hata "Veritabanı dökülemedi: ${sebep:-bilinmeyen sebep}"
fi

# Yarım döküm "yedek alındı" diye gönderilmesin: gzip bütün mü, pg_dump
# kendi bitiş satırını yazmış mı.
if ! gzip -t "$dosya" 2>/dev/null; then
  hata "Sıkıştırılmış yedek dosyası bozuk"
fi
son_satirlar="$(gzip -dc "$dosya" | tail -n 5)"
if [[ $son_satirlar != *"PostgreSQL database dump complete"* ]]; then
  hata "Döküm yarım kalmış (pg_dump bitiş satırı yok)"
fi

boyut="$(stat -c %s "$dosya")"
# 1 MB altı KB yazılır: küçük veritabanında "0,0 MB" boş yedek sanılmasın.
boyut_yazi="$(awk -v b="$boyut" 'BEGIN {
  if (b < 1048576) printf "%.0f KB", b / 1024; else printf "%.1f MB", b / 1048576 }')"
boyut_yazi="${boyut_yazi/./,}"

if [ "$boyut" -gt "$MAX_BAYT" ]; then
  log "HATA yedek ${boyut_yazi}, sınır 45 MB — Telegram'a gönderilmedi"
  bildir "⚠️ Hesaplık yedek 50MB'ı aştı (${boyut_yazi}, ${baslik_zaman}), alternatif gerekli."$'\n'"Bu yedek Telegram'a GÖNDERİLMEDİ. Disk (restic) yedeği ayrıca alınmaya devam ediyor."
  exit 1
fi

if [ "$GONDER" = 0 ]; then
  log "DENEME yedek hazır ve doğrulandı, gönderilmedi: $(basename "$dosya") (${boyut_yazi})"
  exit 0
fi

# ------------------------------------------------------------ gönderim
if ! telegram_api sendDocument \
    --form-string "chat_id=${TELEGRAM_ADMIN_CHAT_ID}" \
    --form-string "caption=Hesaplık yedek ${baslik_zaman} · ${boyut_yazi}" \
    -F "document=@${dosya}"; then
  hata "Yedek Telegram'a gönderilemedi: $TG_HATA"
fi

log "OK gönderildi: $(basename "$dosya") (${boyut_yazi})"
