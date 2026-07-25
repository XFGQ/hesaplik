# Hesaplık — proje hafızası

Sesli/yazılı komutla çalışan cari hesap takip sistemi. Backend FastAPI +
PostgreSQL, frontend React + TS + Vite (PWA). İki sunucu: İzmir (7/24,
kaynak doğruluk) ve Bosna (GPU, LLM/Whisper — henüz bağlanmadı).

## Kesinlikle bozulmaması gereken kurallar

1. **`transactions` append-only.** DB tetikleyicisi UPDATE/DELETE'i reddeder.
   Düzeltme = ters kayıt (`ledger.reverse()`), asla direkt değişiklik değil.
2. **Para matematiği yalnızca `app/services/ledger.py` içinde, `Decimal` ile.**
   float asla kullanılmaz.
3. **Para ve mal iki ayrı hesaptır, birbirini ezmez.** Örnek: kişi 30 balya
   saman aldı (borç), sonra 50 balyanın parasını verdi (tahsilat + kalem).
   Sonuç: para −1.500 TL (ona borçlusun), mal −20 balya ("20 balya saman
   alacaklı" — o senden alacaklı, çünkü peşin ödedi). Bunlar birbirinden
   bağımsız, biri diğerini sıfırlamaz. Kalem işareti: DEBIT +qty (kişi mal
   aldı, borçlu), CREDIT −qty (kişi parasını verdi, malı bekliyor).
4. **Tutarı kullanıcı yazar, fiyat listesi bağlamaz.** Pazarlık gerçeği.
   Birim fiyat tutardan türetilir (`line_total / qty`), tersi değil.
5. **Ürün serbest metindir.** `app/services/catalog.py` eşleştirir
   (büyük/küçük harf + Türkçe "İ/I" normalize + alias). Bulamazsa yeni ürün
   açar. Fuzzy eşleştirme KASTEN otomatik değil — yanlış ürüne sessizce
   bağlamak daha tehlikeli, öneri sunulur (`suggest_products`), otomatik
   bağlanmaz.
6. **Borçlu kişi silinemez.** `DELETE /persons/{id}` bakiye ≠ 0 ise 422 döner.

## Mimari kararlar

- Bakiye asla kolonda tutulmaz, `SUM` ile hesaplanır (`ledger.balance_of`).
- `raw_messages` tablosu dokunulmaz kayıt: her gelen mesaj (Telegram/web)
  işlenmeden önce buraya yazılır. LLM yanlış anlasa bile orijinal metin
  kaybolmaz.
- LLM/STT provider'ları `LLMProvider`/`STTProvider` Protocol arkasında
  soyutlanacak (Faz 4-5, henüz yazılmadı). Router `inference.yml`'dan okur,
  Bosna/İzmir hangisi kullanılacak koddan değil configten belirlenir.
- Yerel geliştirmede Postgres Docker'da (`docker compose up -d db`),
  named volume kullanılıyor (bind mount SELinux/Fedora'da izin sorunu
  çıkardı).

## Komutlar

    just dev       # api + web tek terminalde (Ctrl+C ikisini kapatır)
    just api       # sadece backend, --reload-dir app (node_modules izlemez)
    just web       # sadece frontend
    just test      # pytest, docker db'yi otomatik ayağa kaldırır
    just seed      # örnek ürünler (saman, arpa)

## Test disiplini

Her ledger/catalog değişikliğinde `tests/test_ledger.py` veya
`tests/test_catalog.py`'a karşılık gelen test eklenir. Özellikle:
kuruş yuvarlama, append-only tetikleyici, ters kayıt, para/mal ayrımı.
`just test` önce yeşil olmadan commit atma.

## Branch ve commit

`main` (üretim, korumalı değil ama disiplinli çalış) / `develop` /
`feat,fix,chore,docs,test`+`/açıklama`. Conventional Commits, Türkçe özet,
küçük harf başlangıç, nokta yok. Detay: `CONTRIBUTING.md`.

## Bilinen eksikler / sırada olan

- Kişi bilgi paneli (isme tıklayınca modal/panel) — henüz eklenmedi,
  şu an detay sayfasının üstünde düz `meta` şeridi olarak duruyor.
- Yedekleme (pgBackRest + restic) — Faz 2, henüz kurulmadı.
- Telegram bot + kural parser — Faz 3.
- LLM router + vLLM (Bosna) — Faz 4.
- Sesli komut (Whisper) — Faz 5.

## Arayüz tasarım kuralları (Faz 1 sonrası)

- **Koyu mod statik.** Light mod yok. Zemin #0e1013, panel/kart #15181d,
  girdi #1c2026, kenarlık #2a2f37, metin #f2f0ea, soluk metin #8b93a1.
  Borç kırmızı #f0857d (koyu üzerinde), tahsilat mavi #6aa9f0. Tutarlar
  monospace + tabular-nums.
- **İki kolonlu düzen (masaüstü ≥720px):** solda sabit panel (230px),
  sağda içerik. Panelde: "DUMAN HOLDING" başlık, altında metrik kartları
  (Kişi sayısı, Toplam alacak, Toplam borç), en altta Yenile ve Ayarlar
  butonları + "Güncellendi · saat" bilgisi. Dar ekranda panel üste taşınır,
  metrikler yatay şerit, Yenile/Ayarlar ikon.
- **Ana ekran tablosu:** sütunlar Ad Soyad / Borç-Alacak (eski "Kalemler")
  / Bakiye / Son işlem. Her satır sonunda üç nokta (ti-dots) menüsü:
  Düzenle, Sil. Alt buton tek: siyah "Kişi ekle". Borç/Tahsilat ana
  ekranda YOK.
- **Kişi defteri (detay):** Borç ekle + Tahsilat ekle butonları burada,
  ikisi de modal (pop-up) açar — ayrı sayfaya gitmez. Modal koyu, ortalı,
  X ile kapanır, arka plan koyu yarı saydam. Borç modalı: tarih, ürün,
  adet+birim, tutar. Tahsilat modalı: tarih, ürün (opsiyonel — boşsa düz
  para), adet+birim (ürün doluysa), tutar. Kaydedince modal kapanır.
- **Bilgilendirme (toast):** her işlemden sonra üstte kısa şerit, birkaç
  saniye sonra kaybolur. "Ahmet Yılmaz'a 20 balya saman borç eklendi",
  "2.000 TL tahsilat eklendi", "Sistem güncellendi" gibi. Yeşil/nötr ton.
- **Yenile butonu:** sunucudan tüm sorguları invalidate eder, bitince
  "Güncellendi" toast'ı ve paneldeki saat güncellenir.
- Toast ve modal için harici kütüphane kullanma, kendi basit
  bileşenlerini yaz (React state + setTimeout). localStorage YOK.

## Silme = arşive taşı (kullanıcı kararı, 2026-07)

Kullanıcı "Sil" dediğinde kayıt YOK EDİLMEZ, ayrı arşiv tablosuna taşınır:

- `archived_transactions` tablosu: transactions'ın tüm kolonları + kalemleri
  (JSONB) + arşiv meta (archived_by, archived_at, archive_reason).
- Silme akışı: (1) kaydı + kalemlerini archived_transactions'a kopyala,
  (2) canlı transactions'tan gerçekten sil. Böylece canlı defter sade
  kalır, bakiye silineni saymaz; silinen her şey arşivde kim/ne zaman
  bilgisiyle durur.
- Append-only tetikleyicisi hâlâ geçerli: rastgele DELETE yasak. Yalnızca
  "arşivle-ve-sil" servis fonksiyonu, session-local bir işaret
  (SET LOCAL app.archiving = 'on') ile tetikleyiciye izin verir; işaret
  yoksa DELETE reddedilir. Böylece kod hatası defteri sessizce bozamaz,
  ama kasıtlı arşiv silmesi çalışır.
- "Düzelt": eskiyi arşive taşı (sil) + yeni değerle yeni kayıt aç. Kullanıcıya
  tek işlem gibi görünür.
- Ayrıca 5 dakikada bir tüm veritabanının otomatik yedeği alınır (Faz 2,
  pgBackRest/restic). Arşiv tablosu felaket kurtarma değil, günlük
  "sildim/düzelttim" işlemlerinin izini tutar; ikisi farklı amaç.

## Ayarlar menüsü (yapılacak)

Sol paneldeki Ayarlar butonu bir ayarlar ekranı/modalı açar. İçinde
(Faz 2'de doldurulacak): yedekleme durumu, "şimdi yedekle", "yedekten dön",
arşivlenen kayıtları görüntüle. Şimdilik yerini aç, altını sonra doldur.

## İşletme ayarları (settings tablosu)

Sol paneldeki işletme adı sabit değil, Ayarlar'dan değiştirilebilir ve
sunucuda saklanır (her cihazda aynı görünür, yenileyince kaybolmaz).

- `settings` tablosu: key TEXT PRIMARY KEY, value TEXT, updated_at.
  Basit anahtar-değer. İlk anahtar: business_name (varsayılan "Hesaplık").
- API: GET /api/settings (tüm ayarları döner), PUT /api/settings/{key}.
- Sol panel başlığı business_name'den okur. Ayarlar modalında düzenlenir,
  kaydedilince "Ayarlar güncellendi" toast'ı ve panel başlığı yenilenir.
- İleride buraya başka ayarlar da eklenebilir (para birimi, yedekleme
  aralığı vb.) — key-value olduğu için şema değişmeden büyür.

## Kişi defteri hareket tablosu (PersonDetail)

Hareketler liste değil TABLO olarak gösterilir. Sütunlar:
Tarih / Ürün / Adet / Birim fiyat / Tutar / (üç nokta menü).

- Ürün sütunu: kalem varsa ürün adı (borçlu/alacaklı etiketi ile), yoksa
  not veya "tahsilat"/"borç". Adet ve birim fiyat kalem yoksa boş (—).
- Tutar: borç kırmızı +, tahsilat mavi −, tabular-nums monospace.
- İptal edilmiş (arşive taşınmış zaten listede yok) veya ters kayıt satırı
  görsel olarak ayırt edilsin.
- Her satır sonunda üç nokta (ti-dots) menü: "Düzelt" ve "Sil".
  - Sil: DELETE /api/transactions/{id} (arşive taşır), onay iste, "Kayıt
    silindi" toast'ı.
  - Düzelt: kaydın değerleriyle dolu bir düzenleme modalı açar; kaydedince
    eskiyi sil (arşivle) + yeni değerle yeni kayıt aç, "Kayıt güncellendi"
    toast'ı. Kullanıcıya tek işlem gibi görünür.
- Dar ekranda (mobil) tablo yatay kaydırılabilir ya da satır düzenine
  düşebilir, ama masaüstünde net sütun/satır tablo.

## Canlıya alırken yapılacaklar (HATIRLATMA)

Proje GitHub Actions ile İzmir sunucusuna deploy edilecek. Deployment günü
şunlar mutlaka güncellenmeli:

1. **Yedekleme hedefleri.** Yerelde `./data/backups` kullanılıyor. Sunucuda
   3-2-1 kuralı: (a) sunucu yerel diski, (b) Bosna'daki masaüstü (WireGuard
   üzerinden), (c) bulut (Cloudflare R2 veya Backblaze B2 ücretsiz katman).
   `RESTIC_REPOSITORY` bunlara göre çoğaltılır; her hedef için ayrı repo
   veya `rclone` backend.
2. `RESTIC_PASSWORD` GitHub Actions secret + sunucuda Docker secret olarak
   saklanır, asla repoya girmez.
3. systemd timer'ları sunucuda etkinleştir (`deployment/README.md`).
4. Haftalık restore testi sonucu Telegram'a bildirilsin (Faz 3'te bot
   gelince bağlanacak).
