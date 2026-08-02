## Admin Panel — İskelet + İşlem Akışı İzleme (2026-08-02, Faz 7)

Mevcut web içinde /admin sayfası. Tek admin şifresi (.env: ADMIN_PASSWORD).
Amaç: sistemi tek yerden kontrol/izleme. Bir sorun olduğunda buraya bakmak
yeterli olsun.

**Koruma:** basit şifre. Giriş sayfası → şifre doğruysa admin oturumu
(token/cookie). ADMIN_PASSWORD .env'de. Yanlış şifre → giremez. API
tarafında /api/admin/* uçları şifre/token kontrolü ister (korumasız veri
sızmaz).

**İskelet — sol menü (bölümler), sağ içerik:**
Tüm bölümler menüde görünür ama ŞİMDİLİK sadece "İşlem Akışı" dolu, diğerleri
"yakında" placeholder:
1. İşlem Akışı (DOLU) — müşteri ne dedi → sistem ne algıladı → ne yaptı
2. Sistem Sağlığı (placeholder) — api/bot/db/ollama durumu
3. LLM İzleme (placeholder) — çağrılar, süreler, başarı
4. İstek Kuyruğu (placeholder) — pending_requests durumları
5. Loglar (placeholder) — bot/api log
6. Kişiler & İşlemler (placeholder) — veri yönetimi
7. Kontroller (placeholder) — LLM aç/kapat, model, yedek

**İşlem Akışı bölümü (ilk gerçek içerik):**
Her gelen mesaj için uçtan uca izleme. Kaynak veriler:
- raw_messages: müşteri ne yazdı (ham metin, tarih, chat_id)
- Parse sonucu: sistem ne algıladı (kind, kişi, tutar, ürün — LLM mi regex mi)
- transactions/audit_log: sistem ne yaptı (kayıt, arşiv, düzenleme)
Görünüm: zaman sıralı liste. Her satır: [tarih] "ham metin" → algılanan
(borç/tahsilat, kişi, tutar) → sonuç (kaydedildi/soru soruldu/hata).
Detaya tıklanınca: tam parse çıktısı, LLM mi regex mi, süre, hangi işlem
oluştu. Filtre: tarih, tür (kayıt/sorgu/hata), kişi. Sayfalama.

**Tasarım:** profesyonel, koyu mod (mevcut tema), okunur, yoğun bilgi ama
dağınık değil. Mevcut web'in stiliyle tutarlı (Layout, renkler).