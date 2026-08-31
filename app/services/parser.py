"""Kural tabanlı Türkçe komut çözümleyici (Faz 3, adım 2).

LLM yok: yalnızca regex + sözlük eşleştirme. Girdi ham metin, çıktı
ParsedIntent ya da (anlaşılamadıysa) None. Belirsiz kalan alan uydurulmaz,
boş bırakılır — kararı intent_resolver ve bot verir.

Desteklenen kalıplar (kelime sırası biraz oynayabilir):
  Borç:     "{isim} {sayı} {birim} {ürün} aldı {tutar} tl borç"
            varyantlar: "aldı", "verdim/verdik", "çekti", "sattım",
            "çıktı", "gitti", "borç yaz(dı)"
  Tahsilat: "{isim} {tutar} tl (ödedi|verdi|yatırdı|geldi)"
            varyantlar: "{isim}den/dan {tutar} aldım/aldık",
            "{isim}den tahsil ettim"
            ürünlü:    "{isim} {sayı} {birim} {ürün} parası ödedi {tutar} tl"
  Yön ayrımı fiil çekiminde: "aldı" (o aldı) = borç, "aldım/aldık" (ben
            aldım) = tahsilat — bkz. DEBT_WORDS/PAYMENT_WORDS.
  Tutar/adet: birim kelimesi (balya/kg/...) yoksa sayı ADET değil TUTAR
            sayılır ("mehmete 3000 verdim" -> amount=3000, qty=None).
  Türkçe sayı: "3bin"/"15bin"=3000/15000, "ikiyüz"=200, "3buçuk"=3.5,
            "birmilyon"=1000000 (bkz. parse_turkish_number, _consume_number).
  Bakiye:   "{isim} (borcu|borcunu|hesabı|hesabını|bakiyesi|bakiyesini|
            durumu|durumunu) [ne|nedir|kaç|ne kadar|söyle|göster]"
            varyant:   "{isim} ne kadar borcu var"
  Kişi bilgisi (CLAUDE.md > "DÜZELTME — 'bilgi ver' belirsiz, SOR"):
            "{isim} telefonu/numarası/adresi/nerede oturuyor" -> net niyet,
              person_contact (doğrudan kişi kartı gösterilir, sorulmaz).
            "{isim} bilgi ver/bilgi/bilgileri" -> belirsiz, info_menu (bot
              bakiye/kişi bilgileri/ekstre arasında SORAR).
  Sorgu:    "kişileri/insanları/müşterileri/herkesi/hepsini listele/göster/
              getir" -> list_all (bkz. LIST_VERBS, LIST_ALL_WORDS)
            "borçluları listele" -> list_debtors
            "alacaklıları listele" -> list_creditors
            "{ilçe}lileri listele" -> list_district (ör. "bergamalıları listele")
  Sorgu (Grup 1, CLAUDE.md > "Bot sorgu anlama — kapsamlı genişletme"):
            "kişiler" / "insanlar" / "müşteriler" / "kişileri say" /
              "sistemdeki kişiler" / "tüm kişiler" / "kimler var" / "listele"
              (tek başına) / "kişi listesi" / "müşteri listesi" -> list_all
              (fiilsiz, bkz. _try_bare_list_query)
            "borçlular" / "kim borçlu" / "borçlu olanlar" -> list_debtors,
            "alacaklılar" / "kim alacaklı" / "alacaklı olanlar" ->
              list_creditors (fiilsiz, bkz. _try_bare_list_query)
            "bergamalılar" / "bergamadakiler" / "bergamadaki" (fiilsiz, tek
              kelime) -> list_district (bkz. _try_bare_district_query,
              _district_from_word — hem "-lı/-lar" çekim eki hem "-daki/
              -deki" bulunma hâli eki çözülür)
            "{isim} bakiye/borç/borc/borçlu/durum/hesap/cari/cariye/alacak/
              alacağı" VEYA ters sıra "{anahtar} {isim}" -> balance_query
              (bkz. BALANCE_KEYWORDS_BARE, _try_bare_balance_query). Bunlar
              inflected (borcu/hesabı/...) hâllerden AYRI bir küme: bare
              "borç"/"borc" zaten hem bir tutar işaretçisi hem "debt" kind
              sinyali olduğu için (bkz. _AMOUNT_MARKERS, _detect_kind),
              BALANCE_KEYWORDS'e (inflected, guard'ın kullandığı küme)
              karıştırılırsa "...aldı 15000 tl borç" gibi normal borç
              cümleleri yanlışlıkla çelişki sayılıp None dönerdi.
            "{isim} ne kadar" (hiçbir anahtar kelime olmadan, sondan "ne
              kadar" ile biten cümle) -> balance_query (bkz.
              _try_bare_ne_kadar_query).
            Tek kelime (komut/fiil YOKSA) -> search (Telegram arama gibi,
              isim/soyad/ilçede geçen herkesi listeler, bkz. _try_single_word_search).
  Rapor (CLAUDE.md > "Rapor komutları — gelişmiş anlama"), en spesifikten
  en geneline sırayla denenir:
            "genel rapor" / "genel durum" / "tüm zamanların raporu" /
              "herkesin durumu" / "bütün müşteriler" / "tüm rapor" ->
              report_general
            "günlük rapor" / "günün raporu" / "bugünün raporu" /
              "bugünkü hareketler" / "bugün ne yaptık/oldu" -> report_daily
            "{isim} ekstresi/raporu/dökümü" / "{isim} hesap dökümü" ->
              report_person
            "rapor ver" / "rapor" (tek başına, tür belirsiz) -> report_menu
  Kişi oluşturma (CLAUDE.md > "Bot kayıt akışı — Grup 2"):
            "{isim} adlı/adında/isimli kişiyi sisteme kayıt et",
            "{isim} kişisini ekle", "{isim} sisteme/deftere ekle",
            "{isim} kayıt et/kaydet/oluştur/ekle", "yeni kişi {isim}" ->
              create_person. İsimden komut kelimeleri (adlı/kişiyi/sisteme/
              kayıt/et/ekle...) ayıklanır (bkz. _create_person_name); para
              ya da mal bağlamı olan cümleler bu niyete hiç düşmez (bkz.
              _CREATE_PERSON_BLOCKERS) — "ahmete 20 balya saman ekle" borç
              kaydıdır, kişi oluşturma değil.
  Kişi silme/arşivleme (CLAUDE.md > "Bot kişi silme = arşivleme — Grup 3"):
            "{isim} sil/kaldır/arşivle/sıfırla" -> archive_person (kişi
              GERÇEKTEN silinmez, arşive taşınır + pasifleştirilir).
            "{isim} sil/sıfırla yeniden oluştur/aç" -> archive_and_recreate
              (arşivle + aynı isimle temiz/bakiyesi sıfır yeni kişi açılır).
            "{isim} {adet} {ürün} borcunu ödedi sil" (silme fiili + para/mal
              bağlamı) -> delete_ambiguous: "tahsilat mı, kişiyi silmek mi?"
              diye SORULUR, sessizce kişi silinmez (bkz.
              _try_archive_person_query).
  Toplam bakiye:
            "tüm bakiye" / "toplam borç" / "toplam alacak" / "genel bakiye" /
            "sistemdeki toplam borç" / "total borç" / "güncel toplam" ->
            total_balance (defterin TAMAMININ özeti; "tüm"/"total" artık kişi
            adı sanılmaz — bkz. _try_total_balance_query).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.services.catalog import normalize

CURRENCY_UNITS = {"tl", "try", "lira", "₺"}

UNITS = {
    "balya", "kg", "kilo", "kilogram", "gram", "gr", "ton", "çuval",
    "adet", "litre", "lt", "paket", "koli", "kasa", "teneke", "varil",
    "metre", "m", "düzine", "kutu", "torba", "top", "dolap", "çift",
}

# Yön ayrımı (CLAUDE.md > "LLM son çare, regex birincil"): kişi eki değil,
# FİİL ÇEKİMİ zaten yönü kodluyor — "aldı" (3. şahıs, o aldı) = BORÇ ama
# "aldım/aldık" (1. şahıs, ben aldım) = TAHSİLAT. Bu ayrım kelime bazında
# net ve LLM'e bırakılamayacak kadar kritik (bkz. "mehmetten 5000 aldım").
DEBT_WORDS = {
    "aldı", "verdim", "verdik", "çekti", "sattım", "çıktı", "gitti",
    # "borçlandı" fiilin KENDİSİ yönü kodluyor: kişi borçlandı, yani mal/para
    # ona gitti ("ali 1000 borçlandı"). Türkçe karakter yazmayan kullanıcılar
    # için ASCII yazımları da aynı kümede.
    "borçlandı", "borclandi", "borçlandi", "borclandı",
}
PAYMENT_WORDS = {
    "ödedi", "yatırdı", "verdi", "aldım", "aldık", "geldi", "tahsil",
    # Borç KAPANIŞI da bir tahsilattır ("borcunu kapattı").
    "kapattı", "kapatti",
}
BALANCE_KEYWORDS = {
    "borcu", "borcunu", "hesabı", "hesabını", "bakiyesi", "bakiyesini",
    "durumu", "durumunu",
}
# Anahtar kelimeden önce ("ne kadar borcu var") ya da sonra ("borcu ne
# kadar", "borcunu söyle") gelebilen, isme dahil edilmemesi gereken dolgu
# kelimeleri. "toplam"/"güncel" de buraya dahil: "toplam borç"/"güncel
# bakiye" gibi nitelenmiş bare kalıplarda (bkz. BALANCE_KEYWORDS_BARE) isme
# karışmaması gerekir.
BALANCE_FILLERS = {"ne", "nedir", "kaç", "kadar", "söyle", "göster", "var", "toplam", "güncel"}

# Borç KAPANIŞI (2026-08-31 anlama genişletmesi): "borcu/borcunu" tek başına
# bir bakiye anahtar kelimesidir (BALANCE_KEYWORDS) ama yanında bir TAHSİLAT
# fiili varsa cümle bir sorgu değil, bir KAYITTIR — borç kapanıyor:
# "ali borcunu ödedi", "ahmet 20 saman borcunu ödedi", "mehmet borcunu
# kapattı", "ahmet 20 balya borcunu 15000 tl ödedi". Eskiden bu cümleler
# parse() içindeki "bakiye kelimesi + kayıt fiili" çelişki kontrolüne takılıp
# None dönüyor, yani her seferinde (yavaş) LLM'e devrediliyordu.
DEBT_CLOSING_KEYWORDS = {
    "borcunu", "borcu", "borcunun",
    "borçlarını", "borclarini", "borçları", "borclari",
}


def _strip_debt_closing(tokens: list[str]) -> list[str] | None:
    """"{isim} ... borcunu ödedi/kapattı" ise bakiye anahtar kelimesini
    cümleden düşürür ve kalan tokenları döndürür — böylece geri kalan normal
    TAHSİLAT akışından ("ödedi" -> payment) olduğu gibi geçer. Kalıp
    tutmuyorsa None döner ve hiçbir şey değişmez.

    Kasten dar: cümlede bir tahsilat fiili OLMALI ve bir borç fiili (verdim/
    sattım/aldı...) OLMAMALI. İkisi birden varsa yön çelişir, uydurulmaz —
    mevcut çelişki kontrolü devreye girer ve cümle LLM'e bırakılır."""
    token_set = set(tokens)
    if not (token_set & DEBT_CLOSING_KEYWORDS):
        return None
    if not (token_set & PAYMENT_WORDS) or token_set & DEBT_WORDS:
        return None
    return [t for t in tokens if t not in DEBT_CLOSING_KEYWORDS]