5. `.env`deki tüm parolalar üretim değerleriyle değiştirilir.
6. Postgres portu (`127.0.0.1:5432`) dışarı AÇILMAZ, sadece compose ağı.
7. Caddy ile TLS, `DOMAIN` gerçek alan adına ayarlanır.

## Faz 3 — Telegram bot

**Mesaj asla kaybolmaz.** Webhook/polling ile gelen her güncelleme, işleme
girmeden ÖNCE `raw_messages` tablosuna yazılır ve hemen 200 dönülür.
İşleme ayrı adımda yapılır. Böylece parser çökse, deploy yapılsa, sunucu
yeniden başlasa bile mesaj durur ve sonra işlenir.
- Idempotency: `raw_messages(channel, external_id)` tekil indeksi
  (external_id = Telegram update_id). Aynı güncelleme iki kez işlenmez.

**LLM'den önce kural parser.** Mesajların çoğu düzenli kalıptadır ve regex
ile 15 ms'de çözülür; LLM 300 ms sürer. Kural parser önce dener,
çözemezse (Faz 4'te) LLM'e devreder. `RuleProvider` her zaman açıktır ve
asla kapanmaz — GPU yoksa sistem yine çalışır, sadece daha çok soru sorar.

**Yerelde polling, sunucuda webhook.** Yerel geliştirmede tünel gerekmesin
diye long polling; üretimde Caddy arkasında webhook. Aynı işleme kodu,
farklı besleme.

**Admin/müşteri ayrımı.** `/engine`, `/queue`, `/logs` gibi komutlar sadece
`TELEGRAM_ADMIN_IDS` içindeki chat_id'lere yanıt verir. Yetkisiz kişi bu
komutları yazarsa HİÇ cevap verilmez (komutun varlığı bile sızmasın).
`setMyCommands` scope ile müşteriye yalnızca /start ve /yardim gösterilir.

**Onay akışı.** Güven yüksek ve eşleşme netse: anında kaydet + 60 sn "Geri
al" butonu. Belirsizse TEK soru sor (inline keyboard butonlarıyla, yazı
yazdırma). Asla iki soru üst üste sorma, asla "formatı şöyle yazın" deme.

