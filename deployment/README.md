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

## Manuel çalıştırma / test

```
sudo systemctl start hesaplik-backup.service
sudo systemctl start hesaplik-restore-test.service
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