# Bare (çekimsiz) bakiye anahtar kelimeleri (CLAUDE.md > "Bot sorgu anlama —
# kapsamlı genişletme", Grup 1, madde 1): "furkan bakiye", "furkan durum",
# "furkan hesap", "furkan cari(ye)", "furkan alacak/alacağı". BİLEREK
# BALANCE_KEYWORDS'ten (inflected) AYRI bir küme: bare "borç"/"borc" aynı
# zamanda bir tutar işaretçisi (_AMOUNT_MARKERS) ve _detect_kind'in "debt"
# sinyali olduğu için, eğer bu ikisi aynı kümede olsaydı normal bir borç
# cümlesi ("ahmet ... aldı 15000 tl borç") hem bir bakiye anahtar kelimesi
# hem bir borç fiili içerdiği için (aldı + borç) çelişki sanılıp None
# dönerdi (bkz. parse() içindeki BALANCE_KEYWORDS/DEBT_WORDS çelişki
# kontrolü). Bu yüzden bare küme kendi ayrı, dikkatli fonksiyonuyla
# (_try_bare_balance_query) işlenir: borç/tahsilat fiili ya da tutar
# görülürse bare eşleşme hiç denenmez, debt/payment akışına bırakılır.
BALANCE_KEYWORDS_BARE = {
    "bakiye", "borç", "borc", "durum", "hesap", "cari", "cariye",
    "alacak", "alacağı", "borçlu", "borclu",
}

# Net iletişim/konum niyeti (CLAUDE.md > "DÜZELTME — 'bilgi ver' belirsiz,
# SOR"): "telefonu/numarası/adresi/nerede oturuyor" gibi somut bir istek
# varsa sorulmadan doğrudan kişi kartı gösterilir — yalnızca çıplak "bilgi
# ver" belirsizdir (bkz. INFO_MENU_KEYWORDS).
PERSON_CONTACT_KEYWORDS = {
    "telefonu", "telefonunu", "telefon",
    "numarası", "numarasını", "numarasi", "numarasini", "numara",
    "adresi", "adresini", "adres",
    "nerede", "nerde",
}

# Belirsiz "bilgi ver" isteği: kullanıcı bakiye mi, iletişim bilgisi mi
# istediğini belirtmemiş — bot VARSAYMAZ, üç seçenekli buton sorar
# (bkz. app/bot/main.py > info_menu akışı).
INFO_MENU_KEYWORDS = {"bilgi", "bilgisi", "bilgisini", "bilgiler", "bilgileri", "bilgilerini"}

# Toplam/genel bakiye (CLAUDE.md > "Toplam bakiye niyeti", 2026-08-31):
# "tüm bakiye", "toplam borç", "toplam alacak", "genel bakiye", "sistemdeki
# toplam borç", "total borç", "güncel toplam" -> bunlar bir KİŞİ sorgusu
# değil, defterin TAMAMININ özetidir. Eskiden "tüm"/"total" bir kişi adı
# sanılıp "defterde yok" deniyordu.
TOTAL_BALANCE_QUALIFIERS = {
    "tüm", "tum", "bütün", "butun", "toplam", "toplamda", "total",
    "genel", "sistemdeki", "sistemin", "güncel", "guncel",
    "herkesin", "hepsinin",
}
# "durum"/"durumu" BİLEREK burada YOK: "genel durum"/"herkesin durumu" zaten
# report_general'dır (genel durum raporu PDF'i) ve o davranış korunur.
TOTAL_BALANCE_NOUNS = {
    "bakiye", "bakiyesi", "bakiyeler", "bakiyeleri",
    "borç", "borc", "borcu", "borçlar", "borclar", "borçları", "borclari",
    "alacak", "alacağı", "alacagi", "alacaklar", "alacakları", "alacaklari",
}
# İsim (noun) hiç geçmese de tek başına anlamlı olan nitelikler: "toplam",
# "güncel toplam" gibi. "tüm" ya da "genel" tek başına bir şey ifade etmez.
TOTAL_BALANCE_STANDALONE = {"toplam", "toplamda", "total"}
TOTAL_BALANCE_FILLERS = BALANCE_FILLERS | {"bana", "lütfen", "lutfen", "ver", "nedir"}


def _try_total_balance_query(tokens: list[str]) -> ParsedIntent | None:
    """"tüm bakiye" / "toplam borç" / "genel bakiye" / "güncel toplam" ->
    total_balance (defterin tamamının özeti).

    Kasten DAR: cümlenin TAMAMI nitelik/isim/dolgu kelimelerinden oluşmalı.
    Araya bir kişi adı karışıyorsa ("furkan toplam borç") bu bir kişi
    sorgusudur, buraya düşmez — mevcut bare bakiye akışına bırakılır."""
    token_set = set(tokens)
    if not token_set <= (TOTAL_BALANCE_QUALIFIERS | TOTAL_BALANCE_NOUNS | TOTAL_BALANCE_FILLERS):
        return None
    if not token_set & TOTAL_BALANCE_QUALIFIERS:
        return None
    if not (token_set & TOTAL_BALANCE_NOUNS or token_set & TOTAL_BALANCE_STANDALONE):
        return None
    return ParsedIntent(kind="total_balance")


LIST_VERBS = {
    "listele", "sırala", "listeler", "sıralar", "listelesene", "sıralasana",
    "göster", "getir",
}
LIST_FILLERS = {"tüm", "tum", "bütün", "butun", "lütfen", "lutfen", "bana"}
# "hepsi"/"hepsini" BİLEREK dolgu değil (LIST_ALL_WORDS'te): "hepsini listele"
# gibi bir cümlede tek başına "hepsini" kalınca (diğer dolgular çıkınca) bu
# ismin KENDİSİ liste isteğinin öznesidir, dolgu değil (bkz. _try_list_query
# head kontrolü — dolgu olsaydı head boş kalır, eşleşme kaçardı).
LIST_ALL_WORDS = {
    "kişileri", "kisileri", "kişiler", "kisiler", "herkesi", "herkes",
    "hepsini", "hepsi",
    "insanları", "insanlari", "insanlar",
    "müşterileri", "musterileri", "müşteriler", "musteriler",
}
LIST_DEBTORS_WORDS = {"borçluları", "borclulari", "borçlular", "borclular"}
LIST_CREDITORS_WORDS = {"alacaklıları", "alacaklilari", "alacaklılar", "alacaklilar"}

# Genel "rapor ver" / "rapor" isteği: hangi rapor istendiği belirsiz, bot
# günlük/genel seçimini buton ile sorar. Fiil ve dolgu kelimeleri hariç
# tutulduğunda tek kelime "rapor" kalmalı ("günlük rapor" gibi belirli bir
# tür istenmişse buraya düşmesin diye kasıtlı dar tutuldu).
REPORT_MENU_FILLERS = LIST_FILLERS | {"ver", "istiyorum", "çıkar"}

# Genel durum raporu: bir "nitelik" (genel/tüm/bütün/herkes...) VE bir
# "isim" (rapor/durum/müşteriler...) birlikte geçmeli — yalnız biri anlamsız
# ya da başka bir niyetle karışabilir (ör. "tüm kişileri listele" zaten
# _try_list_query'de yakalanır, buraya hiç düşmez).
REPORT_GENERAL_QUALIFIERS = {"genel", "tüm", "tum", "bütün", "butun", "herkesin", "herkes"}
REPORT_GENERAL_NOUNS = {
    "rapor", "raporu", "raporunu",
    "durum", "durumu", "durumunu",
    "müşteriler", "musteriler", "müşterileri", "musterileri",
    "zamanların", "zamanlarin",
}

# Günlük rapor: aynı nitelik+isim ikilisi mantığı, gün/bugün eksenli.
REPORT_DAILY_QUALIFIERS = {"gün", "günün", "bugün", "bugünün", "günlük", "bugünkü"}
REPORT_DAILY_NOUNS = {
    "rapor", "raporu", "raporunu",
    "hareketler", "hareketleri",
    "yaptık", "yaptik", "oldu",
}

# Kişiye özel ekstre/rapor/döküm isteği. "hesap dökümü" gibi iki kelimeli
# kalıplarda "hesap" isme dahil edilmemesi gereken bir dolgu kelimesidir.
REPORT_PERSON_KEYWORDS = {
    "ekstresi", "ekstresini", "ekstre",
    "raporu", "raporunu",
    "dökümü", "dökümünü", "döküm",
    "dokumu", "dokumunu", "dokum",
}
REPORT_PERSON_PRE_FILLERS = {"hesap"}
# Anahtar kelimeden önce bu kelimelerden biri geçiyorsa muhtemelen bir kişi
# adı değil, genel bir ifadedir ("bir durum raporu hazırla" gibi) — bu
# durumda uydurmadan pes edilir (None), böylece LLM'e devredilir.
REPORT_PERSON_EXCLUDED_PRECEDING = (
    REPORT_GENERAL_QUALIFIERS | REPORT_GENERAL_NOUNS | REPORT_DAILY_QUALIFIERS | REPORT_DAILY_NOUNS
)

# İlçe eki çözümü: sondan önce çoğul/belirtme (-leri/-ları/-ler/-lar/-i/-ı),
# sonra "ile ilgili" eki (-li/-lı/-lu/-lü) soyulur. Uzun ekler önce denenir
# ki "ahmetbeylerlileri" -> "leri" (değil "i") soyulsun. "-ler"/"-lar" (fiilsiz
# çoğul, "bergamalılar" gibi — CLAUDE.md > "Bot sorgu anlama" Grup 1, madde 4)
# "-leri"/"-ları"dan (belirtme hâli) SONRA denenir ki bunlar öncelik kazansın.
_DISTRICT_SUFFIX_OUTER = ("leri", "ları", "ler", "lar", "i", "ı")
_DISTRICT_SUFFIX_INNER = ("li", "lı", "lu", "lü")


def _strip_district_suffix(word: str) -> str | None:
    """"bergamalıları" -> "bergama", "ahmetbeylerlileri" -> "ahmetbeyler".
    Ek bulunamazsa None (bu kelime bir ilçe adı çekimi değil demektir)."""
    stripped = word
    for suf in _DISTRICT_SUFFIX_OUTER:
        if stripped.endswith(suf) and len(stripped) > len(suf) + 1:
            stripped = stripped[: -len(suf)]
            break
    else:
        return None

    for suf in _DISTRICT_SUFFIX_INNER:
        if stripped.endswith(suf) and len(stripped) > len(suf) + 1:
            stripped = stripped[: -len(suf)]
            return stripped
    return None


# Bulunma hâli + çoğul-aitlik eki ("-daki/-deki/-taki/-teki", opsiyonel
# "-ler/-lar" ile): "bergamadakiler" -> "bergama" (Bergama'da olanlar),
# "ankaradaki" -> "ankara". _strip_district_suffix'ten AYRI bir örüntü —
# o yalnızca çoğul/belirtme+"-li/-lı" (bergamalıları) ekini çözer, bu ise
# "X'te olan(lar)" örüntüsünü. Uzun biçim (çoğullu) önce denenir ki
# "bergamadakiler" "bergamadaki" + artık kelimeye bölünmesin.
_DISTRICT_LOCATIVE_SUFFIXES = ("dakiler", "takiler", "deki", "teki", "daki", "taki")


def _strip_district_locative_suffix(word: str) -> str | None:
    """"bergamadakiler"/"bergamadaki" -> "bergama". Ek yoksa None."""
    for suf in _DISTRICT_LOCATIVE_SUFFIXES:
        if word.endswith(suf) and len(word) > len(suf) + 1:
            return word[: -len(suf)]
    return None


def _district_from_word(word: str) -> str | None:
    """Bir kelimeyi ilçe adına çözer: önce çekim eki (_strip_district_suffix,
    "bergamalıları" gibi), olmazsa bulunma hâli eki (_strip_district_locative_
    suffix, "bergamadakiler" gibi). İkisi de yoksa None."""
    return _strip_district_suffix(word) or _strip_district_locative_suffix(word)