**Müşteriye teknik detay gösterilmez.** Provider adı, güven skoru, hata
kodu, "LLM/AI/model" kelimeleri asla görünmez.

## Kalıcı düzen ve kişi silme (2026-07-24 kararı)

**Sol panel her sayfada kalır.** People, PersonDetail — hepsinde solda
sabit panel görünür. Bunun için ortak bir Layout bileşeni (React Router
Outlet ile) kullanılır; her sayfa kendi barını çizmez. "Defter'e dön"
butonu sol panelde belirgin şekilde durur.

**Kişi silme üç nokta menüsünden.** Kişi listesinde (ana ekran tablosu) her
satırın sağında üç nokta: Düzenle / Sil. Kişi detayındaki "Hesap kapanınca
kaldırılabilir" butonu KALDIRILIR (kafa karıştırıcıydı; bakiye sıfır
değilken pasif duran bir butondu).

**Silme onayı yazarak.** Kişi silinirken window.confirm yetmez: modal açılır,
kullanıcı işletme adının ilk kelimesini (settings.business_name'in ilk
kelimesi, örn. "DUMAN") yazmadan Sil butonu aktifleşmez. Bakiye sıfır
değilse modalda bu açıkça uyarı olarak gösterilir ("Bu kişinin 10.000 TL
borcu var") ama kullanıcı yazarak onaylarsa işlem yapılır — kayıtlar zaten
soft-delete ve arşivle korunuyor.

**Hareket tablosu sabit.** Kişi defterindeki tablo, içerik azken bile
alanı doldurur; alt buton/eylemler sayfanın en altında sabit durur, tablo
uzadıkça yukarı kaymaz.

## Yedekten geri dönme (yalnızca komut satırı)

    set -a; source .env; set +a
    restic snapshots                       # ID seç
    just backup                            # önce mevcut hâli yedekle
    restic dump <ID> /hesaplik.dump > /tmp/geri.dump
    docker compose exec -T db pg_restore -U hesaplik -d hesaplik \
      --clean --if-exists --no-owner < /tmp/geri.dump

Tüm veritabanını o ana geri sarar. API'ye konmaz, kaza riski yüksek.

## Yazarak onay her silmede

Yazarak onay yalnızca kişi silmede değil, HAREKET (borç/tahsilat) silmede
de uygulanır. Ortak bir ConfirmDeleteModal bileşeni kullanılır; onay
kelimesi her yerde aynıdır: settings.business_name'in ilk kelimesi, Türkçe
kurallarla büyük harfe çevrilmiş (örn. "DUMAN").

Ayrım kasıtlıdır:
- **Düzelt** → yazı istemez. Para kaybolmaz, değeri değişir (eskisi arşive
  gider, yenisi açılır). Sık kullanılan, düşük riskli işlem.
- **Sil** → yazı ister. Kayıt defterden tamamen kalkar. Nadir, yüksek
  riskli işlem.

## Kişi eşleştirme güvenliği (2026-07-25 — kritik bug düzeltmesi)

Telegram'da yanlış kişiye para yazma hatası oldu: "furkan yılmaz" yazıldı,
sistem "furkan duman"a ekledi. Sebep: pg_trgm eşiği gevşekti, farklı
soyadlı iki isim "tek net eşleşme" sayıldı.

Kurallar:
- **Net eşleşme = yalnızca birebir (normalize) ad eşleşmesi VEYA belirgin
  şekilde tek yakın aday.** İki aday arasındaki benzerlik farkı küçükse
  (ör. ikisi de eşiğin üstünde) ASLA otomatik seçme — "hangisi?" diye sor.
- **Soyad ayırt edicidir.** "furkan yılmaz" ile "furkan duman" NET eşleşme
  değildir; girdi iki kelimeyse (ad+soyad) ve tam eşleşen kişi yoksa,
  kısmi eşleşmeleri aday olarak sun, otomatik bağlama.
- **Tek kelime girdi ("furkan") birden çok kişiye uyuyorsa** → hepsini
  aday göster, "hangisi?" diye sor. Otomatik seçme.
- Eşik değerleri kod içinde sabit sihirli sayı olarak değil, adlandırılmış
  sabit olarak tanımlansın (SIMILARITY_STRONG, SIMILARITY_CANDIDATE) ki
  ayarlanabilsin.
- Para yazan hiçbir işlemde "muhtemelen bu kişidir" varsayımı yapılmaz.
  Şüphe varsa sor. Yanlış kişiye borç yazmak, bir soru sormaktan çok daha
  pahalıdır.
- Bu mantık testlerle korunsun: "furkan yılmaz vs furkan duman" senaryosu
  ve "tek kelime iki adaya uyuyor" senaryosu tests/test_intent_resolver.py'de
  bulunmalı.
