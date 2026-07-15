# Hesaplık

Sesli ve yazılı komutla çalışan cari hesap takip sistemi.
Borç ve tahsilat kaydı, kişi bazlı bakiye, ürün kalemli defter.

## Tasarım ilkeleri

1. **Defter append-only.** `transactions` üzerinde UPDATE/DELETE veritabanı
   tetikleyicisiyle yasaklıdır. Düzeltme, karşıt kayıt (`reverses_id`) ile yapılır.
2. **Para matematiği yalnızca `app/services/ledger.py` içinde ve `Decimal` ile.**
   float kullanımı yasak.
3. **Bakiye kolonda tutulmaz**, onaylı hareketlerin toplamıdır.
4. **Yapay zekâ hesap yapmaz.** Yalnızca niyet çıkarır; tutar, eşleştirme ve
   kayıt bu servisin işidir.

## Kurulum

    cp .env.example .env      # POSTGRES_PASSWORD'ü değiştir
    docker compose up -d
    curl localhost/api/health

GPU'yu sunucuya taktığında:

    docker compose --profile gpu up -d

## Testler

    docker compose up -d db
    export TEST_DSN=postgresql+asyncpg://hesaplik:PAROLA@localhost:5432/hesaplik_test
    pytest -q

## Yol haritası

- [x] Faz 0 — defter çekirdeği, şema, testler
- [ ] Faz 1 — PWA (React + TS), üç ekran
- [ ] Faz 2 — yedekleme (pgBackRest + restic + restore testi)
- [ ] Faz 3 — kural tabanlı parser + Telegram bot
- [ ] Faz 4 — LLM router (vLLM / kural fallback)
- [ ] Faz 5 — sesli komut (Whisper)
- [ ] Faz 6 — PDF rapor, Loki/Grafana, /engine