# Niyet belirlendikten sonra kişi/ürün metninden temizlenen kelimeler.
STOPWORDS = DEBT_WORDS | PAYMENT_WORDS | {
    "borç", "borc", "yaz", "yazdı", "yazdım", "parası", "parasını",
    "için", "icin", "ettim",
}

# Yeni kişi OLUŞTURMA türevleri (CLAUDE.md > "Bot kayıt akışı — Grup 2"):
# "ahmet adında yeni kişi oluştur", "ahmet duman kayıt et", "furkan duman
# adlı kişiyi sisteme kayıt et", "faruk caner sisteme ekle", "ercüment
# çözer kişisini ekle" — borç YOK, sadece kişi eklensin isteniyor.
# Tetikleyici üç türlü olabilir:
#   1. Açık bir eylem kelimesi (oluştur/kayıt/kaydet/ekle/aç/gir).
#   2. Bir isimlendirme kelimesi ("adlı"/"adında"/"isimli"/"isminde").
#   3. "yeni" + ("kişi"/"isim") ikilisi birlikte ("yeni kişi"/"yeni isim").
# Tek başına "yeni" ya da "kişi" (madde 3'ün yarısı) tetiklemez — aksi halde
# alakasız cümlelerde de yanlışlıkla eşleşirdi.
CREATE_PERSON_ACTIONS = {
    "oluştur", "olustur", "oluşturun", "olusturun",
    "kayıt", "kayit", "kaydet", "kaydedin",
    "ekle", "ekleyin", "aç", "ac", "gir",
}
CREATE_PERSON_NAMING_WORDS = {"adlı", "adli", "adında", "adinda", "isimli", "isminde"}
# "{isim} kişiyi/kişisini ..." — isimden sonra gelen "kişi/isim" türevleri.
CREATE_PERSON_PERSON_WORDS = {
    "kişi", "kisi", "kişiyi", "kisiyi", "kişisini", "kisisini",
    "kişiyi", "isim", "ismi", "isimle",
}
# Hedef ("nereye eklensin") kelimeleri — isme dahil değil.
CREATE_PERSON_TARGET_WORDS = {
    "sisteme", "sistemine", "sistem", "deftere", "defterime", "defterine",
    "defter", "listeye", "listeme", "listesine", "liste",
    "kayıtlara", "kayitlara", "kayıtlarıma", "kayitlarima",
    "kayıtlarına", "kayitlarina",
}
# İsim öbeğinden ayıklanan dolgu/komut kelimeleri (isme KARIŞMAMALI). Bunlar
# yalnızca isim öbeğinin BAŞINDAKİ ve SONUNDAKİ dizilerden ayıklanır (bkz.
# _create_person_name) — ortadaki gerçek ad-soyad korunur, yani soyadı
# "Kişi"/"Ekle" gibi bir komut kelimesine benzeyen biri silinmez.
CREATE_PERSON_FILLERS = (
    CREATE_PERSON_ACTIONS | CREATE_PERSON_NAMING_WORDS
    | CREATE_PERSON_PERSON_WORDS | CREATE_PERSON_TARGET_WORDS
    | {"yeni", "et", "edin", "bir", "adına", "adina"}
)
# "yeni" ile birlikte tetikleyici sayılan isim/kişi kelimeleri (madde 3).
CREATE_PERSON_NOUN_WORDS = {"kişi", "kisi", "isim"}

# Kişi SİLME/ARŞİVLEME türevleri (CLAUDE.md > "Bot kişi silme = arşivleme —
# Grup 3"): "furkanı sil", "furkan sil", "furkanı kaldır", "furkanı
# arşivle", "furkanı sıfırla". HİÇBİR ŞEY gerçekten silinmez — bu niyet
# arşivle+pasifleştir akışını tetikler (bkz. app/services/person_archive.py).
# "yeniden oluştur/aç" eklenmişse (ör. "furkanı sil yeniden oluştur")
# archive_and_recreate: arşivle + aynı isimle temiz yeni kişi. Bu kontrol
# create_person'dan (aşağıda, parse() içinde) ÖNCE denenir çünkü ikisi de
# "oluştur" kelimesini tetikleyici sayabilir — bir ARCHIVE_ACTION_WORDS
# üyesi (sil/kaldır/arşivle/sıfırla) varsa bu her zaman bir silme komutudur,
# create_person'a asla düşmemeli.
ARCHIVE_ACTION_WORDS = {"sil", "kaldır", "kaldir", "arşivle", "arsivle", "sıfırla", "sifirla"}
ARCHIVE_RECREATE_MARKERS = {"oluştur", "olustur", "aç", "ac"}
ARCHIVE_FILLERS = ARCHIVE_ACTION_WORDS | ARCHIVE_RECREATE_MARKERS | {"yeniden"}


# "sil" HER ZAMAN "kişiyi sil" DEMEK DEĞİLDİR (2026-08-31 düzeltmesi):
# "furkan duman 20 saman borcunu ödedi sil" cümlesinde asıl niyet
# TAHSİLAT'tır — kullanıcı kapanan BORCU silmek/kapatmak ister, KİŞİYİ
# değil. Eskiden bu cümle archive_person'a düşüyor ve üstelik cümlenin
# tamamı ("furkan duman 20 saman borcunu ödedi") kişi adı sanılıyordu.
# Artık: bir silme fiiliyle birlikte para/mal bağlamı da varsa niyet
# BELİRSİZ sayılır (kind="delete_ambiguous") ve bot/web üç butonla sorar
# ("Tahsilat gir" / "Kişiyi sil" / "İptal"). Yanlış kişi silmek, bir soru
# sormaktan çok daha pahalıdır. Bağlam yoksa ("furkanı sil") davranış
# aynen eskisi gibi: doğrudan archive_person.
_DELETE_MONEY_MARKERS = CURRENCY_UNITS | UNITS | {
    "borç", "borc", "tahsilat", "parası", "parasını", "ödeme", "odeme",
}
# İsim öbeğini bitiren kelimeler: bunlardan biri (ya da bir sayı) görüldüğü
# anda ad-soyad bitmiştir (bkz. _leading_name). BALANCE_KEYWORDS de dahil —
# "furkan duman 20 saman borcunu ödedi" içindeki isim "furkan duman"dır.
_NAME_STOP_WORDS = (
    DEBT_WORDS | PAYMENT_WORDS | CURRENCY_UNITS | UNITS
    | BALANCE_KEYWORDS | BALANCE_KEYWORDS_BARE | BALANCE_FILLERS
    | {"borç", "borc", "tahsilat", "parası", "parasını", "ödeme", "odeme"}
)


def _leading_name(tokens: list[str]) -> str:
    """Cümlenin BAŞINDAKİ ad-soyad öbeği: ilk sayıya ya da bilinen bir
    fiil/birim/anahtar kelimeye kadar olan kısım. "furkan duman 20 saman
    borcunu ödedi" -> "furkan duman". Baştan hiçbir şey toplanamazsa boş
    string döner (uydurulmaz, çağıran taraf pes eder)."""
    name: list[str] = []
    for i, tok in enumerate(tokens):
        if tok in _NAME_STOP_WORDS or _consume_number(tokens, i) is not None:
            break
        name.append(tok)
    return " ".join(name).strip()


def _has_money_context(tokens: list[str]) -> bool:
    """Cümlede bir borç/tahsilat fiili, para birimi, ölçü birimi, "borç/
    tahsilat" kelimesi ya da herhangi bir sayı var mı?"""
    if set(tokens) & (DEBT_WORDS | PAYMENT_WORDS | _DELETE_MONEY_MARKERS):
        return True
    return any(_consume_number(tokens, i) is not None for i in range(len(tokens)))


def strip_delete_words(raw_text: str) -> str:
    """Silme fiilini ("sil/kaldır/arşivle/sıfırla" ve "yeniden oluştur")
    cümleden çıkarır: "furkan duman 20 saman borcunu ödedi sil" ->
    "furkan duman 20 saman borcunu ödedi". delete_ambiguous sorusunda
    kullanıcı "Tahsilat gir" derse kalan metin normal akıştan yeniden
    geçirilir (bkz. app/bot/main.py, app/services/web_chat.py)."""
    tokens = _split_tokens(normalize(" ".join((raw_text or "").split())))
    return " ".join(t for t in tokens if t not in ARCHIVE_FILLERS)


def _try_archive_person_query(tokens: list[str]) -> ParsedIntent | None:
    """"{isim} sil/kaldır/arşivle/sıfırla" -> archive_person. Aynı cümlede
    "yeniden" + "oluştur"/"aç" de varsa -> archive_and_recreate (arşivle +
    aynı isimle temiz yeni kişi). Silme fiili yoksa hiç tetiklenmez.

    Cümlede para/mal bağlamı da varsa (bkz. _DELETE_MONEY_MARKERS) niyet
    belirsizdir — archive'a ATLANMAZ, delete_ambiguous dönülür ve kullanıcıya
    sorulur. İsim bile güvenle çıkarılamıyorsa (baştan bir ad-soyad öbeği
    yoksa) uydurulmaz: None dönülür ve cümle LLM'e devredilir."""
    token_set = set(tokens)
    if not (token_set & ARCHIVE_ACTION_WORDS):
        return None

    person_tokens = [t for t in tokens if t not in ARCHIVE_FILLERS]
    if not person_tokens:
        return None

    if _has_money_context(person_tokens):
        person = _leading_name(person_tokens)
        if not person:
            return None
        return ParsedIntent(kind="delete_ambiguous", person_name=person)

    recreate = "yeniden" in token_set and bool(token_set & ARCHIVE_RECREATE_MARKERS)
    person = " ".join(person_tokens).strip()
    if not person:
        return None
    kind = "archive_and_recreate" if recreate else "archive_person"
    return ParsedIntent(kind=kind, person_name=person)


# Kişi DÜZENLEME türevleri (CLAUDE.md > "Silme mesajı + kişi düzenleme —
# Grup 4"): iki bağımsız kalıp.
#   1. NET: "{kişi} {alan} {değer} yap" — alan VE değer belli, doğrudan
#      güncellenecek (bkz. _try_edit_person_net). "mehmetin ismi akif yap"
#      gibi iyelik ekli kişi adı ("mehmetin") KASTEN soyulmaz — diğer tüm
#      niyetlerde olduğu gibi ham bırakılır, ek temizleme merkezi olarak
#      intent_resolver.find_person_match içinde (strip_turkish_suffix) yapılır.
#   2. BELİRSİZ: "{kişi} düzenle" / "{kişi} isim değiştir" vb. — alan/değer
#      YOK, bot [Ad soyad][Telefon][İl][İlçe][Adres] butonlarıyla sorar
#      (bkz. _try_edit_person_menu). Bir alan kelimesi geçse bile ("telefon
#      düzenle") değer verilmediği sürece yine TAM menü sorulur — CLAUDE.md
#      bunu kasıtlı basit tutuyor, "hangi alan" ile "yeni değer ne" ayrı
#      sorulmaz, hep aynı buton akışından geçilir.
FIELD_WORDS = {
    "isim": "full_name", "ismi": "full_name", "ad": "full_name",
    "adı": "full_name", "adi": "full_name",
    "telefon": "phone", "telefonu": "phone",
    "numara": "phone", "numarası": "phone", "numarasi": "phone",
    "il": "city", "ili": "city", "şehir": "city", "sehir": "city",
    "şehri": "city", "sehri": "city",
    "ilçe": "district", "ilce": "district", "ilçesi": "district", "ilcesi": "district",
    "adres": "address", "adresi": "address",
    "not": "note", "notu": "note",
}
# "ad soyad" iki kelimelik bir alan adı — tek kelimelik FIELD_WORDS'ten AYRI
# ele alınır çünkü _try_edit_person_net tek tek token tarar.
FIELD_PHRASES = {
    ("ad", "soyad"): "full_name", ("ad", "soyadı"): "full_name", ("ad", "soyadi"): "full_name",
}
# Değer atama fiili: yalnızca bu kelimelerden BİRİYLE biten cümle NET kabul
# edilir ("mehmet ilçe ahmetbeyler yap") — aksi halde uydurmadan pes edilir.
EDIT_ASSIGN_VERBS = {"yap", "yapsın", "yapsin"}


