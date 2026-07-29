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

## Telegram sorgu komutları (regex, Faz 3 devamı)

Kayıt kadar sorgulama da Telegram'dan yapılır. Kural tabanlı (LLM yok),
sonuç Telegram'a METİN liste olarak döner.

Desteklenecek sorgular (esnek kalıp, ek/yazım toleranslı):
- "kişileri listele" / "kişileri sırala" → tüm kişiler + bakiye + kısa kalem
- "borçluları listele" → bakiyesi + olanlar (kişi sana borçlu)
- "alacaklıları listele" → bakiyesi − olanlar (sen borçlusun / peşin ödeyen)
- "{ilçe}lileri listele" → o ilçedeki kişiler (ör. "bergamalıları listele",
  "ahmetbeylerlileri listele"). İlçe adı Türkçe ekle çekimli gelebilir
  (-li/-lı/-lu/-lü + -leri/-ları); eki soyup persons.district ile eşleştir.
- "{isim} borcunu söyle" / "{isim} borcu ne kadar" → tek kişi bakiye+kalem
  (mevcut bakiye sorgusu, kişi eşleştirme güvenlik kurallarıyla)

Sıralama: bakiye büyükten küçüğe (en çok borçlu üstte). Liste uzunsa
Telegram mesaj sınırına (4096 karakter) dikkat, gerekirse parçala.
Format: mono hizalı değil, sade okunur — "Ad Soyad — 1.500 TL borçlu".
Para tutarları Türkçe biçim (1.500,00 TL). Boş sonuç → "Kimse yok" tarzı
nazik mesaj.

Backend: bu filtreler API'de de işe yarar, ledger/servis katmanına
eklensin (ilçeye göre, borçlu/alacaklı filtresi), bot ve web ikisi de
kullanabilsin.

## Faz 4 — LLM (Ollama, sonra vLLM)

Regex'in sınırına gelindi. "anlamazsa sor", serbest cümle, esnek varyasyon
= LLM işi. Kural parser KALDIRILMAZ — hızlı yol olarak kalır, LLM fallback
olur.

**Akış:** mesaj → kural parser dener (15 ms) → çözerse kaydet → çözemezse
LLM'e gönder (Ollama/vLLM) → LLM JSON çıkarır → BİZİM KOD doğrular
(kişi pg_trgm, ürün catalog, tutar sayı mı) → net ve güvenliyse kaydet,
değilse "bunu mu demek istediniz?" diye sorar. LLM asla son sözü söylemez,
öneri sunar; karar kod + kullanıcıda.

**Provider soyutlaması (kritik):**
- LLMProvider Protocol: parse(text) -> ParsedIntent. Somut sınıflar:
  OllamaProvider (base_url, model), sonra VllmProvider, ve RuleProvider
  (her zaman açık taban).
- Hangi provider kullanılacağı config'ten (inference.yml veya .env), koddan
  DEĞİL. Laptop→Ollama, 2080 Super→vLLM geçişi tek satır config.
- Model/URL .env'den: LLM_PROVIDER=ollama, OLLAMA_URL=http://localhost:11434,
  LLM_MODEL=qwen2.5:7b. Ollama yoksa/erişilemezse sistem kural parser +
  "elle gir" ile çalışmaya devam eder, ÇÖKMEZ.

**Prompt:** few-shot (örnekli) sistem prompt'u. Kişi adı eklerini temizle,
birim ile para birimini karıştırma, emin olmadığını null bırak. Ollama
--format json ile JSON zorlanır. Prompt kod içinde sabit bir dosyada
(app/services/llm_prompt.py veya .txt), kolay düzenlenebilir.

**Güven ve onay:** LLM çıktısındaki her alan kod tarafından doğrulanır.
Kişi eşleşmezse veya çok aday varsa → sor (mevcut NEEDS_CONFIRMATION).
Ürün/tutar/tür belirsizse → "bunu mu demek istediniz: ... ?" + Evet/düzelt.
Kural parser güvenli sayılır (doğrudan kaydeder), LLM sonucu düşük güven
sayılır (kritik alanlarda onay ister).

