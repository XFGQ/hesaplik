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
