## Admin Panel — İskelet + İşlem Akışı İzleme (2026-08-02, Faz 7)

Mevcut web içinde /admin sayfası. Tek admin şifresi (.env: ADMIN_PASSWORD).
Amaç: sistemi tek yerden kontrol/izleme. Bir sorun olduğunda buraya bakmak
yeterli olsun.

**Koruma:** basit şifre. Giriş sayfası → şifre doğruysa admin oturumu
(token/cookie). ADMIN_PASSWORD .env'de. Yanlış şifre → giremez. API
tarafında /api/admin/* uçları şifre/token kontrolü ister (korumasız veri
sızmaz).

**İskelet — sol menü (bölümler), sağ içerik:**
Tüm bölümler menüde görünür ama şimdilik ilk ikisi dolu, diğerleri
"yakında" placeholder:
1. İşlem Akışı (DOLU) — müşteri ne dedi → sistem ne algıladı → ne yaptı
2. Sistem Sağlığı (DOLU) — api/bot/db/ollama/yedek durumu
2b. Yedekleme (DOLU, geri yükleme AŞAMALI) — yedek listesi + "ana veri yap"
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

**Sistem Sağlığı bölümü (GET /api/admin/health):**
Bir sorun olduğunda tek bakışta "ne ayakta, ne değil". Beş bileşen, her biri
durum (ok/uyarı/hata/bilgi) + tek satır özet + ayrıntı:
- Veritabanı: SELECT 1, kişi/işlem sayısı, son işlem zamanı. Erişilemezse HATA.
- LLM (Ollama): LLM_PROVIDER=none ise "kapalı" (bilgi, hata değil). ollama ise
  /api/tags'e 3 sn timeout'la gidilir; erişilemezse HATA, ayarlı model listede
  yoksa UYARI.
- Telegram botu: DOLAYLI. Bot ayrı container'da, süreç durumu görülemez —
  raw_messages'taki son telegram mesajından çıkarılır (15 dk içinde mesaj =
  aktif, yoksa "sessiz", hata değil). Kartta "dolaylı ölçüm" etiketi görünür.
- Yedekleme: restic snapshots (mevcut backup.list_snapshots). Son yedek 60
  dakikadan eskiyse UYARI (timer 5 dk'da bir çalışıyor), depo okunamazsa UYARI.
- API: bu yanıtın kendisi + süreç ömrü + sürüm.
Genel özet üstte tek cümle ("Tüm sistemler çalışıyor" / "1 uyarı var").
Arayüz 30 sn'de bir kendiliğinden yeniler, elle "Yenile" de var.

**Yedekleme bölümü (GET /api/admin/backups, POST /api/admin/backups/restore):**
Sistem Sağlığı'ndaki tek satırlık yedek özetinden ayrı, tam bölüm.
- Liste: her yedek için tarih/saat, restic kısa kimliği (short_id — komut
  satırındaki `restic dump <id>` ile aynı), boyut, konum (yol + host), etiket.
  En yeni üstte. Boyut restic vermezse null → ekranda "—", 0 yazılmaz.
- Özet: toplam yedek, son yedek, depo yolu (RESTIC_REPOSITORY), sonraki
  otomatik yedek TAHMİNİ (son yedek + 5 dk; timer host'ta, ölçülemez).
- Depoya erişilemezse 503 + sebep — "yedek yok" ile karıştırılmaz.

**"Ana veri yap" (geri yükleme) — "Yol A": panel İSTER, host UYGULAR.**
Satırdaki buton → uyarı modalı ("mevcut veri önce otomatik yedeklenecek,
sonra üstüne yazılacak") → yönetim şifresi TEKRAR istenir → "EVET, ANA VERİ
YAP". Sunucu şifreyi doğrular, `restore_requests`e `bekliyor` bir istek +
audit_log kaydı yazar ve "istek alındı" der. **API pg_restore çalıştırmaz**
(container'da docker yok, depo salt okunur); işi host'taki izleyici
(`scripts/restore-apply.sh`, systemd) yapar.
- Yanlış şifre 403 (401 DEĞİL: 401 paneli şifre ekranına atardı, oysa oturum
  geçerli). Deneme sayısı giriş ekranıyla aynı kilide takılır.
- Süren restore varken ikincisi 409. Asıl garanti kısmi tekil indeks
  (`uq_restore_tek_aktif`) — eşzamanlı istek veritabanınca reddedilir.
- Durum: `GET /api/admin/backups/restore/status`. Panel süren iş varken 3
  sn'de bir sorar, adımları gösterir: Bekliyor → Yedekleniyor → Yükleniyor →
  Tamamlandı (ya da Hata + sebep). Bitince yedek listesi tazelenir, güvenlik
  yedeği listede rozetle görünür.
- İzleyici kurulu değilse istek `bekliyor` kalır; birkaç dakika sonra panel
  "izleyici çalışmıyor olabilir" uyarısı gösterir (`stale`, dolaylı sinyal).

**Ölçülemeyen uydurulmaz.** Host diski, systemd timer'ı, bot sürecinin
canlılığı API container'ının içinden görülemez; bunlar ya dolaylı gösterilir
(measured=false + açıklama) ya da hiç gösterilmez. CPU/RAM/disk kartı YOK.

**Tasarım:** profesyonel, koyu mod (mevcut tema), okunur, yoğun bilgi ama
dağınık değil. Mevcut web'in stiliyle tutarlı (Layout, renkler).