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