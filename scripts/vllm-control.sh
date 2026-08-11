#!/usr/bin/env bash
# Hesaplık — vLLM uzaktan aç/kapat izleyicisi (BOSNA'da çalışır, İzmir'de DEĞİL).
#
# TASLAK: mantık tamamdır ama Bosna'da HENÜZ kurulmadı, kurulum + ilk
# çalıştırma elle doğrulanmalı (bkz. deployment/README.md ve
# deployment/vllm-control.{service,timer}).
#
# ------------------------------------------------------------------ neden
# Panel ("Yol B", bkz. app/services/vllm_control.py) Bosna'ya doğrudan komut
# GÖNDERMEZ — yalnızca İzmir'deki DB'ye bir TERCİH yazar (settings.vllm_desired).
# Bu script o tercihi KENDİSİ ÇEKER (GET /api/vllm-desired, token korumalı) ve
# buna göre vLLM container'ını burada, Bosna'da, docker start/stop/run ile
# uygular. systemd timer ile ~30 sn'de bir çalıştırılır.
#
# ------------------------------------------------------------------ akış
#   1. İzmir'e GET /api/vllm-desired (token) -> "on" | "off"
#   2. "docker ps" ile vllm container'ı şu an çalışıyor mu bak
#   3. istenen == çalışan durum ise HİÇBİR ŞEY YAPMA (idempotent, gereksiz
#      start/stop yok)
#   4. "on" isteniyor ve kapalıysa:
#        - container zaten varsa (durdurulmuş) -> docker start (hızlı)
#        - hiç yoksa -> docker run ... (TAM komut aşağıda sabit, Bosna'daki
#          2080 Super'de doğrulanmış ayarlar)
#   5. "off" isteniyor ve çalışıyorsa -> docker stop (rm YOK — container
#      durur, VRAM boşalır, ama bir dahaki "on"da yeniden run etmeye gerek
#      kalmadan hızlı "docker start" yeterli olur)
#   6. Her eylem data/vllm-control.log'a yazılır.
#
# ------------------------------------------------------------- güvenlik
# * İzmir'e giden istek SALT OKUNUR (GET) — bu script İzmir'e hiçbir şey
#   YAZMAZ, yalnızca okur. Bozuk/eski bir Bosna kopyası bile İzmir'deki
#   veriyi bozamaz; olsa olsa yanlış docker start/stop yapar (bu da
#   idempotent karşılaştırma sayesinde en fazla bir kez).
# * VLLM_CONTROL_TOKEN olmadan/yanlışsa İzmir 401 döner — script bunu hata
#   sayıp vLLM'e HİÇ DOKUNMAZ (belirsizken elini kaldırmaz).
# * İzmir'e erişilemezse (tünel/ağ kopmuş) script hiçbir şey yapmaz — SON
#   BİLİNEN DURUMU KORUR, rastgele kapatıp açmaz. GPU'nun kendi kendine
#   kapanması/açılması yalnızca İzmir'den NET bir cevap geldiğinde olur.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# VLLM_CONTROL_URL: İzmir API'sinin bu makineden ulaşılabilir adresi
# (WireGuard tüneli üzerinden, örn. http://10.100.0.1:8100 — doğrudan
# İzmir'in genel adresi DEĞİL, tünel IP'si kullanılmalı).
: "${VLLM_CONTROL_URL:?VLLM_CONTROL_URL .env içinde tanımlı olmalı (örn. http://10.100.0.1:8100)}"
: "${VLLM_CONTROL_TOKEN:?VLLM_CONTROL_TOKEN .env içinde tanımlı olmalı (İzmir tarafındaki değerle aynı olmalı)}"
: "${VLLM_CONTAINER_NAME:=vllm}"
: "${VLLM_MODEL:=Qwen/Qwen2.5-7B-Instruct-AWQ}"
: "${VLLM_PORT:=8000}"

LOG_FILE="./data/vllm-control.log"
LOCK_FILE="./data/vllm-control.lock"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$1" | tee -a "$LOG_FILE" >&2
}

# Tek örnek: timer sık çalışır, üst üste binen iki docker start/stop olmasın.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "başka bir vllm-control çalışıyor, çıkılıyor"
  exit 0
fi

# ---------------------------------------------------------------- 1. İzmir'e sor
# GET, salt okunur. Erişilemezse/401 dönerse SESSİZCE çık — son bilinen
# durum korunur, tahmin yürütülmez.

if ! response="$(curl -fsS --max-time 10 \
  -H "X-Vllm-Control-Token: $VLLM_CONTROL_TOKEN" \
  "${VLLM_CONTROL_URL%/}/api/vllm-desired" 2>&1)"; then
  log "İzmir'e erişilemedi ya da yetkisiz, hiçbir şey yapılmıyor: $response"
  exit 0
fi

DESIRED="$(printf '%s' "$response" | jq -r '.desired // empty' 2>/dev/null || true)"
case "$DESIRED" in
  on|off) ;;
  *)
    log "beklenmeyen yanıt, hiçbir şey yapılmıyor: $response"
    exit 0
    ;;
esac

# ---------------------------------------------------------------- 2. mevcut durum

RUNNING="false"
if docker ps --format '{{.Names}}' | grep -qx "$VLLM_CONTAINER_NAME"; then
  RUNNING="true"
fi

# ---------------------------------------------------------------- 3. idempotent karşılaştırma

if { [ "$DESIRED" = "on" ] && [ "$RUNNING" = "true" ]; } \
  || { [ "$DESIRED" = "off" ] && [ "$RUNNING" = "false" ]; }; then
  exit 0   # zaten istenen durumda — timer sık çalışır, sessizce çık
fi

# ------------------------------------------------------------------- 4/5. uygula

if [ "$DESIRED" = "on" ]; then
  if docker ps -a --format '{{.Names}}' | grep -qx "$VLLM_CONTAINER_NAME"; then
    log "vLLM açılıyor (mevcut container yeniden başlatılıyor: $VLLM_CONTAINER_NAME)"
    docker start "$VLLM_CONTAINER_NAME" >>"$LOG_FILE" 2>&1
  else
    log "vLLM açılıyor (yeni container oluşturuluyor: $VLLM_CONTAINER_NAME, model=$VLLM_MODEL)"
    # TAM komut kasıtlı sabit (parametrize edilmedi): bu ayarlarla Bosna'daki
    # 2080 Super'de çalıştığı doğrulandı (max-model-len 4096,
    # gpu-memory-utilization 0.80, enforce-eager — CUDA graph derlemesi
    # atlanır, ilk istek daha hızlı ayağa kalkar, VRAM daha öngörülebilir).
    docker run -d --name "$VLLM_CONTAINER_NAME" \
      --restart unless-stopped \
      --gpus all \
      -p "127.0.0.1:${VLLM_PORT}:8000" \
      -v hfcache:/root/.cache/huggingface \
      vllm/vllm-openai:latest \
      --model "$VLLM_MODEL" --quantization awq \
      --max-model-len 4096 --gpu-memory-utilization 0.80 --enforce-eager \
      >>"$LOG_FILE" 2>&1
  fi
  log "vLLM AÇILDI ($VLLM_CONTAINER_NAME) — modelin belleğe yüklenmesi birkaç dakika sürebilir"
else
  log "vLLM kapatılıyor ($VLLM_CONTAINER_NAME)"
  docker stop "$VLLM_CONTAINER_NAME" >>"$LOG_FILE" 2>&1
  log "vLLM KAPANDI ($VLLM_CONTAINER_NAME) — VRAM boşaldı"
fi
