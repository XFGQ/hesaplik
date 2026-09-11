# Yedekleme — sunucu kurulumu

Bu klasördeki systemd birimleri henüz kurulmadı, hazır bekliyor. İzmir
sunucusuna deploy günü etkinleştirilecek.

## Kurulum

```
sudo cp deployment/hesaplik-backup.service /etc/systemd/system/
sudo cp deployment/hesaplik-backup.timer /etc/systemd/system/
sudo cp deployment/hesaplik-restore-test.service /etc/systemd/system/
sudo cp deployment/hesaplik-restore-test.timer /etc/systemd/system/

# Unit dosyalarındaki User= ve WorkingDirectory= gerçek kurulum yoluna göre
# düzeltilmeli (varsayılan: user=hesaplik, /opt/hesaplik). Bu kullanıcı
# docker grubunda olmalı (docker compose exec çalıştırabilmesi için).

sudo systemctl daemon-reload
sudo systemctl enable --now hesaplik-backup.timer
sudo systemctl enable --now hesaplik-restore-test.timer

# Kontrol
systemctl list-timers | grep hesaplik
journalctl -u hesaplik-backup.service -n 50
```

## Geri yükleme izleyicisi (restore-apply) — HENÜZ DENENMEDİ

Admin panelindeki "Ana veri yap" düğmesi veritabanına dokunmaz; yalnızca
`restore_requests` tablosuna `bekliyor` bir istek yazar. İsteği uygulayan
şey host'taki bu birimdir:

```
sudo cp deployment/hesaplik-restore-apply.service /etc/systemd/system/
sudo cp deployment/hesaplik-restore-apply.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hesaplik-restore-apply.timer
```

**Kurmadan önce mutlaka test edin.** `scripts/restore-apply.sh` taslaktır ve
uçtan uca denenmemiştir; `pg_restore --clean --if-exists` mevcut şemayı
düşürüp yeniden kurar. İlk denemeyi ayrı bir test veritabanında yapın
(`POSTGRES_DB` ile başka bir veritabanına yönlendirerek).

Timer kurulu değilken panelden gelen istekler `bekliyor` durumunda kalır —
kaybolmaz, panel birkaç dakika sonra "sunucudaki izleyici çalışmıyor olabilir"
uyarısı gösterir.

İzleme:

```
tail -f data/restore.log
journalctl -u hesaplik-restore-apply.service -n 50
```

## vLLM uzaktan aç/kapat (vllm-control) — BOSNA'da kurulur, İzmir'de DEĞİL

"Yol B": panel (İzmir, /admin > LLM Yönetimi) yalnızca bir TERCİH yazar
(`GET`/`POST /api/admin/vllm-control`); Bosna'daki bu birim tercihi kendi
çeker (`GET /api/vllm-desired`, token korumalı) ve `docker start/stop/run`
ile uygular. Panel Bosna'ya asla doğrudan komut göndermez — bkz.
`app/services/vllm_control.py` ve `scripts/vllm-control.sh` modül başı
yorumları.

```
# BOSNA'daki makinede:
sudo cp deployment/vllm-control.service /etc/systemd/system/
sudo cp deployment/vllm-control.timer   /etc/systemd/system/
# Unit'lerdeki User=/WorkingDirectory= gerçek kuruluma göre düzeltilmeli.
# Bosna'nın .env dosyasında (İzmir'in .env'inden AYRI) şunlar olmalı:
#   VLLM_CONTROL_URL=http://10.100.0.1:8100   (İzmir'e WireGuard tünel IP'si)
#   VLLM_CONTROL_TOKEN=...                     (İzmir'deki VLLM_CONTROL_TOKEN ile AYNI)
sudo systemctl daemon-reload
sudo systemctl enable --now vllm-control.timer
```

**Kurmadan önce elle deneyin.** `scripts/vllm-control.sh` taslaktır, uçtan uca
denenmemiştir. `docker run` komutundaki model/parametreler script içinde
sabittir (max-model-len 4096, gpu-memory-utilization 0.80, enforce-eager) —
değiştirmeden önce Bosna'daki 2080 Super'de elle test edin.

Timer kurulu değilken/İzmir'e erişilemezken script hiçbir şey yapmaz — vLLM
son bilinen durumunda kalır, rastgele açılıp kapanmaz.

