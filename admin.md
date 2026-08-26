## Admin Panel — İskelet + İşlem Akışı İzleme (2026-08-02, Faz 7)

Mevcut web içinde /admin sayfası. Tek admin şifresi (.env: ADMIN_PASSWORD).
Amaç: sistemi tek yerden kontrol/izleme. Bir sorun olduğunda buraya bakmak
yeterli olsun.

**Koruma:** basit şifre. Giriş sayfası → şifre doğruysa admin oturumu
(token/cookie). ADMIN_PASSWORD .env'de. Yanlış şifre → giremez. API
tarafında /api/admin/* uçları şifre/token kontrolü ister (korumasız veri
sızmaz).

**İskelet — sol menü (bölümler), sağ içerik:**
Yalnızca "Kontroller" (yazma işlemi — LLM aç/kapat, model, yedek) henüz
placeholder; diğer tüm bölümler dolu:
1. İşlem Akışı (DOLU) — müşteri ne dedi → sistem ne algıladı → ne yaptı
2. Sistem Sağlığı (DOLU) — api/bot/db/ollama/yedek durumu
2b. Yedekleme (DOLU, geri yükleme AŞAMALI) — yedek listesi + "ana veri yap"
3. LLM İzleme (DOLU) — çağrılar, süreler, başarı
4. İstek Kuyruğu (DOLU) — pending_requests durumları
5. Loglar (DOLU) — audit_log denetim kaydı
6. Kişiler & İşlemler (DOLU) — salt okunur veri gezgini + arşiv
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

**LLM İzleme (GET /api/admin/llm-monitor, 2026-08-26):**
"LLM Yönetimi"nden (yukarıdaki switch — vLLM/Ollama tercihi) AYRI bölüm: bu
bir analitik. raw_messages'ta yalnızca `parse_source='llm'` düşen (regex'in
çözemediği, yavaş yola giden) satırlar listelenir. Üstte özet karo şeridi:
toplam çağrı, ortalama süre (parse_ms), başarı oranı, başarılı/başarısız
sayısı. "Başarı" = LLM kullanılabilir sonuç üretti (kaydedildi/yanıtlandı/
soru soruldu); "başarısızlık" = hata veya anlaşılamadı. İstatistik
FİLTRELENMİŞ kümenin tamamı üzerinden, yalnız görünen sayfa değil. Filtre:
sonuç, tarih aralığı.

**İstek Kuyruğu (GET /api/admin/queue, 2026-08-26):**
pending_requests (CLAUDE.md > "Çoklu istek — kalıcı istek kuyruğu").
Üstte durum sayıları (beklemede/işleniyor/tamamlandı/başarısız/iptal,
filtreden bağımsız — kuyruğun tamamı), altta filtrelenebilir liste
(metin, sıra no, durum, sonuç/hata, zaman).

**Kişiler & İşlemler (GET /api/admin/persons, /persons/{id}/transactions,
/archived-persons, /archived-transactions, 2026-08-26):**
Salt okunur veri gezgini, hiçbir yazma işlemi yok. İki sekme:
- Aktif defter: kişi listesi (public /api/persons ile aynı sorgu —
  `queries.list_persons_with_balance`), bakiye, açık kalemler, arama/
  kapsam filtresi (hepsi/borçlular/alacaklılar). Satıra tıklayınca o
  kişinin TÜM hareketleri (durumu ne olursa olsun, salt okunur denetim
  amaçlı) satır içinde açılır.
- Arşiv: "Sil" = arşivle kararının (CLAUDE.md) denetim görünümü. İki alt
  sekme: silinen kişiler (archived_persons — arşivlenen bakiye, sebep, kim/
  ne zaman) ve silinen hareketler (archived_transactions, kişiye
  daraltılabilir).

**Loglar (GET /api/admin/audit-log, 2026-08-26):**
audit_log tablosu: kim/ne zaman/neyi değiştirdi (borç/tahsilat ekleme,
ters kayıt, hareket/kişi arşivleme, kişi düzenleme, geri yükleme isteği —
hepsi buraya yazar). Container stdout logları DEĞİL, yalnızca DB denetim
kaydı. Filtre: kim (actor), işlem (action), varlık (entity), tarih. Satıra
tıklanınca before/after JSON'ı açılır.

**Tasarım:** profesyonel, koyu mod (mevcut tema), okunur, yoğun bilgi ama
dağınık değil. Mevcut web'in stiliyle tutarlı (Layout, renkler).