def _try_edit_person_net(tokens: list[str]) -> ParsedIntent | None:
    """"{kişi} {alan} {değer} yap" -> alan+değer net, doğrudan güncelleme
    niyeti. Cümle bir EDIT_ASSIGN_VERBS üyesiyle bitmiyorsa hiç denenmez."""
    if not tokens or tokens[-1] not in EDIT_ASSIGN_VERBS:
        return None
    body = tokens[:-1]

    field = None
    field_end = None
    person_tokens: list[str] = []
    for i in range(len(body) - 1):
        phrase = (body[i], body[i + 1])
        if phrase in FIELD_PHRASES:
            field = FIELD_PHRASES[phrase]
            field_end = i + 2
            person_tokens = body[:i]
            break
    if field is None:
        idx = next((i for i, t in enumerate(body) if t in FIELD_WORDS), None)
        if idx is None:
            return None
        field = FIELD_WORDS[body[idx]]
        field_end = idx + 1
        person_tokens = body[:idx]

    value_tokens = body[field_end:]
    person = " ".join(person_tokens).strip()
    value = " ".join(value_tokens).strip()
    if not person or not value:
        return None
    return ParsedIntent(kind="edit_person", person_name=person, field=field, new_value=value)


# Yazım hatası toleransı (CLAUDE.md): "düzenlee", "dzenle", "dğeiştir",
# "dğeişiklik" gibi varyasyonlar da yakalanmalı. Tam bir sözlük yerine
# Damerau-Levenshtein mesafesiyle küçük bir kanonik listeye karşı fuzzy
# karşılaştırma yapılır (bkz. _is_edit_trigger_word) — bitişik iki harfin
# yer değiştirmesi (transposition) TEK bir düzenleme sayılır, düz
# Levenshtein'de bu 2 sayılıp bazı gerçek yazım hatalarını (dğeiştir gibi)
# eşik dışında bırakırdı.
EDIT_TRIGGER_CANONICALS = ("düzenle", "değiştir", "değişiklik")
_EDIT_TRIGGER_MAX_DISTANCE = 2
# Uzunluk farkı bu değerden büyükse mesafe hesabına hiç girilmez (hem hız
# hem de alakasız kısa/uzun kelimelerin yanlışlıkla eşleşmesini önlemek için).
_EDIT_TRIGGER_LEN_GUARD = 2

EDIT_MENU_FILLERS = {
    "adlı", "adli", "kişiyi", "kisiyi", "kişi", "kisi", "soyad", "soyadı", "soyadi",
}


def _damerau_levenshtein(a: str, b: str) -> int:
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + cost)
    return d[la][lb]


def _is_edit_trigger_word(word: str) -> bool:
    for canon in EDIT_TRIGGER_CANONICALS:
        if abs(len(word) - len(canon)) > _EDIT_TRIGGER_LEN_GUARD:
            continue
        if _damerau_levenshtein(word, canon) <= _EDIT_TRIGGER_MAX_DISTANCE:
            return True
    return False


def _try_edit_person_menu(tokens: list[str]) -> ParsedIntent | None:
    """"{kişi} düzenle", "{kişi} adlı kişiyi düzenle", "{kişi} isim
    değiştir/düzenle/değişiklik", "{kişi} telefon düzenle" -> alan/değer
    belirtilmemiş, bot tam menü sorar (field/new_value boş). Yalnızca cümle
    TAM OLARAK bir tetikleyici kelimeyle BİTİYORSA denenir — aksi halde
    "değiştirdi" gibi alakasız fiil çekimleri de yanlışlıkla tetiklenebilirdi."""
    if not tokens or not _is_edit_trigger_word(tokens[-1]):
        return None
    person_tokens = [t for t in tokens[:-1] if t not in EDIT_MENU_FILLERS and t not in FIELD_WORDS]
    person = " ".join(person_tokens).strip()
    if not person:
        return None
    return ParsedIntent(kind="edit_person", person_name=person, field=None, new_value=None)


_TR_ONES = {
    "bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5,
    "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
}
_TR_TENS = {
    "on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "elli": 50,
    "altmış": 60, "yetmiş": 70, "seksen": 80, "doksan": 90,
}
_TR_SCALES = {"yüz": 100, "bin": 1000, "milyon": 1_000_000}

# "buçuk" (yarım fazlası) bir sayı kelimesi değil ama zincirin son halkası
# olabilir ("üç buçuk" -> 3.5) — sözlük dışı ayrı işlenir (bkz. _consume_number).
_NUMBER_WORDS = set(_TR_ONES) | set(_TR_TENS) | set(_TR_SCALES) | {"buçuk"}

_NUMBER_TOKEN = re.compile(r"\d[\d.,]*")


def _segment_number_word(word: str, _cache: dict[str, list[str] | None] = {}) -> list[str] | None:
    """Bitişik yazılmış Türkçe sayı kelimelerini ayırır: "ikiyüz" ->
    ["iki", "yüz"], "binbeşyüz" -> ["bin", "beş", "yüz"]. Kelime TAMAMEN
    sayı kelimelerinden oluşmuyorsa None (rastgele bir isim/ürün adının
    yanlışlıkla bölünmemesi için tam kapsama şartı aranır)."""
    if word in _cache:
        return _cache[word]
    if word in _NUMBER_WORDS:
        _cache[word] = [word]
        return [word]
    result: list[str] | None = None
    for split in range(len(word) - 1, 0, -1):
        prefix, suffix = word[:split], word[split:]
        if prefix in _NUMBER_WORDS:
            rest = _segment_number_word(suffix)
            if rest is not None:
                result = [prefix] + rest
                break
    _cache[word] = result
    return result


@dataclass(slots=True)
class ParsedIntent:
    kind: str  # "debt" | "payment" | "balance_query" | "list_all" | "list_debtors" |
               # "list_creditors" | "list_district" | "report_menu" | "report_person" |
               # "report_general" | "report_daily" | "person_contact" | "info_menu" |
               # "search" (tek kelime, Telegram arama gibi — bkz. _try_single_word_search) |
               # "total_balance" (defterin tamamının özeti — bkz. _try_total_balance_query) |
               # "archive_person" | "archive_and_recreate" |
               # "delete_ambiguous" (silme mi tahsilat mı belirsiz, sorulur) |
               # "edit_person" |
               # "product_query" (ürün/stok/fiyat sorgusu — HENÜZ DESTEKLENMİYOR,
               #   tanınır ki bot sessizce yanlış bir şey yapmasın)
    person_name: str | None = None
    qty: Decimal | None = None
    unit: str | None = None
    product: str | None = None
    amount: Decimal | None = None
    district: str | None = None
    query: str | None = None  # yalnızca kind == "search" için: aranan tek kelime
    field: str | None = None  # yalnızca kind == "edit_person": full_name/phone/city/district/address/note
    new_value: str | None = None  # yalnızca kind == "edit_person", NET komutta dolu (bkz. _try_edit_person_net)
    # Borç kapanışı: "ali borcunu ödedi" — tahsilat niyeti NET ama tutar
    # SÖYLENMEMİŞ. Tutar uydurulmaz; kişi çözülünce güncel bakiye teklif
    # edilir ve kullanıcıya onaylatılır (bkz. intent_resolver + message_processor).
    close_debt: bool = False


def _parse_amount(raw: str) -> Decimal | None:
    """"15000" -> 15000; "15.000" -> 15000 (binlik ayraç);
    "1.500,50" / "1500,50" -> 1500.50 (TR ondalık virgülü)."""
    raw = raw.strip()
    if not raw:
        return None
    has_dot = "." in raw
    has_comma = "," in raw
    try:
        if has_dot and has_comma:
            raw = raw.replace(".", "").replace(",", ".")
        elif has_comma:
            raw = raw.replace(",", ".")
        elif has_dot and re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
            raw = raw.replace(".", "")
        return Decimal(raw)
    except InvalidOperation:
        return None


def _consume_number(tokens: list[str], i: int) -> tuple[Decimal, int] | None:
    """tokens[i]'den başlayan bir sayıyı tüketir: düz rakam ("15000"),
    Türkçe sayı kelimesi dizisi ("on beş bin" -> 15000) ya da glue-split
    sonrası karışık biçim ("3"+"bin" -> 3000, "3"+"buçuk" -> 3.5). İlk
    token rakamsa (yalnızca i'de) taban değer olarak alınır, ardından gelen
    "yüz"/"bin"/"milyon"/"buçuk" ile zincirlenebilir — bu sayede "3bin" ASLA
    3 milyon değil 3000 olur (çarpan mantığı: bin=×1000, milyon=×1000000,
    yüz=×100, kökle çarpılır/toplanır, rakamla karıştırılmaz).
    Bulamazsa None döner."""
    total = Decimal(0)
    segment = Decimal(0)
    matched = False
    j = i
    while j < len(tokens):
        tok = tokens[j]
        if j == i and _NUMBER_TOKEN.fullmatch(tok):
            value = _parse_amount(tok)
            if value is None:
                break
            segment += value
            matched = True
            j += 1
            continue
        if tok in _TR_ONES:
            segment += _TR_ONES[tok]
            matched = True
            j += 1
            continue
        if tok in _TR_TENS:
            segment += _TR_TENS[tok]
            matched = True
            j += 1
            continue
        if tok == "buçuk":
            segment += Decimal("0.5")
            matched = True
            j += 1
            continue
        if tok in _TR_SCALES:
            scale = _TR_SCALES[tok]
            base = segment if segment else Decimal(1)
            if scale == 100:
                segment = base * scale
            else:  # bin, milyon: taban katmanı kapanır, toplama eklenir
                total += base * scale
                segment = Decimal(0)
            matched = True
            j += 1
            continue
        break
    if not matched:
        return None
    return total + segment, j - i


def parse_turkish_number(text: str) -> Decimal | None:
    """Tek bir sayı ifadesini uçtan uca çözer: "3bin" -> 3000, "3 bin" ->
    3000, "on beş bin" -> 15000, "ikiyüz" -> 200, "3buçuk" -> 3.5,
    "birmilyon" -> 1000000, "15000" -> 15000. Metnin TAMAMI tek bir sayıya
    karşılık gelmelidir (fazladan kelime kalırsa None) — cümle içinde geçen
    bir sayıyı bulmak için değil, sayı ifadesinin kendisini test etmek/
    çözmek için kullanılır (bkz. tests/test_sayi.py)."""
    tokens = _split_tokens(normalize(text or ""))
    if not tokens:
        return None
    consumed = _consume_number(tokens, 0)
    if consumed is None:
        return None
    value, length = consumed
    if length != len(tokens):
        return None
    return value