İzleme:

```
tail -f data/vllm-control.log
journalctl -u vllm-control.service -n 50
```

## Telegram'a yedek (telegram-yedek) — restic'e EK

`scripts/telegram-yedek.sh` günde iki kez (04:00 ve 06:00, Türkiye saati)
tüm veritabanını düz SQL olarak döker, gzip'ler ve admin'in kişisel
Telegram'ına dosya olarak gönderir (`hesaplik_2026-09-11_0600.sql.gz`,
başlık "Hesaplık yedek 11.09.2026 06:00 · 1,2 MB"). Sunucu/disk tamamen
giderse son yedek Telegram'da durur. Restic disk yedeğinin yerine geçmez,
yanında çalışır.

`.env`'de gerekenler:

- `TELEGRAM_BOT_TOKEN` — botun kullandığı token (aynısı).
- `TELEGRAM_ADMIN_CHAT_ID` — kişisel chat id. Telegram'da `@userinfobot`'a
  bir mesaj yazın, cevaptaki `Id:` sayısını girin. Ayrıca kendi botunuza bir
  kez `/start` yazın — bot, kendisiyle hiç konuşmamış birine mesaj atamaz
  (Telegram "chat not found" der, script bunu log'a yazar).

```
# Önce elle deneyin (HEMEN bir yedek gönderir):
cd /opt/hesaplik
COMPOSE_FILE=docker-compose.prod.yml ./scripts/telegram-yedek.sh

# Telegram'a göndermeden yalnızca dök + sıkıştır + doğrula:
COMPOSE_FILE=docker-compose.prod.yml ./scripts/telegram-yedek.sh --gonderme

sudo cp deployment/hesaplik-telegram-yedek.service /etc/systemd/system/
sudo cp deployment/hesaplik-telegram-yedek.timer   /etc/systemd/system/
# User=, WorkingDirectory=, ExecStart= yollarını gerçek kuruluma göre düzeltin.
sudo systemctl daemon-reload
sudo systemctl enable --now hesaplik-telegram-yedek.timer
systemctl list-timers | grep telegram-yedek   # sıradaki: 04:00 / 06:00
```

Timer saatleri `Europe/Istanbul` diye yazıldı; sunucu UTC'de olsa da
çalışma Türkiye saatiyle 04:00 ve 06:00'dadır. Sunucu o saatte kapalıysa
açılışta bir kez telafi edilir (`Persistent=true`).

**Alternatif: crontab** (systemd yerine, `hesaplik` kullanıcısının
`crontab -e`'si):

```
CRON_TZ=Europe/Istanbul
0 4,6 * * * cd /opt/hesaplik && COMPOSE_FILE=docker-compose.prod.yml ./scripts/telegram-yedek.sh >/dev/null 2>&1
```

`CRON_TZ` yalnızca cronie'de (Fedora/RHEL) çalışır; Debian/Ubuntu cron'u onu
tanımaz, orada sunucu saat dilimi Türkiye olmalı
(`timedatectl set-timezone Europe/Istanbul`). Çıktının atılması sorun değil:
script her şeyi `data/telegram-yedek.log`'a yazar, hatayı Telegram'dan
bildirir.

**Sınır ve hatalar.** Telegram bot API'si 50 MB üstü dosya kabul etmez.
Sıkıştırılmış yedek 45 MB'ı geçerse GÖNDERİLMEZ, admin'e "Yedek 50MB'ı aştı,
alternatif gerekli" mesajı gider. Döküm, bütünlük kontrolü (`gzip -t` +
pg_dump'ın bitiş satırı) ya da gönderim başarısız olursa da sebebiyle
birlikte admin'e mesaj gider, servis `failed` görünür. Geçici dosya her
durumda silinir, sunucuda döküm kopyası kalmaz.

**Telegram'daki yedekten geri dönme.** Dosya `--clean --if-exists` ile
alınır: mevcut tabloları düşürüp yeniden kurar. Önce mevcut hâli yedekleyin:

```
./scripts/backup.sh
gunzip -c hesaplik_2026-09-11_0600.sql.gz \
  | docker compose -f docker-compose.prod.yml exec -T db \
      psql -v ON_ERROR_STOP=1 -U hesaplik -d hesaplik
```

İzleme:

```
tail -f data/telegram-yedek.log
journalctl -u hesaplik-telegram-yedek.service -n 50
```

## Manuel çalıştırma / test

```
sudo systemctl start hesaplik-backup.service
sudo systemctl start hesaplik-restore-test.service
sudo systemctl start hesaplik-telegram-yedek.service   # hemen Telegram'a yedek
```

## Canlıya alırken mutlaka değiştirilecekler

Bu listenin güncel ve tam hali `CLAUDE.md` > "Canlıya alırken yapılacaklar"
bölümünde. Özet:

1. **Yedekleme hedefleri (3-2-1 kuralı).** Yerelde `./data/backups`
   (tek yerel restic deposu) kullanılıyor. Sunucuda en az üç hedef gerekir:
   sunucu yerel diski, Bosna'daki masaüstü (WireGuard üzerinden), bulut
   (Cloudflare R2 veya Backblaze B2 ücretsiz katman). Her hedef için ayrı
   `RESTIC_REPOSITORY` (veya `rclone` backend) demek — `scripts/backup.sh`
   tek depoya yazıyor; üç hedefe yazmak için ya scripti üç kez farklı
   `RESTIC_REPOSITORY` ile çağıran bir sarmalayıcı eklenir, ya da script
   depo listesi üzerinde döner. Bu depoyu henüz seçmedik.
2. `RESTIC_PASSWORD` GitHub Actions secret + sunucuda Docker/systemd secret
   olarak saklanır, **asla** `.env` dosyası repoya girmez ve asla loglanmaz.
3. Bu README'deki systemd timer'ları etkinleştir (yukarıdaki adımlar).
4. Haftalık restore testi sonucu Telegram'a bildirilsin (Faz 3'te bot
   gelince `hesaplik-restore-test.service`'e `ExecStartPost` veya benzeri
   bir bildirim adımı eklenecek).
5. `.env`'deki tüm parolalar (Postgres, restic) üretim değerleriyle
   değiştirilir — `.env.example`'daki placeholder'lar asla kullanılmaz.
6. Postgres portu (`127.0.0.1:5432`) dışarı açılmaz, sadece compose ağı
   (docker-compose.yml zaten böyle yapılandırılmış, kontrol et).
7. Caddy ile TLS, `DOMAIN` gerçek alan adına ayarlanır.

## Web arayüzünde yedek listesi (api container'ı)

Ayarlar > Yedekleme bölümü `GET /api/backups` ile depoyu **listeler**;
yedeği alan yine host'taki `hesaplik-backup.timer`'dır. Bunun için
`docker-compose.prod.yml`'de api servisi:

- `RESTIC_REPOSITORY` / `RESTIC_PASSWORD` değişkenlerini alır (`.env`),
- depoyu **salt okunur** (`:ro`) bağlar; container depoya yazamaz.

**`RESTIC_REPOSITORY` üretimde mutlak yol olmalı** (örn.
`/var/www/duman.rinnesoft.com/data/backups`). Sebep: bağlama noktasının
kaynağı ile hedefi aynı yoldur — restic depoyu `RESTIC_REPOSITORY`'deki
mutlak yolda arar, container içinde de aynı yerde görünmesi gerekir.
Göreli yol (`./data/backups`) verilirse `docker compose up` geçersiz
bağlama hedefiyle patlar.

Uzak depoya (`s3:...`, `rclone:...`) geçilirse bağlama satırı kaldırılır,
yalnızca değişkenler kalır.

"Şimdi yedekle" düğmesi container'dan çalışmaz (orada `docker compose`
yok ve depo salt okunur): endpoint 503 ile "Yedekler sunucuda otomatik
alınıyor" der, listeleme etkilenmez.

## Notlar

- `scripts/backup.sh` ve `scripts/restore-test.sh` idempotent: depo yoksa
  `restic init` ile oluşturur, geçici test veritabanını her çalıştırmada
  düşürüp yeniden kurar.
- Loglar `./data/backup.log` dosyasına eklenir (append). Sunucuda log
  rotasyonu için `logrotate` eklenmesi düşünülebilir (henüz yok).
- `RESTIC_PASSWORD` betiklerde asla `echo`/log edilmez; kaybolursa depo
  kurtarılamaz, bu yüzden parola ayrıca güvenli bir kasada (örn. Bitwarden)
  saklanmalı.
