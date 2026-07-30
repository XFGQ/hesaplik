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
  Sorgu:    "kişileri listele/sırala" -> list_all
            "borçluları listele" -> list_debtors
            "alacaklıları listele" -> list_creditors
            "{ilçe}lileri listele" -> list_district (ör. "bergamalıları listele")
  Sorgu (Grup 1, CLAUDE.md > "Bot sorgu anlama — kapsamlı genişletme"):
            "kişiler" / "kişileri say" / "sistemdeki kişiler" / "tüm kişiler" /
              "kimler var" -> list_all (fiilsiz, bkz. _try_bare_list_all)
            "bergamalılar" (fiilsiz, tek kelime) -> list_district (bkz.
              _try_bare_district_query)
            "{isim} bakiye/borç/borc/durum/hesap/cari/cariye/alacak/alacağı"
              VEYA ters sıra "{anahtar} {isim}" -> balance_query (bkz.
              BALANCE_KEYWORDS_BARE, _try_bare_balance_query). Bunlar
              inflected (borcu/hesabı/...) hâllerden AYRI bir küme: bare
              "borç"/"borc" zaten hem bir tutar işaretçisi hem "debt" kind
              sinyali olduğu için (bkz. _AMOUNT_MARKERS, _detect_kind),
              BALANCE_KEYWORDS'e (inflected, guard'ın kullandığı küme)
              karıştırılırsa "...aldı 15000 tl borç" gibi normal borç
              cümleleri yanlışlıkla çelişki sayılıp None dönerdi.
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
  Kişi silme/arşivleme (CLAUDE.md > "Bot kişi silme = arşivleme — Grup 3"):
            "{isim} sil/kaldır/arşivle/sıfırla" -> archive_person (kişi
              GERÇEKTEN silinmez, arşive taşınır + pasifleştirilir).
            "{isim} sil/sıfırla yeniden oluştur/aç" -> archive_and_recreate
              (arşivle + aynı isimle temiz/bakiyesi sıfır yeni kişi açılır).
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
DEBT_WORDS = {"aldı", "verdim", "verdik", "çekti", "sattım", "çıktı", "gitti"}
PAYMENT_WORDS = {
    "ödedi", "yatırdı", "verdi", "aldım", "aldık", "geldi", "tahsil",
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
    "alacak", "alacağı",
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

LIST_VERBS = {"listele", "sırala", "listeler", "sıralar", "listelesene", "sıralasana"}
LIST_FILLERS = {"tüm", "tum", "bütün", "butun", "hepsini", "hepsi", "lütfen", "lutfen", "bana"}
LIST_ALL_WORDS = {"kişileri", "kisileri", "kişiler", "kisiler", "herkesi", "herkes"}
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

# Niyet belirlendikten sonra kişi/ürün metninden temizlenen kelimeler.
STOPWORDS = DEBT_WORDS | PAYMENT_WORDS | {
    "borç", "borc", "yaz", "yazdı", "yazdım", "parası", "parasını",
    "için", "icin", "ettim",
}

# Yeni kişi OLUŞTURMA türevleri (CLAUDE.md > "Bot kayıt akışı — Grup 2"):
# "ahmet adında yeni kişi oluştur", "ahmet duman kayıt et", "ahmet yıldırım
# oluştur", "ahmet yıldırım yeni kişi/isim" — borç YOK, sadece kişi
# eklensin isteniyor. Tetikleyici iki türlü olabilir:
#   1. Açık bir eylem kelimesi ("oluştur" ya da "kayıt", "kayıt et"teki gibi).
#   2. "adında" (isimlendirme kalıbı, tek başına yeterli).
#   3. "yeni" + ("kişi"/"isim") ikilisi birlikte ("yeni kişi"/"yeni isim").
# Tek başına "yeni" ya da "kişi" (madde 3'ün yarısı) tetiklemez — aksi halde
# alakasız cümlelerde de yanlışlıkla eşleşirdi.
CREATE_PERSON_ACTIONS = {"oluştur", "olustur", "kayıt", "kayit"}
CREATE_PERSON_NAMING_WORD = "adında"
CREATE_PERSON_NOUN_WORDS = {"kişi", "kisi", "isim"}
# İsim öbeğinden ayıklanan dolgu/komut kelimeleri (isme KARIŞMAMALI).
CREATE_PERSON_FILLERS = {
    "adında", "adinda", "yeni", "kişi", "kisi", "isim", "et", "oluştur",
    "olustur", "kayıt", "kayit",
}

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


def _try_archive_person_query(tokens: list[str]) -> ParsedIntent | None:
    """"{isim} sil/kaldır/arşivle/sıfırla" -> archive_person. Aynı cümlede
    "yeniden" + "oluştur"/"aç" de varsa -> archive_and_recreate (arşivle +
    aynı isimle temiz yeni kişi). Silme fiili yoksa hiç tetiklenmez."""
    token_set = set(tokens)
    if not (token_set & ARCHIVE_ACTION_WORDS):
        return None

    recreate = "yeniden" in token_set and bool(token_set & ARCHIVE_RECREATE_MARKERS)
    person_tokens = [t for t in tokens if t not in ARCHIVE_FILLERS]
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
               # "archive_person" | "archive_and_recreate" | "edit_person"
    person_name: str | None = None
    qty: Decimal | None = None
    unit: str | None = None
    product: str | None = None
    amount: Decimal | None = None
    district: str | None = None
    query: str | None = None  # yalnızca kind == "search" için: aranan tek kelime
    field: str | None = None  # yalnızca kind == "edit_person": full_name/phone/city/district/address/note
    new_value: str | None = None  # yalnızca kind == "edit_person", NET komutta dolu (bkz. _try_edit_person_net)


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

    if word in LIST_ALL_WORDS:
        return ParsedIntent(kind="list_all")
    if word in LIST_DEBTORS_WORDS:
        return ParsedIntent(kind="list_debtors")
    if word in LIST_CREDITORS_WORDS:
        return ParsedIntent(kind="list_creditors")

    district = _strip_district_suffix(word)
    if district:
        return ParsedIntent(kind="list_district", district=district)
    return None


# Grup 1, madde 4 (CLAUDE.md > "Bot sorgu anlama"): "kişiler" gibi bir liste
# isteği FİİLSİZ de gelebilir ("kişileri listele" değil sadece "kişiler").
# "sistemdeki" bu bağlamda ek bir dolgu kelimesi (LIST_FILLERS zaten
# tüm/tum/bütün/butun/hepsini/hepsi/lütfen/lutfen/bana içeriyor).
_BARE_LIST_ALL_QUALIFIERS = LIST_FILLERS | {"sistemdeki"}
# "kişileri say" / "kimler var": fiil yerine geçen sabit iki kelimelik
# kalıplar, filler çıkarma mantığına uymadıkları için ayrı kontrol edilir.
_BARE_LIST_ALL_FIXED_PHRASES = {
    ("kişileri", "say"), ("kisileri", "say"),
    ("kimler", "var"),
}


def _try_bare_list_all(tokens: list[str]) -> ParsedIntent | None:
    """"kişiler", "tüm kişiler", "sistemdeki kişiler", "kişileri say",
    "kimler var" -> list_all, hiçbir listele/sırala fiili olmadan."""
    if tuple(tokens) in _BARE_LIST_ALL_FIXED_PHRASES:
        return ParsedIntent(kind="list_all")

    remaining = [t for t in tokens if t not in _BARE_LIST_ALL_QUALIFIERS]
    if remaining and all(t in LIST_ALL_WORDS for t in remaining):
        return ParsedIntent(kind="list_all")
    return None


# Bare ilçe sorgusu: "listele" fiili olmadan tek başına "bergamalılar" gibi
# bir kelime de bir ilçe listesi isteği sayılır (madde 4). Ama LIST_ALL/
# DEBTORS/CREDITORS kelimeleri de tesadüfen "-ler"/"-lar" ile bitebildiği
# için ("kişiler", "borçlular", "alacaklılar") bunlar KESİNLİKLE hariç
# tutulur — yoksa "borçlular" yanlışlıkla district="borç" sanılırdı.
_DISTRICT_BARE_EXCLUDED = LIST_ALL_WORDS | LIST_DEBTORS_WORDS | LIST_CREDITORS_WORDS


def _try_bare_district_query(tokens: list[str]) -> ParsedIntent | None:
    """"bergamalılar" (tek kelime, fiilsiz) -> list_district. "bergamalıları
    listele" ile aynı anlam, yalnızca fiil eksik."""
    if len(tokens) != 1:
        return None
    word = tokens[0]
    if word in _DISTRICT_BARE_EXCLUDED:
        return None
    district = _strip_district_suffix(word)
    if district:
        return ParsedIntent(kind="list_district", district=district)
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


def _try_create_person_query(tokens: list[str]) -> ParsedIntent | None:
    """Yeni kişi OLUŞTURMA türevleri (CLAUDE.md > "Bot kayıt akışı — Grup
    2"): "ahmet adında yeni kişi oluştur", "ahmet adında kişi kayıt et",
    "ahmet duman kayıt et", "ahmet yıldırım oluştur", "ahmet yıldırım yeni
    kişi/isim" -> SADECE kişi ekleme niyeti, borç/tahsilat YOK.

    Tetikleyici (bkz. modül üstü CREATE_PERSON_* yorumu): açık bir eylem
    kelimesi (oluştur/kayıt), ya da "adında", ya da "yeni"+"kişi/isim"
    ikilisi. İsim, tetikleyici/dolgu kelimeleri (CREATE_PERSON_FILLERS)
    ayıklandıktan sonra geri kalan kelimelerdir — "ahmet duman kayıt et"
    içindeki "duman" bir dolgu DEĞİL, soyad olduğu için korunur."""
    token_set = set(tokens)
    triggered = bool(
        token_set & CREATE_PERSON_ACTIONS
        or CREATE_PERSON_NAMING_WORD in token_set
        or ("yeni" in token_set and token_set & CREATE_PERSON_NOUN_WORDS)
    )
    if not triggered:
        return None

    person_tokens = [t for t in tokens if t not in CREATE_PERSON_FILLERS]
    person = " ".join(person_tokens).strip()
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
    | CREATE_PERSON_ACTIONS | CREATE_PERSON_NOUN_WORDS
    | ARCHIVE_ACTION_WORDS | ARCHIVE_RECREATE_MARKERS
    | set(FIELD_WORDS) | EDIT_ASSIGN_VERBS | set(EDIT_TRIGGER_CANONICALS) | EDIT_MENU_FILLERS
    | _NUMBER_WORDS
    | {"rapor", "sistemdeki", "kimler", CREATE_PERSON_NAMING_WORD, "yeniden"}
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

    bare_list_all = _try_bare_list_all(tokens)
    if bare_list_all is not None:
        return bare_list_all

    bare_district = _try_bare_district_query(tokens)
    if bare_district is not None:
        return bare_district

    # Rapor niyetleri en spesifikten en geneline denenir (genel/günlük/kişi
    # önce, tek başına "rapor" en sona): "genel raporu"/"günlük raporu" gibi
    # kalıplar "raporu" kişi-eki ile de eşleşebildiği için, kişi kontrolü
    # bunlardan SONRA çalışmalı — yoksa "genel"/"günlük" bir kişi adı sanılır.
    report_general = _try_report_general(tokens)
    if report_general is not None:
        return report_general

    report_daily = _try_report_daily(tokens)
    if report_daily is not None:
        return report_daily

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
    token_set = set(tokens)
    if token_set & BALANCE_KEYWORDS and token_set & (DEBT_WORDS | PAYMENT_WORDS):
        return None

    balance = _try_balance_query(tokens)
    if balance is not None:
        return balance

    bare_balance = _try_bare_balance_query(tokens)
    if bare_balance is not None:
        return bare_balance

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
    )