def _split_tokens(norm: str) -> list[str]:
    norm = re.sub(r"[.,!?;:]+(?=\s|$)", "", norm)          # cümle sonu noktalama
    # "15000tl" -> "15000 tl", "3bin" -> "3 bin", "3buçuk" -> "3 buçuk": rakama
    # bitişik yazılmış her türlü harf grubu (para birimi ya da sayı çarpanı) ayrılır.
    norm = re.sub(r"(\d)([a-zçğıöşü]+)", r"\1 \2", norm)
    norm = re.sub(r"(₺)(\d)", r"\1 \2", norm)
    tokens = norm.split()

    # Bitişik yazılmış Türkçe sayı kelimelerini ayır: "ikiyüz" -> "iki yüz",
    # "birmilyon" -> "bir milyon" (bkz. _segment_number_word — tam kapsama
    # şartı sayesinde rastgele bir isim/ürün adı yanlışlıkla bölünmez).
    expanded: list[str] = []
    for tok in tokens:
        if tok.isdigit() or len(tok) < 3:
            expanded.append(tok)
            continue
        segments = _segment_number_word(tok)
        expanded.extend(segments if segments else [tok])
    return expanded


# Para birimi ("tl"/"lira"/"₺") olmasa da hemen ardından "borç" gelen bir
# sayı da tutar sayılır ("15bin borç" -> 15000): "borç" burada para birimi
# yerine geçen bir tutar işaretçisi. Kind tespiti bu tokenlar tüketilmeden
# ÖNCE (parse() içinde) yapıldığı için "borç"un kind sinyali olarak
# kaybolması sorun olmaz (bkz. parse()).
_AMOUNT_MARKERS = CURRENCY_UNITS | {"borç", "borc"}


def _extract_amount(tokens: list[str]) -> tuple[Decimal | None, list[str]]:
    """Sayı + (tl|lira|₺|borç) ikilisini bulur, kalan tokenlardan çıkarır."""
    for i in range(len(tokens)):
        consumed = _consume_number(tokens, i)
        if consumed is None:
            continue
        value, length = consumed
        end = i + length
        if end < len(tokens) and tokens[end] in _AMOUNT_MARKERS:
            return value, tokens[:i] + tokens[end + 1:]
    return None, tokens


def _extract_qty_unit(
    tokens: list[str],
) -> tuple[Decimal | None, str | None, list[str], list[str]]:
    """İlk sayıyı adet olarak alır. Hemen ardından bilinen bir birim varsa
    onu kullanır, yoksa birim boş bırakılır (ürün serbest metin)."""
    for i in range(len(tokens)):
        consumed = _consume_number(tokens, i)
        if consumed is None:
            continue
        value, length = consumed
        end = i + length
        person_tokens = tokens[:i]
        rest = tokens[end:]
        if rest and rest[0] in UNITS:
            return value, rest[0], rest[1:], person_tokens
        return value, None, rest, person_tokens
    return None, None, [], tokens


def _detect_kind(tokens: list[str]) -> str | None:
    token_set = set(tokens)
    if token_set & DEBT_WORDS:
        return "debt"
    if token_set & PAYMENT_WORDS:
        return "payment"
    if "borç" in token_set or "borc" in token_set:
        return "debt"
    return None


def _try_short_record(tokens: list[str]) -> ParsedIntent | None:
    """Kısa kayıt biçimi (CLAUDE.md > "Bot kayıt akışı — Grup 2"): fiil
    YOKSA ("aldı"/"borç"/"verdim" gibi hiçbir yön sinyali) ama cümlenin
    yapısı net bir şekilde {isim} {adet} {ürün} {tutar} ise ("ahmet 30 saman
    5000tl"), borç kaydı varsayılır — mal/para birine gitmiş, fiil
    söylenmemiş olsa da bu yapı yalnızca "borç yazma"nın kısaltmasıdır.

    Yalnızca _detect_kind hiçbir yön bulamadığında (kind=None) çağrılır,
    bu yüzden burada AYRICA bir fiil/bare-"borç" kontrolüne gerek yok — ama
    yine de savunma amaçlı tekrarlanır (bu fonksiyon başka bir bağlamdan da
    çağrılırsa yanlışlıkla bir borç/tahsilat cümlesini ele almasın diye).

    Adet, ürün ya da tutardan biri eksikse yapı net değildir — uydurmadan
    None dönülür (LLM'e bırakılır); yön belirsizliği YALNIZCA bu dörtlünün
    hepsi bir arada olduğunda güvenle "borç" sayılır."""
    token_set = set(tokens)
    if token_set & (DEBT_WORDS | PAYMENT_WORDS) or "borç" in token_set or "borc" in token_set:
        return None

    amount, remaining = _extract_amount(tokens)
    if amount is None:
        return None

    qty, unit, product_tokens, person_tokens = _extract_qty_unit(remaining)
    if qty is None or not product_tokens or not person_tokens:
        return None

    person = " ".join(person_tokens).strip()
    product = " ".join(product_tokens).strip()
    if not person or not product:
        return None

    return ParsedIntent(
        kind="debt", person_name=person, qty=qty, unit=unit, product=product, amount=amount
    )


def _try_balance_query(tokens: list[str]) -> ParsedIntent | None:
    """Anahtar kelime ("borcu"/"hesabı"/"bakiyesi"/"durumu" ve ekli halleri)
    tek başına yeterlidir — bir soru/emir kelimesi ("ne", "söyle"...) şart
    değil. Bu kelimeler isim öncesinde ("ne kadar borcu var") ya da
    sonrasında ("borcu ne kadar", "borcunu söyle") gelebilir; ikisinde de
    isme dahil edilmez."""
    idx = next((i for i, tok in enumerate(tokens) if tok in BALANCE_KEYWORDS), None)
    if idx is None:
        return None
    person_tokens = [t for t in tokens[:idx] if t not in BALANCE_FILLERS]
    person = " ".join(person_tokens).strip()
    if not person:
        return None
    return ParsedIntent(kind="balance_query", person_name=person)


def _try_bare_balance_query(tokens: list[str]) -> ParsedIntent | None:
    """Bare (çekimsiz) bakiye anahtar kelimeleri (BALANCE_KEYWORDS_BARE):
    "furkan bakiye", "furkan durum", "furkan hesap", "furkan cari(ye)",
    "furkan alacak/alacağı", "furkan borç/borc", "furkan toplam borç" —
    kelime sırası esnek, anahtar isimden ÖNCE de gelebilir ("durum furkan",
    "bakiye furkan").

    İki güvenlik freni (bkz. modül üstü BALANCE_KEYWORDS_BARE yorumu):
      1. Cümlede bir borç/tahsilat fiili (aldı/verdi/ödedi/...) ya da
         herhangi bir sayı varsa hiç denenmez — bu, gerçek bir borç/tahsilat
         cümlesi demektir ("furkan 5000 borç"), bakiye sorgusu değil.
      2. Anahtar kelimenin YALNIZCA bir tarafında isim adayı olmalı, diğer
         tarafta (dolgu hariç) hiçbir şey kalmamalı. Yoksa "bana bir durum
         raporu hazırla" gibi alakasız uzun bir cümle ("durum" kelimesi
         geçtiği için) yanlışlıkla "bana bir" diye anlamsız bir isimle
         bakiye sorgusuna dönüşür — iki taraf da doluysa (ör. "durum"un
         önünde VE arkasında hala kelime varsa) bu net bir isim+anahtar
         kalıbı değildir, uydurmadan pes edilir (None, LLM'e bırakılır).
    """
    idx = next((i for i, tok in enumerate(tokens) if tok in BALANCE_KEYWORDS_BARE), None)
    if idx is None:
        return None

    token_set = set(tokens)
    if token_set & (DEBT_WORDS | PAYMENT_WORDS):
        return None
    if any(_consume_number(tokens, i) is not None for i in range(len(tokens))):
        return None

    before = [t for t in tokens[:idx] if t not in BALANCE_FILLERS]
    after = [t for t in tokens[idx + 1:] if t not in BALANCE_FILLERS]

    if before and not after:
        person_tokens = before
    elif after and not before:
        person_tokens = after
    else:
        return None

    person = " ".join(person_tokens).strip()
    if not person:
        return None
    return ParsedIntent(kind="balance_query", person_name=person)


# Anahtar kelimesiz bakiye soruları: cümlenin SONUNDA duran soru kalıpları.
# "{isim} ne kadar", "{isim} kaç para", "{isim} kaç lira" — hepsi aynı şeyi
# sorar (o kişinin bakiyesi). "kaç para" 2026-08-31 genişletmesiyle eklendi.
BARE_BALANCE_TAILS = (
    ("ne", "kadar"),
    ("kaç", "para"),
    ("kac", "para"),
    ("kaç", "lira"),
    ("kaç", "tl"),
)


def _try_bare_ne_kadar_query(tokens: list[str]) -> ParsedIntent | None:
    """"{isim} ne kadar" / "{isim} kaç para" (hiçbir bakiye anahtar kelimesi
    olmadan, yukarıdaki kalıplardan biriyle biten cümle) -> balance_query.
    Yalnızca _try_balance_query VE _try_bare_balance_query hiçbir anahtar
    kelime bulamadığında (ikisi de None döndüğünde) çağrılır — bu yüzden
    burada ayrıca bir anahtar kelime çelişkisi kontrolüne gerek yok, sadece
    borç/tahsilat fiili ve sayı güvenlik frenleri (bkz.
    _try_bare_balance_query ile aynı gerekçe) tekrarlanır."""
    tail = next((t for t in BARE_BALANCE_TAILS if tuple(tokens[-len(t):]) == t), None)
    if tail is None or len(tokens) <= len(tail):
        return None

    token_set = set(tokens)
    if token_set & (DEBT_WORDS | PAYMENT_WORDS):
        return None
    if any(_consume_number(tokens, i) is not None for i in range(len(tokens))):
        return None

    person = " ".join(tokens[: -len(tail)]).strip()
    if not person:
        return None
    return ParsedIntent(kind="balance_query", person_name=person)


def _try_person_contact_query(tokens: list[str]) -> ParsedIntent | None:
    """"{isim} telefonu/numarası/adresi" / "{isim} nerede oturuyor" -> net
    iletişim/konum niyeti, sormadan doğrudan kişi kartı gösterilir."""
    idx = next((i for i, tok in enumerate(tokens) if tok in PERSON_CONTACT_KEYWORDS), None)
    if idx is None:
        return None
    person = " ".join(tokens[:idx]).strip()
    if not person:
        return None
    return ParsedIntent(kind="person_contact", person_name=person)


def _try_info_menu_query(tokens: list[str]) -> ParsedIntent | None:
    """"{isim} bilgi ver/bilgi/bilgileri" -> hangi bilgi istendiği belirsiz,
    bot bakiye/kişi bilgileri/ekstre arasında buton ile sorar (CLAUDE.md >
    "DÜZELTME — 'bilgi ver' belirsiz, SOR"). Net iletişim niyeti
    (_try_person_contact_query) bundan önce denenir."""
    idx = next((i for i, tok in enumerate(tokens) if tok in INFO_MENU_KEYWORDS), None)
    if idx is None:
        return None
    person = " ".join(tokens[:idx]).strip()
    if not person:
        return None
    return ParsedIntent(kind="info_menu", person_name=person)


def _try_list_query(tokens: list[str]) -> ParsedIntent | None:
    verb_idx = next((i for i, tok in enumerate(tokens) if tok in LIST_VERBS), None)
    if verb_idx is None:
        return None

    head = [t for t in tokens[:verb_idx] if t not in LIST_FILLERS]
    if len(head) != 1:
        return None
    word = head[0]

    if _is_list_all_word(word):
        return ParsedIntent(kind="list_all")
    if word in LIST_DEBTORS_WORDS:
        return ParsedIntent(kind="list_debtors")
    if word in LIST_CREDITORS_WORDS:
        return ParsedIntent(kind="list_creditors")

    district = _district_from_word(word)
    if district:
        return ParsedIntent(kind="list_district", district=district)
    return None