**Hız:** işlemcide 3-8 sn/cümle (model bellekte kalırsa). 2080 Super +
vLLM'de ~0.3 sn. Kural parser'ın çözdüğü mesajlar LLM'e hiç gitmez, o
yüzden çoğu mesaj yine hızlı.

**Ses (Faz 5, sonra):** aynı GPU'ya faster-whisper. Ses → metin → yukarıdaki
akış. Whisper de provider (STTProvider) arkasında. GPU gelince eklenecek.

## Faz 4b — PDF Raporlar

Üç rapor türü, ortak sabit şablon (hepsi aynı tarzda). reportlab + DejaVu
font (Türkçe). Kaynak taslak onaylandı, app/services/report.py olacak.

**Şablon (ReportDoc):** lacivert üst bant (#1e3a5f) — sol: işletme adı
(settings.business_name) + "Cari Hesap Raporu", sağ: rapor başlığı + alt
başlık. Kişi ekstresinde alt başlık = kişi adı, BÜYÜK ve beyaz (vurgu="alt").
Alt bilgi: oluşturulma tarihi, "Hesaplık", sayfa no. Renkler: NAVY başlık,
BORC #c0261d kırmızı, ALACAK #1d6b3c yeşil, ZEBRA satır. Tutarlar Türkçe
biçim (1.500,00 ₺). Az ama anlamlı renk.

**Üç rapor:**
1. Günlük: bugün (veya verilen gün) kaydedilen hareketler. Sütun: Saat/
   Kişi/Ürün/Miktar/Tür/Tutar. Özet kutuları: kayıt sayısı, toplam borç,
   toplam tahsilat, net.
2. Kişi ekstresi: bir kişinin tüm hareketleri + yürüyen bakiye sütunu.
   Üstte kişi bilgi şeridi (ad, tel, ilçe/il) + güncel bakiye + açık
   kalemler. Sütun: Tarih/Açıklama/Birim fiyat/Tür/Tutar/Bakiye.
3. Genel durum: tüm kişiler, borçlu çoktan aza sıralı. Sütun: Kişi/İlçe/
   Açık kalem/Durum/Bakiye. Özet: kişi sayısı, toplam alacak, toplam borç,
   net alacak.

**Nereden:**
- Telegram: "rapor ver" → günlük/genel butonla seç. "{isim} ekstresi" veya
  "{isim} raporu" → kişi ekstresi (kişi eşleştirme güvenlik kurallarıyla).
  PDF dosyası + KISA metin özet birlikte gönderilir.
- Web: her rapor için buton (ana ekranda veya kişi detayında), tıkla → PDF
  indir. Kişi ekstresi kişi detayında, diğerleri ana ekranda/ayarlarda.

**Teknik:** reportlab pyproject.toml'a. Font yolu sabit değil, birkaç
olası yol denensin (DejaVu Fedora/Debian'da farklı yerde olabilir), yoksa
anlamlı hata. PDF bellekte üretilip (BytesIO) Telegram'a gönderilir ve/veya
web'de indirilir; diske yazmak şart değil.

## Rapor komutları — gelişmiş anlama (regex + LLM)

Kullanıcı raporu çok farklı şekillerde ister; hepsi anlaşılmalı, anlaşılmazsa
LLM devreye girmeli.

**Üç rapor niyeti:**
- report_general: "genel rapor", "tüm zamanların raporu", "herkesin durumu",
  "genel durum raporu", "bütün müşteriler raporu" → genel durum PDF
- report_daily: "günün raporu", "bugünün raporu", "gün raporu", "bugün ne
  yaptık", "günlük rapor", "bugünkü hareketler" → günlük PDF
- report_person: "{isim} ekstresi/raporu/dökümü/hesap dökümü" → kişi ekstresi
- report_menu: sadece "rapor" / "rapor ver" (hangisi belli değil) → günlük mü
  genel mi diye butonla sor

**Regex:** yaygın kalıpları ve Türkçe ekleri tanı (rapor/raporu/raporunu,
ekstre/ekstresi/ekstresini, döküm/dökümü). Kesin çözülenler doğrudan, "rapor"
tek başına → menü.

**LLM:** regex çözemezse rapor niyetlerini de LLM tanımalı. llm_prompt.py'ye
rapor örnekleri eklensin: "bana genel bir rapor çıkar" → report_general,
"bugün neler olmuş göster" → report_daily, "ahmetin hesap dökümünü ver" →
report_person(ahmet). LLM islem alanına "rapor" ekle, tür (genel/gunluk/kisi)
ve kişi (varsa) döndürsün. Belirsizse ("rapor" tek başına) → menü.

Kişi ekstresi her zaman kişi eşleştirme güvenlik kurallarından geçer.

## Bot "yazıyor..." göstergesi

LLM işlemcide yavaş (~13 sn). Kullanıcı beklerken bot donmuş görünmesin:
- Uzun sürebilecek her işlemde (LLM parse, rapor üretimi) Telegram'a
  "typing" chat action gönder (send_chat_action ACTION_TYPING). Cevap
  gelene kadar tekrarlanır (Telegram typing ~5 sn sürer, uzun işlemde
  periyodik yenilenmeli).
- Regex'in anında çözdüğü komutlarda gösterge gereksiz (zaten hızlı), ama
  zararı yok; basitlik için LLM'e düşen ve rapor üreten yollarda göster.
- PDF gönderirken "upload_document" action kullanılabilir.

## KRİTİK — LLM isim bozuyor (2026-07-27)

Ciddi buglar tespit edildi, hepsi LLM'in Türkçe ek temizlemesinden:
1. "mehmetten 5000 aldım" → LLM ismi "mehtap" olarak uydurdu. İSİM
   DEĞİŞTİRME kabul edilemez, para/kişi güvenliği ihlali.
2. "ahmetin durumu ne" → "ahmetin" eki temizlenemedi, kişi bulunamadı.
   Ama "ahmet in" (boşluklu) çalıştı. Tutarsız.
3. Aynı isim bazen bulunuyor bazen "hangisini?" soruyor — kararsız.

**Kural: İsim temizlemeyi LLM'E BIRAKMA.** LLM ham metinden ismi
çıkarabilir ama Türkçe ekleri (iyelik -in/-ın/-nin, ayrılma -den/-dan/-ten/
-tan, yönelme -e/-a/-ye/-ya) KOD tarafından temizlenir, LLM tarafından
değil. LLM'in döndürdüğü isim, kod içinde normalize edilip pg_trgm ile
eşleştirilir. LLM asla harf ekleyip çıkaramaz (mehmet→mehtap gibi).

**Uygulama:**
- app/services/catalog.py veya yeni bir isim-normalize modülü: Türkçe ek
  soyma fonksiyonu. Kişi adının son kelimesindeki yaygın ekleri sök
  (ahmetten→ahmet, mehmetin→mehmet, aliye→ali). Ek listesini kapsamlı tut.
- intent_resolver kişi eşleştirmede bu normalize'i HEM regex HEM LLM
  sonucuna uygular. LLM ne döndürürse döndürsün, kod ekini temizler.
- Ünsüz yumuşaması dikkat: "mehmedin"→"mehmet" (d→t), "ahmedin"→"ahmet".
- Eşleştirme yine pg_trgm güvenlik kurallarıyla (soyad ayrımı, çoklu aday).

## Tek mesajda birden çok istek

Kullanıcı bir mesajda birden çok işlem yazabilir ("mehmetten 5000 aldım
ali veliye 500 mal gitti"). Sistem:
- Mesajı cümlelere/işlemlere böl (satır sonu, "ve", ayrı fiiller).
- Her işlemi SIRAYLA işle, her biri için AYRI cevap/onay gönder.
- İlk işlemi yap + bilgilendir, sonra ikinciyi yap + bilgilendir.
- Bölme belirsizse tek işlem sayıp normal akışa devam et (aşırı bölme
  yapma, yanlış bölmektense tek bırak).

## LLM son çare, regex birincil (2026-07-27 mimari kararı)

Test sonucu: LLM işlemcide çok yavaş (2 dk) ve hatalı — "aldım"ı borç
sandı, "3bin"i 3.000.000 yaptı, "verdim"i anlamadı. Bu kalıplar DÜZENLİ,
regex'le anında ve DOĞRU çözülmeli. LLM'e sadece gerçekten serbest/belirsiz
cümlelerde başvurulur.

**Kural: Mümkün olan HER kalıbı regex'e ekle. LLM son çare.**
Kayıt, tahsilat, bakiye, liste, rapor komutlarının yaygın tüm biçimleri
regex'te olmalı. LLM yalnızca regex'in tamamen çözemediği (None döndürdüğü)
serbest ifadeler için devreye girer.

**Regex'in kesin çözmesi gerekenler (LLM'e gitmemeli):**
- Yön: "kişiDEN aldım/tahsil ettim" = TAHSİLAT (para bana geldi).
  "kişiYE verdim/borç/sattım/çıktı" = BORÇ (mal/para ona gitti).
  "kişi X aldı" (3. şahıs) = BORÇ (o aldı, bana borçlandı).
  Bu ayrım koddadır, LLM'e bırakılmaz — para yönü kritik.
- Türkçe sayı: "3bin"/"3 bin"=3000, "5bin"=5000, "10bin"=10000,
  "yüz"=100, "ikiyüz"=200, "bin beşyüz"=1500, "2buçuk"=2.5.
  Bitişik/ayrık yazımlar, "bin/yüz/milyon" çarpanları. Kod parse eder.
- Para vs adet: "3bin lira/tl" = tutar, "20 balya/kilo" = adet+birim.

**Donanım stratejisi (provider seçimi config'ten):**
- Yerel Ollama (laptop/sunucu işlemci): yavaş yedek.
- 2080 Super vLLM (host): hızlı birincil, hazır olduğunda.
- Öncelik: 2080 Super çalışıyorsa onu kullan; timeout/erişilemezse yerel
  Ollama'ya düş; o da olmazsa regex + "elle gir". Katmanlı fallback.
- Kod değişmez, provider config'ten seçilir (mevcut soyutlama).

## Telegram'dan kişi eklerken detay sorma

Telegram'dan yeni kişi oluşturulunca (borç/tahsilat sırasında "yeni kişi
ekle" seçilince) sadece isim kaydediliyordu; il/ilçe/telefon boş kalıyordu.
Kişi kartı eksik olunca ilçe filtreleri ("bergamalıları listele") çalışmaz.

**Akış:** yeni kişi eklenirken adım adım (conversational) sor:
1. Ad soyad (zaten cümleden çıkarılan isim; onayla veya düzelt).
2. Telefon (opsiyonel, "geç" ile atlanabilir).
3. İl (opsiyonel).
4. İlçe (opsiyonel).
Her adımda "geç"/"atla" butonu olsun (zorunlu değil, sadece isim yeterli).
Bilgiler toplanınca kişi oluşturulur, SONRA bekleyen borç/tahsilat işlenir.

Basit tutulmalı: soru-cevap akışı, kullanıcı istemezse "geç" der, minimum
isimle kaydeder. Aşırı zorlama yok — hızlı giriş için "hepsini geç" imkânı.
State bot tarafında (ConversationHandler veya chat_data) tutulur.

## Kişi bilgi sorgusu + hitap kelimeleri

**"{kişi} bilgi ver / bilgileri / kim / kimdir"** → kişinin kartını göster:
ad soyad, telefon, il/ilçe, güncel bakiye, açık kalemler. Bakiye
sorgusundan farkı: iletişim/konum bilgisi de gösterilir. Yeni niyet:
person_info. Kişi eşleştirme güvenlik kurallarıyla (çoklu aday → sor).

**Hitap kelimeleri isimden ayıklanır:** "abla, abi/ağabey, bey, hanım,
amca, dayı, teyze, hala, usta, hoca, efendi, kardeş" gibi hitaplar isim
değildir. "esma abla" → "esma", "ahmet usta" → "ahmet", "mehmet bey" →
"mehmet". Bu ayıklama kişi adı çıkarımında (hem regex hem LLM sonrası)
yapılır, "hesabının/durumu" gibi bağlam kelimeleriyle aynı temizleme
katmanında. Dikkat: gerçek isimle karışmasın (nadiren isim olabilir ama
hitap olarak kullanımı baskın; sona geldiğinde ayıkla).

## DÜZELTME — "bilgi ver" belirsiz, SOR (2026-07-28)

Önceki karar "bilgi ver → kişi kartı" yanlıştı. "bilgi ver" belirsiz:
kullanıcı bakiye/borç de kastedebilir, iletişim bilgisi de. Bot VARSAYMAZ,
sorar (anlamıyorsa sor ilkesi):

"{kişi} bilgi ver / bilgi / bilgileri" → belirsiz → bot sorar:
  "Ne bilgisi?" + butonlar:
  [Bakiye / borç]  [Kişi bilgileri]  [Ekstre (PDF)]
Seçime göre:
  - Bakiye/borç → mevcut bakiye sorgusu (bakiye + açık kalemler)
  - Kişi bilgileri → ad/telefon/il/ilçe kartı
  - Ekstre → kişi ekstresi PDF

Net niyetler doğrudan çalışır (sormadan):
  "{kişi} bakiyesi/borcu/durumu" → bakiye
  "{kişi} telefonu/numarası/adresi/nerede" → kişi bilgileri
  "{kişi} ekstresi/dökümü" → ekstre PDF
Sadece belirsiz "bilgi" için seçim sorulur.

## Bot sorgu anlama — kapsamlı genişletme (2026-07-28, Grup 1)

Kullanıcı botun HER sorgu türevini anlamasını istiyor. Regex birincil,
anlaşılmayan LLM'e. Tüm bu kalıplar REGEX'te olmalı (LLM'e gitmemeli):

**Bakiye (hepsi aynı sonuç — bakiye + açık kalemler tablosu):**
"furkan bakiye", "furkan borc/borç", "furkan borcu ne", "furkan durum",
"furkan durumu", "furkan durumu ne", "furkan durum ne", "furkan hesap",
"furkan cari/cariye", "furkan güncel bakiye", "furkan alacak/alacağı",
"furkan toplam borç", ve TERS SIRA: "durum furkan", "bakiye furkan".
Kelime sırası esnek: {isim} {anahtar} veya {anahtar} {isim}.

**Bakiye çıktısı TABLO halinde:** id/tarih/ürün/adet/fiyat/tutar sütunlu,
sonda güncel bakiye. Telegram'da monospace hizalı okunur liste.

**Bilgi menüsü (belirsiz):** "furkan bilgi" → "Ne bilgisi?" +
[Bakiye/borç] [Kişi bilgileri] [Ekstre PDF] butonları (zaten var, koru).

**Ekstre/döküm:** "furkan hesap dökümü", "furkan dökümanı", "furkan ekstre",
"furkan güncel bakiye pdf" → kişi ekstresi PDF.

**Listeler:** "kişiler"/"kişileri say"/"sistemdeki kişiler"/"tüm kişiler" →
herkes + güncel bakiye. "bergamalılar"/"bergama" (tek kelime) → o ilçe.

**Telegram arama gibi davransın:** tek kelime yazınca:
- "ahmet" (tek başına, komut yok) → tüm Ahmet'leri listele ("hangi ahmet?"
  değil, hepsini bakiyeleriyle göster — arama gibi)
- "duman" → soyadı Duman olanları listele
- "bergama" → Bergama ilçesindekiler
Yani tek kelime = arama: isimde/soyadda/ilçede eşleşenleri listele.

Anlaşılmayan her şey LLM'e (qwen) düşer. Regex kapsamı geniş olmalı ki
LLM nadiren devreye girsin (yavaş).