# Grup 1, madde 4 (CLAUDE.md > "Bot sorgu anlama"): "kişiler" gibi bir liste
# isteği FİİLSİZ de gelebilir ("kişileri listele" değil sadece "kişiler").
# "sistemdeki" bu bağlamda ek bir dolgu kelimesi (LIST_FILLERS zaten
# tüm/tum/bütün/butun/lütfen/lutfen/bana içeriyor).
_BARE_LIST_QUALIFIERS = LIST_FILLERS | {"sistemdeki"}
# Fiil yerine geçen sabit kalıplar — filler çıkarma mantığına uymadıkları
# (ör. "kim", "var", "olanlar" gerçek kelimeler, dolgu değil) için ayrı
# kontrol edilir. Değer, döndürülecek ParsedIntent.kind'tir.
_BARE_LIST_FIXED_PHRASES: dict[tuple[str, ...], str] = {
    ("kişileri", "say"): "list_all",
    ("kisileri", "say"): "list_all",
    ("kimler", "var"): "list_all",
    ("listele",): "list_all",
    ("kim", "borçlu"): "list_debtors",
    ("kim", "borclu"): "list_debtors",
    ("kim", "borçlu", "var"): "list_debtors",
    ("kim", "borclu", "var"): "list_debtors",
    ("borçlu", "olanlar"): "list_debtors",
    ("borclu", "olanlar"): "list_debtors",
    ("kim", "alacaklı"): "list_creditors",
    ("kim", "alacakli"): "list_creditors",
    ("alacaklı", "olanlar"): "list_creditors",
    ("alacakli", "olanlar"): "list_creditors",
}


# Yazım toleransı (2026-08-31 anlama genişletmesi): "kişler", "ksiler",
# "kişileer", "kişilerr" gibi yaygın yanlış yazımlar da bir liste isteğidir —
# eskiden bunlar tek kelime "arama"ya (search) düşüp "eşleşen kişi yok"
# cevabı alıyordu. Tam bir yanlış-yazım sözlüğü tutmak yerine küçük bir
# kanonik listeye karşı Damerau-Levenshtein bakılır (düzenleme
# tetikleyicileriyle aynı yöntem, bkz. _is_edit_trigger_word).
#
# Kasten DAR tutuldu ki gerçek bir isim/ürün liste komutu sanılmasın:
#   - ilk harf aynı olmalı ("işler" -> "kişiler" eşleşmez),
#   - uzunluk farkı en fazla 2,
#   - 2 mesafe yalnızca en az 6 harfli kelimelerde kabul edilir ("kiler"
#     gibi kısa gerçek kelimeler dışarıda kalır).
LIST_ALL_CANONICALS = (
    "kişiler", "kişileri", "kisiler", "kisileri",
    "insanlar", "insanları", "müşteriler", "müşterileri",
)
_LIST_ALL_MIN_LEN = 5
_LIST_ALL_LEN_GUARD = 2
_LIST_ALL_FUZZY_MIN_LEN = 6  # mesafe 2 için gereken en kısa kelime

# "{X} listesi" kalıbının tekil biçimleri: "kişi listesi", "müşteri listesi".
LIST_ALL_SINGULAR_WORDS = {"kişi", "kisi", "müşteri", "musteri", "insan"}
LIST_SUFFIX_WORDS = {"listesi", "listesini", "listemi"}
LIST_DEBTORS_SINGULAR = {"borçlu", "borclu"}
LIST_CREDITORS_SINGULAR = {"alacaklı", "alacakli"}


def _is_list_all_word(word: str) -> bool:
    """"kişiler"/"insanlar"/"müşteriler" ve bunların yaygın yanlış
    yazımları ("kişler", "ksiler", "kişileer")."""
    if word in LIST_ALL_WORDS:
        return True
    if len(word) < _LIST_ALL_MIN_LEN:
        return False
    for canon in LIST_ALL_CANONICALS:
        if word[0] != canon[0] or abs(len(word) - len(canon)) > _LIST_ALL_LEN_GUARD:
            continue
        distance = _damerau_levenshtein(word, canon)
        if distance <= 1 or (distance == 2 and len(word) >= _LIST_ALL_FUZZY_MIN_LEN):
            return True
    return False


def _try_list_suffix_query(tokens: list[str]) -> ParsedIntent | None:
    """"kişi listesi", "kişiler listesi", "müşteri listesi", "borçlu
    listesi", "alacaklı listesi" -> ilgili liste. Cümle bir LIST_SUFFIX_WORDS
    üyesiyle BİTMİYORSA hiç denenmez."""
    if not tokens or tokens[-1] not in LIST_SUFFIX_WORDS:
        return None
    head = [t for t in tokens[:-1] if t not in _BARE_LIST_QUALIFIERS]
    if not head:
        return None
    if all(t in LIST_ALL_SINGULAR_WORDS or _is_list_all_word(t) for t in head):
        return ParsedIntent(kind="list_all")
    if all(t in LIST_DEBTORS_SINGULAR or t in LIST_DEBTORS_WORDS for t in head):
        return ParsedIntent(kind="list_debtors")
    if all(t in LIST_CREDITORS_SINGULAR or t in LIST_CREDITORS_WORDS for t in head):
        return ParsedIntent(kind="list_creditors")
    return None


def _try_bare_list_query(tokens: list[str]) -> ParsedIntent | None:
    """"kişiler", "tüm kişiler", "sistemdeki kişiler", "kişileri say",
    "kimler var", "listele" (tek), "kişi listesi", "borçlular", "kim
    borçlu", "borçlu olanlar" vb. -> list_all/list_debtors/list_creditors,
    hiçbir listele/sırala fiili olmadan. Yanlış yazılmış liste kelimeleri
    ("kişler", "ksiler") de buraya düşer (bkz. _is_list_all_word)."""
    fixed_kind = _BARE_LIST_FIXED_PHRASES.get(tuple(tokens))
    if fixed_kind is not None:
        return ParsedIntent(kind=fixed_kind)

    suffix_query = _try_list_suffix_query(tokens)
    if suffix_query is not None:
        return suffix_query

    remaining = [t for t in tokens if t not in _BARE_LIST_QUALIFIERS]
    if not remaining:
        return None
    if all(_is_list_all_word(t) for t in remaining):
        return ParsedIntent(kind="list_all")
    if all(t in LIST_DEBTORS_WORDS for t in remaining):
        return ParsedIntent(kind="list_debtors")
    if all(t in LIST_CREDITORS_WORDS for t in remaining):
        return ParsedIntent(kind="list_creditors")
    return None


# Bare ilçe sorgusu: "listele" fiili olmadan tek başına "bergamalılar" gibi
# bir kelime de bir ilçe listesi isteği sayılır (madde 4). Ama LIST_ALL/
# DEBTORS/CREDITORS kelimeleri de tesadüfen "-ler"/"-lar" ile bitebildiği
# için ("kişiler", "borçlular", "alacaklılar") bunlar KESİNLİKLE hariç
# tutulur — yoksa "borçlular" yanlışlıkla district="borç" sanılırdı. Bu
# hariç tutma artık pratikte hiç devreye girmiyor: _try_bare_list_query
# (yukarıda, parse() sırasında bundan ÖNCE denenir) "borçlular" gibi
# kelimeleri zaten list_debtors olarak yakalayıp döndüğü için buraya hiç
# ulaşmıyor — yine de ikinci bir güvenlik katmanı olarak korunur.
_DISTRICT_BARE_EXCLUDED = LIST_ALL_WORDS | LIST_DEBTORS_WORDS | LIST_CREDITORS_WORDS


def _try_bare_district_query(tokens: list[str]) -> ParsedIntent | None:
    """"bergamalılar"/"bergamadakiler" (tek kelime, fiilsiz) ->
    list_district. "bergamalıları listele" ile aynı anlam, yalnızca fiil
    eksik."""
    if len(tokens) != 1:
        return None
    word = tokens[0]
    if word in _DISTRICT_BARE_EXCLUDED:
        return None
    district = _district_from_word(word)
    if district:
        return ParsedIntent(kind="list_district", district=district)
    return None


# İlçeden kişi sorgusu (2026-08-31 anlama genişletmesi): "bergamadan kimler
# var", "bergamadaki kimler", "bergamada kim var". Ayrılma (-dan/-den/-tan/
# -ten) ve bulunma (-da/-de/-ta/-te) hâli ekleri YALNIZCA bu kalıp içinde
# soyulur, _district_from_word'e eklenmez: tek başına bir kelimede ("aydan",
# "sudan" gibi bir isim/soyad) bunları soymak yanlış ilçe üretirdi. Burada
# kelimenin hemen ardından "kimler/kim (var)" geldiği için bağlam nettir.
_DISTRICT_SOURCE_SUFFIXES = ("dan", "den", "tan", "ten", "da", "de", "ta", "te")
# "kimler var" / "kim var" / "kimler" / "kim" — ilçe kelimesinden SONRA gelen
# soru kuyruğu. Tek başına "kimler var" zaten list_all'dır (bkz.
# _BARE_LIST_FIXED_PHRASES); burada mutlaka bir ilçe kelimesi önde olmalı.
_DISTRICT_PEOPLE_TAILS = (
    ("kimler", "var"), ("kim", "var"), ("kimler", "kayıtlı"),
    ("kimler",), ("kim",),
)


def _district_from_source_word(word: str) -> str | None:
    """"bergamadan"/"bergamada"/"bergamadaki"/"bergamalılar" -> "bergama"."""
    district = _district_from_word(word)
    if district:
        return district
    for suf in _DISTRICT_SOURCE_SUFFIXES:
        if word.endswith(suf) and len(word) > len(suf) + 2:
            return word[: -len(suf)]
    return None


def _try_district_people_query(tokens: list[str]) -> ParsedIntent | None:
    """"bergamadan kimler var" / "bergamadaki kimler" -> list_district.
    Kalıp kasten dar: soru kuyruğunun ÖNÜNDE tam olarak TEK kelime (ilçe
    adı) durmalı, o kelimeden de bir ilçe adı çözülebilmeli."""
    for tail in _DISTRICT_PEOPLE_TAILS:
        if len(tokens) != len(tail) + 1 or tuple(tokens[-len(tail):]) != tail:
            continue
        district = _district_from_source_word(tokens[0])
        if district:
            return ParsedIntent(kind="list_district", district=district)
    return None


# Ürün/stok/fiyat sorgusu (Grup C): "toplam kaç saman satıldı", "ne kadar
# arpa var", "saman fiyatı", "arpa stoğu". Bu sorular defterin BİLMEDİĞİ
# şeyleri soruyor — sistem cari hesap tutar, stok/fiyat listesi tutmaz
# (CLAUDE.md kural 4: fiyat listesi bağlamaz, tutarı kullanıcı yazar).
# Yine de TANINIR: tanınmazsa cümle bir kişi adı ya da bir kayıt sanılıp
# sessizce yanlış bir şey yapılabilir. Bot "bu özellik henüz yok" der.
PRODUCT_QUERY_SOLD_WORDS = {"satıldı", "satildi", "sattım", "sattim", "satılmış", "satilmis"}
PRODUCT_QUERY_NOUNS = {
    "fiyat", "fiyatı", "fiyati", "fiyatını", "fiyatini",
    "stok", "stoğu", "stogu", "stoku", "stokta",
}
PRODUCT_QUERY_QUANTITY_WORDS = {"kaç", "kac", "ne", "kadar", "toplam", "toplamda"}
# Ürün adı yerine geçemeyecek kelimeler: bunlar bir bakiye/liste sorgusunun
# parçasıdır, ürün DEĞİL ("ne kadar borcu var" ürün sorgusu değildir).
_PRODUCT_QUERY_EXCLUDED = (
    BALANCE_KEYWORDS | BALANCE_KEYWORDS_BARE | BALANCE_FILLERS
    | TOTAL_BALANCE_QUALIFIERS | TOTAL_BALANCE_NOUNS
    | LIST_ALL_WORDS | LIST_DEBTORS_WORDS | LIST_CREDITORS_WORDS
    | LIST_ALL_SINGULAR_WORDS | DEBT_CLOSING_KEYWORDS
    | {"var", "yok", "kimler", "kim"}
)


def _product_query_name(tokens: list[str]) -> str | None:
    """Ürün adı adayı: dolgu/soru kelimeleri ayıklandıktan sonra kalan.
    Boş kalırsa ya da elenmiş bir kelimeye denk gelirse None (uydurulmaz)."""
    rest = [
        t
        for t in tokens
        if t not in PRODUCT_QUERY_SOLD_WORDS
        and t not in PRODUCT_QUERY_NOUNS
        and t not in PRODUCT_QUERY_QUANTITY_WORDS
        and t not in UNITS
        and t != "var"
    ]
    if not rest or set(rest) & _PRODUCT_QUERY_EXCLUDED:
        return None
    if any(_consume_number(rest, i) is not None for i in range(len(rest))):
        return None
    return " ".join(rest).strip() or None


def _try_product_query(tokens: list[str]) -> ParsedIntent | None:
    """"toplam kaç saman satıldı" / "ne kadar arpa var" / "saman fiyatı" /
    "arpa stoğu" -> product_query (henüz desteklenmeyen ürün sorgusu).

    Üç dar kalıp; hiçbiri tutmazsa None (cümle normal akışa devam eder):
      1. bir satış kelimesi ("satıldı") + bir miktar sorusu ("kaç"/"toplam"),
      2. cümlenin sonunda fiyat/stok kelimesi ("saman fiyatı"),
      3. "ne kadar {ürün} var" / "kaç {ürün} var".
    """
    token_set = set(tokens)
    # Bir KAYIT cümlesi asla ürün sorgusu sayılmaz: para birimi ya da (satış
    # kelimesi dışında) bir borç/tahsilat fiili varsa burada işimiz yok.
    if token_set & CURRENCY_UNITS:
        return None
    if token_set & ((DEBT_WORDS | PAYMENT_WORDS) - PRODUCT_QUERY_SOLD_WORDS):
        return None

    if token_set & PRODUCT_QUERY_SOLD_WORDS and token_set & PRODUCT_QUERY_QUANTITY_WORDS:
        product = _product_query_name(tokens)
        if product:
            return ParsedIntent(kind="product_query", product=product)
        return None

    if tokens[-1] in PRODUCT_QUERY_NOUNS and len(tokens) > 1:
        product = _product_query_name(tokens)
        if product:
            return ParsedIntent(kind="product_query", product=product)
        return None

    if tokens[-1] == "var" and token_set & PRODUCT_QUERY_QUANTITY_WORDS:
        product = _product_query_name(tokens)
        if product:
            return ParsedIntent(kind="product_query", product=product)
        return None

    return None


def _try_report_general(tokens: list[str]) -> ParsedIntent | None:
    """"genel rapor", "tüm zamanların raporu", "herkesin durumu",
    "bütün müşteriler" vb. -> tek anlamı olan genel durum raporu, menü
    sorulmadan direkt üretilir."""
    token_set = set(tokens)
    if token_set & REPORT_GENERAL_QUALIFIERS and token_set & REPORT_GENERAL_NOUNS:
        return ParsedIntent(kind="report_general")
    return None


def _try_report_daily(tokens: list[str]) -> ParsedIntent | None:
    """"günlük rapor", "bugünün raporu", "bugün ne oldu" vb. -> tek anlamı
    olan günlük rapor, menü sorulmadan direkt üretilir."""
    token_set = set(tokens)
    if token_set & REPORT_DAILY_QUALIFIERS and token_set & REPORT_DAILY_NOUNS:
        return ParsedIntent(kind="report_daily")
    return None


def _try_report_person_query(tokens: list[str]) -> ParsedIntent | None:
    """"{isim} ekstresi/raporu/dökümü" / "{isim} hesap dökümü" -> kişinin
    ekstre PDF'i istenir. Anahtar kelimeden önce genel/günlük rapor
    kelimelerinden biri geçiyorsa (ör. "bir durum raporu") bu bir kişi adı
    değildir — uydurmadan None dönülür, LLM'e bırakılır."""
    idx = next((i for i, tok in enumerate(tokens) if tok in REPORT_PERSON_KEYWORDS), None)
    if idx is None:
        return None
    person_tokens = [t for t in tokens[:idx] if t not in REPORT_PERSON_PRE_FILLERS]
    if not person_tokens or set(person_tokens) & REPORT_PERSON_EXCLUDED_PRECEDING:
        return None
    person = " ".join(person_tokens).strip()
    if not person:
        return None
    return ParsedIntent(kind="report_person", person_name=person)


def _try_report_menu(tokens: list[str]) -> ParsedIntent | None:
    """"rapor ver" / "rapor" -> hangi rapor istendiği belirsiz, bot günlük/genel
    seçimini buton ile sorar (kişiye özel istekler _try_report_person_query'de)."""
    remaining = [t for t in tokens if t not in REPORT_MENU_FILLERS]
    if remaining == ["rapor"]:
        return ParsedIntent(kind="report_menu")
    return None


# Para/mal bağlamı olan bir cümle ASLA create_person DEĞİLDİR: "ahmete 20
# balya saman ekle 5000 tl" ya da "ahmete borç ekle" gibi cümleler bir eylem
# kelimesi ("ekle") içerdiği için tetikleyiciyi karşılar, ama bunlar kayıt
# (borç/tahsilat) cümleleridir — create_person'a düşerlerse para kaydı
# kaybolur. Sayı, para birimi, ölçü birimi, borç/tahsilat fiili ya da bakiye
# kelimesi geçen her cümle bu niyetin dışında bırakılır; normal akış
# (borç/tahsilat/bakiye) aynen devam eder.
_CREATE_PERSON_BLOCKERS = (
    DEBT_WORDS | PAYMENT_WORDS | CURRENCY_UNITS | UNITS
    | BALANCE_KEYWORDS | BALANCE_KEYWORDS_BARE
    # "bir" bir sayı kelimesi olsa da burada neredeyse her zaman belirteçtir
    # ("bir kişi ekle", "{isim} isminde bir kişi oluştur"), o yüzden engel
    # sayılmaz — gerçek bir tutar cümlesinde ("bir milyon verdim") zaten
    # fiil ya da para birimi de bulunur ve cümle onlardan dolayı elenir.
    | (_NUMBER_WORDS - {"bir"})
    | {"borç", "borc", "tahsilat", "parası", "parasını"}
)


def _create_person_name(tokens: list[str]) -> str:
    """Kişi oluşturma cümlesinden SADECE ad-soyadı çıkarır.

    Üç adım, hepsi kasten ihtiyatlı (yanlış temizleme ismi bozar):
      1. Bir isimlendirme kelimesi ("adlı"/"adında"/"isimli"/"isminde")
         varsa isim ondan ÖNCEsidir; sonrası tamamen komut metnidir
         ("furkan duman adlı kişiyi sisteme kayıt et" -> "furkan duman").
      2. Baştaki dolgu/komut kelimeleri soyulur ("yeni kişi yıldız tilbe"
         -> "yıldız tilbe").
      3. Sondaki dolgu/komut kelimeleri soyulur ("serpil çiçek kişisini
         kayıt et" -> "serpil çiçek").
    ORTADAKİ kelimelere DOKUNULMAZ: soyadı bir komut kelimesine benzeyen
    biri ("ali kişi duman ekle") sessizce bozulmasın diye — yalnızca uçtaki
    belirgin komut dizileri ayıklanır."""
    idx = next((i for i, tok in enumerate(tokens) if tok in CREATE_PERSON_NAMING_WORDS), None)
    name_tokens = list(tokens[:idx] if idx is not None else tokens)

    while name_tokens and name_tokens[0] in CREATE_PERSON_FILLERS:
        name_tokens.pop(0)
    while name_tokens and name_tokens[-1] in CREATE_PERSON_FILLERS:
        name_tokens.pop()

    return " ".join(name_tokens).strip()


def _try_create_person_query(tokens: list[str]) -> ParsedIntent | None:
    """Yeni kişi OLUŞTURMA türevleri (CLAUDE.md > "Bot kayıt akışı — Grup
    2"): "ahmet adında yeni kişi oluştur", "ahmet adında kişi kayıt et",
    "ahmet duman kayıt et", "ahmet yıldırım oluştur", "ahmet yıldırım yeni
    kişi/isim", "furkan duman adlı kişiyi sisteme kayıt et", "faruk caner
    sisteme ekle", "ercüment çözer kişisini ekle" -> SADECE kişi ekleme
    niyeti, borç/tahsilat YOK.

    Tetikleyici (bkz. modül üstü CREATE_PERSON_* yorumu): açık bir eylem
    kelimesi (oluştur/kayıt/kaydet/ekle/aç/gir), ya da bir isimlendirme
    kelimesi (adlı/adında/isimli/isminde), ya da "yeni"+"kişi/isim" ikilisi.
    Cümlede para/mal bağlamı varsa (_CREATE_PERSON_BLOCKERS) hiç tetiklenmez.
    İsim, komut kelimeleri ayıklandıktan sonra geri kalandır (bkz.
    _create_person_name) — "ahmet duman kayıt et" içindeki "duman" bir dolgu
    DEĞİL, soyad olduğu için korunur."""
    token_set = set(tokens)
    triggered = bool(
        token_set & CREATE_PERSON_ACTIONS
        or token_set & CREATE_PERSON_NAMING_WORDS
        or ("yeni" in token_set and token_set & CREATE_PERSON_NOUN_WORDS)
    )
    if not triggered:
        return None
    if token_set & _CREATE_PERSON_BLOCKERS or any(_NUMBER_TOKEN.fullmatch(t) for t in tokens):
        return None

    person = _create_person_name(tokens)
    if not person:
        return None
    return ParsedIntent(kind="create_person", person_name=person)


# Tek kelime = arama (CLAUDE.md > "Bot sorgu anlama" Grup 1, madde 5): eğer
# kullanıcı tek bir kelime yazmışsa ve bu kelime hiçbir bilinen komut/fiil/
# anahtar kelime DEĞİLSE, Telegram'ın kendi aramasıymış gibi davranılır —
# isim/soyad/ilçede bu kelime geçen HERKES bakiyeleriyle listelenir (bkz.
# app/services/queries.py > search_persons). Bilinen bir komut/fiil/anahtar
# kelimeyse (ör. "rapor", "aldı", "kişiler") bu fonksiyona hiç gelinmez —
# parse() içinde daha spesifik kontroller zaten önce çalışır ve eşleşirse
# döner; buraya yalnızca HİÇBİRİ eşleşmediğinde (kind=None) düşülür.
_SINGLE_WORD_RESERVED = (
    DEBT_WORDS | PAYMENT_WORDS | CURRENCY_UNITS | UNITS | STOPWORDS
    | BALANCE_KEYWORDS | BALANCE_KEYWORDS_BARE | BALANCE_FILLERS
    | PERSON_CONTACT_KEYWORDS | INFO_MENU_KEYWORDS
    | LIST_VERBS | LIST_FILLERS | LIST_ALL_WORDS | LIST_DEBTORS_WORDS | LIST_CREDITORS_WORDS
    | REPORT_MENU_FILLERS | REPORT_GENERAL_QUALIFIERS | REPORT_GENERAL_NOUNS
    | REPORT_DAILY_QUALIFIERS | REPORT_DAILY_NOUNS
    | REPORT_PERSON_KEYWORDS | REPORT_PERSON_PRE_FILLERS
    | CREATE_PERSON_ACTIONS | CREATE_PERSON_NOUN_WORDS | CREATE_PERSON_NAMING_WORDS
    | ARCHIVE_ACTION_WORDS | ARCHIVE_RECREATE_MARKERS
    | set(FIELD_WORDS) | EDIT_ASSIGN_VERBS | set(EDIT_TRIGGER_CANONICALS) | EDIT_MENU_FILLERS
    | _NUMBER_WORDS
    | CREATE_PERSON_FILLERS
    | TOTAL_BALANCE_QUALIFIERS | TOTAL_BALANCE_NOUNS
    | DEBT_CLOSING_KEYWORDS | PRODUCT_QUERY_NOUNS | PRODUCT_QUERY_SOLD_WORDS
    | LIST_ALL_SINGULAR_WORDS | LIST_SUFFIX_WORDS
    | {"rapor", "sistemdeki", "kimler", "yeniden", "kim", "para"}
)


def _try_single_word_search(word: str) -> ParsedIntent | None:
    if word in _SINGLE_WORD_RESERVED:
        return None
    if _NUMBER_TOKEN.fullmatch(word):
        return None
    return ParsedIntent(kind="search", query=word)


def parse(raw_text: str) -> ParsedIntent | None:
    text = " ".join((raw_text or "").split())
    if not text:
        return None

    norm = normalize(text)
    tokens = _split_tokens(norm)
    if not tokens:
        return None

    listing = _try_list_query(tokens)
    if listing is not None:
        return listing

    # Toplam/genel bakiye ("tüm bakiye", "toplam borç") — kişi sorgularından
    # ve rapor niyetlerinden ÖNCE denenir: "tüm"/"toplam"/"total" aksi halde
    # bir kişi adı ya da bir dolgu kelimesi sanılıp yanlış niyete düşerdi.
    # Kalıp kasten dar tutulduğu için ("genel durum" gibi rapor kalıpları
    # burada eşleşmez) sonraki kontrollerin hiçbirini gölgelemez.
    total_balance = _try_total_balance_query(tokens)
    if total_balance is not None:
        return total_balance

    # Rapor genel/günlük niyetleri BARE liste kontrolünden ÖNCE denenir:
    # "müşteriler" artık hem bir bare liste kelimesi (LIST_ALL_WORDS) hem de
    # bir rapor ismi (REPORT_GENERAL_NOUNS) olduğu için, "bütün müşteriler"
    # gibi nitelik+isim ikilisi (report_general'ın kendi, daha spesifik
    # kalıbı) bare liste kontrolüne düşüp "kişi listesi" sanılmadan önce
    # burada yakalanmalı. Tek başına "müşteriler" (nitelik YOK) bu kontrolden
    # geçmez (REPORT_GENERAL_QUALIFIERS kesişimi boş kalır), bare liste
    # kontrolüne aynen düşmeye devam eder.
    report_general = _try_report_general(tokens)
    if report_general is not None:
        return report_general

    report_daily = _try_report_daily(tokens)
    if report_daily is not None:
        return report_daily

    bare_list_all = _try_bare_list_query(tokens)
    if bare_list_all is not None:
        return bare_list_all

    bare_district = _try_bare_district_query(tokens)
    if bare_district is not None:
        return bare_district

    # "bergamadan kimler var" / "bergamadaki kimler" — fiilsiz ilçe sorgusunun
    # soru biçimi. Tek başına "kimler var" zaten yukarıdaki bare liste
    # kontrolünde list_all olarak yakalanır, buraya düşmez.
    district_people = _try_district_people_query(tokens)
    if district_people is not None:
        return district_people

    # Rapor niyetleri en spesifikten en geneline denenir (genel/günlük - ki
    # ikisi de yukarıda bare liste kontrolünden önce zaten denendi - kişi en
    # sona): "genel raporu"/"günlük raporu" gibi kalıplar "raporu" kişi-eki
    # ile de eşleşebildiği için, kişi kontrolü bunlardan SONRA çalışmalı —
    # yoksa "genel"/"günlük" bir kişi adı sanılır.
    report_person = _try_report_person_query(tokens)
    if report_person is not None:
        return report_person

    report_menu = _try_report_menu(tokens)
    if report_menu is not None:
        return report_menu

    # Kişi silme/arşivleme (CLAUDE.md > "Bot kişi silme = arşivleme — Grup
    # 3"): create_person'dan ÖNCE denenir — "furkanı sil yeniden oluştur"
    # gibi bir cümle "oluştur" içerdiği için create_person'a düşebilirdi,
    # ama bir ARCHIVE_ACTION_WORDS üyesi varsa bu her zaman silme demektir.
    archive_person = _try_archive_person_query(tokens)
    if archive_person is not None:
        return archive_person

    # Kişi DÜZENLEME (CLAUDE.md > "Silme mesajı + kişi düzenleme — Grup 4"):
    # NET ("{kişi} {alan} {değer} yap") önce denenir, çünkü daha spesifik
    # bir kalıptır (belirli bir fiil grubuyla bitmeli); BELİRSİZ ("{kişi}
    # düzenle") daha geniş bir tetikleyici kümesine (fuzzy) dayanır. Archive
    # kontrolünden SONRA denenir: "sıfırla" gibi bir ARCHIVE_ACTION_WORDS
    # üyesi asla buraya karışmamalı (zaten yukarıda return ile ayrılıyor).
    edit_person_net = _try_edit_person_net(tokens)
    if edit_person_net is not None:
        return edit_person_net

    edit_person_menu = _try_edit_person_menu(tokens)
    if edit_person_menu is not None:
        return edit_person_menu

    # Yeni kişi OLUŞTURMA türevleri (CLAUDE.md > "Bot kayıt akışı — Grup 2"):
    # borç/tahsilat DEĞİL, sadece kişi ekleme niyeti. Rapor kontrollerinden
    # sonra (rapor anahtar kelimeleriyle çakışma riski yok ama sıra tutarlı
    # kalsın diye) ve bakiye/kişi-bilgisi kontrollerinden önce denenir.
    create_person = _try_create_person_query(tokens)
    if create_person is not None:
        return create_person

    # Kişi bilgisi: net iletişim niyeti (telefonu/adresi/nerede) önce
    # denenir, ancak belirsiz "bilgi ver" ondan sonra — ikisi de bir isim
    # gerektirir ve anahtar kelime kümeleri çakışmaz.
    person_contact = _try_person_contact_query(tokens)
    if person_contact is not None:
        return person_contact

    info_menu = _try_info_menu_query(tokens)
    if info_menu is not None:
        return info_menu

    # Bug (2026-07-26): "ahmet yılmaz 20 balya borcunu 15000 tl ödedi" gibi
    # bir TAHSİLAT cümlesi "borcunu" (bakiye anahtar kelimesi) içerdiği
    # için _try_balance_query'ye düşüyor ve "ahmet yılmaz 20 balya" diye
    # anlamsız bir isimle sahte bir bakiye sorgusuna dönüşüyordu; bu da
    # kural parser'ın "çözdüm" sanıp LLM'e hiç devretmemesine yol açıyordu.
    # Cümlede HEM bir bakiye kelimesi HEM bir borç/tahsilat fiili
    # (aldı/verdim/çekti/ödedi/yatırdı/verdi) varsa bu ikisi çelişir —
    # kural parser'ın basit "sayı/birim/ürün" ayrıştırması böyle karışık
    # bir cümleyi güvenle çözemez, LLM'e bırakılır (None dön).
    # Borç KAPANIŞI: "ali borcunu ödedi" — "borcunu" burada bir bakiye
    # sorgusu değil, kapanan borcun kendisidir. Anahtar kelime düşürülür,
    # kalan cümle normal TAHSİLAT akışından geçer (bkz. _strip_debt_closing).
    # Aşağıdaki çelişki kontrolünden ÖNCE yapılmalı — yoksa cümle "bakiye
    # kelimesi + kayıt fiili" sayılıp her seferinde LLM'e devrediliyordu.
    close_debt = False
    closing_tokens = _strip_debt_closing(tokens)
    if closing_tokens is not None:
        tokens = closing_tokens
        close_debt = True

    token_set = set(tokens)
    if token_set & BALANCE_KEYWORDS and token_set & (DEBT_WORDS | PAYMENT_WORDS):
        return None

    balance = _try_balance_query(tokens)
    if balance is not None:
        return balance

    bare_balance = _try_bare_balance_query(tokens)
    if bare_balance is not None:
        return bare_balance

    bare_ne_kadar = _try_bare_ne_kadar_query(tokens)
    if bare_ne_kadar is not None:
        return bare_ne_kadar

    # Ürün/stok/fiyat sorgusu (Grup C) — defterin tutmadığı bir bilgi
    # soruluyor. Tanınır ki bot "bu özellik henüz yok" desin; tanınmasa
    # cümle bir kişi adı ya da bir kayıt sanılabilirdi. Bakiye
    # kontrollerinden SONRA denenir ("ne kadar borcu var" ürün sorgusu değil).
    product_query = _try_product_query(tokens)
    if product_query is not None:
        return product_query

    # Kind, tutar çıkarılmadan ÖNCE tespit edilir: "borç" hem bir fiil
    # sinyali hem de (bkz. _extract_amount) tutarın bitişiğindeki bir
    # işaretçi olarak tüketilebilir ("15bin borç" -> 15000). Sıra tersine
    # çevrilirse ("...borç" tüketildikten sonra kind aransa) tek kind
    # sinyali "borç" olan cümlelerde (fiil hiç yoksa) kind kaybolurdu.
    kind = _detect_kind(tokens)
    if kind is None:
        # Kısa kayıt biçimi (CLAUDE.md > "Bot kayıt akışı — Grup 2"): fiil
        # yok ama {isim} {adet} {ürün} {tutar} yapısı net — borç varsay.
        short_record = _try_short_record(tokens)
        if short_record is not None:
            return short_record

        # Tek kelime = arama (madde 5): hiçbir komut/fiil/anahtar kelime
        # eşleşmediyse ve mesaj tek bir kelimeyse, Telegram arama gibi
        # davranılır (bkz. _try_single_word_search). "furkan bakiye" gibi
        # komutlu ifadeler zaten daha yukarıda (balance_query vb.) yakalanıp
        # dönmüş olduğundan buraya hiç düşmez.
        if len(tokens) == 1:
            search = _try_single_word_search(tokens[0])
            if search is not None:
                return search
        return None

    amount, tokens = _extract_amount(tokens)

    tokens = [t for t in tokens if t not in STOPWORDS]
    qty, unit, product_tokens, person_tokens = _extract_qty_unit(tokens)

    # Adet-birim bulunamadıysa ve ürün de yoksa, bu sayı aslında bir ADET
    # değil çıplak bir TUTARDIR ("mehmete 3000 verdim" — para birimi/"borç"
    # eki yok ama "3000" tek başına kalan sayı, ürün/birim bağlamı da yok).
    # CLAUDE.md > "Para vs adet": birim kelimesi yoksa sayı tutar sayılır.
    if amount is None and qty is not None and unit is None and not product_tokens:
        amount, qty = qty, None

    person_name = " ".join(person_tokens).strip() or None
    product = " ".join(product_tokens).strip() or None

    if not person_name:
        return None

    return ParsedIntent(
        kind=kind,
        person_name=person_name,
        qty=qty,
        unit=unit,
        product=product,
        amount=amount,
        # Tutar SÖYLENMEMİŞ bir borç kapanışı ("ali borcunu ödedi"): tutar
        # uydurulmaz, kişi çözülünce güncel bakiye teklif edilip onaylatılır.
        close_debt=close_debt and amount is None,
    )
