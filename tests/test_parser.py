from decimal import Decimal

from app.services.parser import parse


def test_ornek_cumle_borc_urunlu():
    p = parse("furkan duman 20 balya saman aldı 15000 tl borç")
    assert p.kind == "debt"
    assert p.person_name == "furkan duman"
    assert p.qty == Decimal("20")
    assert p.unit == "balya"
    assert p.product == "saman"
    assert p.amount == Decimal("15000")


def test_buyuk_harf_ve_turkce_i_duyarli():
    p = parse("AHMET 20 BALYA SAMAN ALDI 15000 TL BORÇ")
    assert p.kind == "debt"
    assert p.person_name == "ahmet"
    assert p.product == "saman"


def test_borc_varyanti_verdim():
    p = parse("ahmet 5 çuval arpa verdim 1000 tl borç")
    assert p.kind == "debt"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("5")
    assert p.unit == "çuval"
    assert p.product == "arpa"
    assert p.amount == Decimal("1000")


def test_borc_varyanti_cekti():
    p = parse("mehmet 10 kg şeker çekti 500 tl borç")
    assert p.kind == "debt"
    assert p.qty == Decimal("10")
    assert p.unit == "kg"
    assert p.product == "şeker"
    assert p.amount == Decimal("500")


def test_borc_yaz_nakit_kalemsiz():
    p = parse("ayşe 200 tl borç yazdım")
    assert p.kind == "debt"
    assert p.person_name == "ayşe"
    assert p.qty is None
    assert p.unit is None
    assert p.product is None
    assert p.amount == Decimal("200")


def test_birim_taninmazsa_bos_kalir():
    p = parse("ayşe 5 yumurta aldı 50 tl borç")
    assert p.kind == "debt"
    assert p.qty == Decimal("5")
    assert p.unit is None
    assert p.product == "yumurta"


def test_tahsilat_basit():
    p = parse("ahmet 20000 tl ödedi")
    assert p.kind == "payment"
    assert p.person_name == "ahmet"
    assert p.amount == Decimal("20000")
    assert p.qty is None
    assert p.product is None


def test_tahsilat_yatirdi():
    p = parse("mehmet 1500 tl yatırdı")
    assert p.kind == "payment"
    assert p.amount == Decimal("1500")


def test_tahsilat_verdi():
    p = parse("ayşe 750 lira verdi")
    assert p.kind == "payment"
    assert p.person_name == "ayşe"
    assert p.amount == Decimal("750")


def test_tahsilat_urunlu():
    p = parse("ahmet 30 balya saman parası ödedi 20000 tl")
    assert p.kind == "payment"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("30")
    assert p.unit == "balya"
    assert p.product == "saman"
    assert p.amount == Decimal("20000")


def test_bakiye_sorgusu_borcu_ne():
    p = parse("ahmet borcu ne")
    assert p.kind == "balance_query"
    assert p.person_name == "ahmet"


def test_bakiye_sorgusu_hesabi_nedir():
    p = parse("mehmet hesabı nedir")
    assert p.kind == "balance_query"
    assert p.person_name == "mehmet"


def test_bakiye_sorgusu_bakiyesi_ne_kadar():
    p = parse("furkan duman bakiyesi ne kadar")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan duman"


def test_bakiye_sorgusu_durumu_kac():
    p = parse("ayşe durumu kaç")
    assert p.kind == "balance_query"
    assert p.person_name == "ayşe"


def test_bakiye_sorgusu_borcunu_soyle():
    p = parse("furkan borcunu söyle")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_borcunu_goster():
    p = parse("furkan borcunu göster")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_hesabi_yalin():
    p = parse("furkan hesabı")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_hesabini_soyle():
    p = parse("furkan hesabını söyle")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_hesabi_ne():
    p = parse("furkan hesabı ne")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_bakiyesi_yalin():
    p = parse("furkan bakiyesi")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_bakiyesini_soyle():
    p = parse("furkan bakiyesini söyle")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_durumu_yalin():
    p = parse("furkan durumu")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_durumunu_soyle():
    p = parse("furkan durumunu söyle")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_sorgusu_ne_kadar_borcu_var():
    p = parse("furkan ne kadar borcu var")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_borc_kelimesi_tahsilat_fiiliyle_karisirsa_kural_parser_pes_eder():
    # Bug (2026-07-26): "borcunu" bir bakiye anahtar kelimesi olduğu için
    # bu tahsilat cümlesi yanlışlıkla "ahmet yılmaz 20 balya" diye anlamsız
    # bir isimle sahte bir bakiye sorgusuna dönüşüyordu — kural parser
    # "çözdüm" sandığı için LLM fallback'e hiç düşmüyordu. Cümlede hem
    # bakiye kelimesi hem tahsilat fiili varsa artık None dönmeli (LLM'e
    # bırak), yanlış bir niyet UYDURULMAMALI.
    p = parse("ahmet yılmaz 20 balya borcunu 15000 tl ödedi")
    assert p is None


def test_borc_kelimesi_borc_fiiliyle_karisirsa_kural_parser_pes_eder():
    p = parse("ahmet borcunu aldı 500 tl")
    assert p is None


def test_turkce_sayi_kelimeleri_miktar_ve_tutar():
    p = parse("ahmet yirmi balya saman aldı on beş bin tl borç")
    assert p.kind == "debt"
    assert p.qty == Decimal("20")
    assert p.unit == "balya"
    assert p.amount == Decimal("15000")


def test_ondalik_virgul_ve_binlik_nokta():
    p = parse("mehmet 1.500,50 tl ödedi")
    assert p.kind == "payment"
    assert p.amount == Decimal("1500.50")


def test_binlik_nokta_ayraci():
    p = parse("ayşe 15.000 tl borç yazdı")
    assert p.kind == "debt"
    assert p.amount == Decimal("15000")


def test_fazla_bosluk_toleransli():
    p = parse("  ahmet   20   balya   saman   aldı   15000   tl   borç  ")
    assert p.kind == "debt"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("20")
    assert p.amount == Decimal("15000")


def test_anlasilmayan_metin_none_doner():
    assert parse("bugün hava çok güzel") is None


def test_bos_metin_none_doner():
    assert parse("") is None
    assert parse("   ") is None


def test_tutar_yoksa_amount_bos_kalir():
    # Niyet türü (borç) anlaşılsa da tutar yoksa parser uydurmaz; "anlaşılmadı"
    # kararını intent_resolver, amount=None'a bakarak verir.
    p = parse("ahmet saman aldı borç")
    assert p.kind == "debt"
    assert p.amount is None


# --------------------------------------------------------------- sorgu komutları


def test_sorgu_kisileri_listele():
    p = parse("kişileri listele")
    assert p.kind == "list_all"


def test_sorgu_kisileri_sirala():
    p = parse("kişileri sırala")
    assert p.kind == "list_all"


def test_sorgu_tum_kisileri_listele():
    p = parse("tüm kişileri listele")
    assert p.kind == "list_all"


def test_sorgu_kisiler_listele_tekil_cogul():
    p = parse("kişiler listele")
    assert p.kind == "list_all"


def test_sorgu_buyuk_harf_ve_turkce_i():
    p = parse("KİŞİLERİ LİSTELE")
    assert p.kind == "list_all"


def test_sorgu_borclulari_listele():
    p = parse("borçluları listele")
    assert p.kind == "list_debtors"


def test_sorgu_borclular_sirala():
    p = parse("borçlular sırala")
    assert p.kind == "list_debtors"


def test_sorgu_alacaklilari_listele():
    p = parse("alacaklıları listele")
    assert p.kind == "list_creditors"


def test_sorgu_alacaklilar_sirala():
    p = parse("alacaklılar sırala")
    assert p.kind == "list_creditors"


def test_sorgu_ilce_bergama():
    p = parse("bergamalıları listele")
    assert p.kind == "list_district"
    assert p.district == "bergama"


def test_sorgu_ilce_ahmetbeyler():
    p = parse("ahmetbeylerlileri listele")
    assert p.kind == "list_district"
    assert p.district == "ahmetbeyler"


def test_sorgu_ilce_fazla_bosluk_toleransli():
    p = parse("   bergamalıları    listele   ")
    assert p.kind == "list_district"
    assert p.district == "bergama"


def test_sorgu_borc_yazma_ile_karismaz():
    # "borçluları listele" içinde "borç" geçse de bu bir sorgu, borç kaydı değil.
    p = parse("borçluları listele")
    assert p.kind == "list_debtors"
    assert p.person_name is None
    assert p.amount is None


# --------------------------------------------------------------- rapor niyetleri


def test_rapor_ver_menu_doner():
    p = parse("rapor ver")
    assert p.kind == "report_menu"


def test_bare_rapor_menu_doner():
    p = parse("rapor")
    assert p.kind == "report_menu"


def test_rapor_ver_dolgu_kelimeli():
    p = parse("bana rapor ver lütfen")
    assert p.kind == "report_menu"


def test_isim_ekstresi_report_person():
    p = parse("ahmet yılmaz ekstresi")
    assert p.kind == "report_person"
    assert p.person_name == "ahmet yılmaz"


def test_isim_ekstre_report_person():
    p = parse("furkan ekstre")
    assert p.kind == "report_person"
    assert p.person_name == "furkan"


def test_isim_raporu_report_person():
    p = parse("mehmet raporu")
    assert p.kind == "report_person"
    assert p.person_name == "mehmet"


def test_isimsiz_ekstresi_none_doner():
    # Anahtar kelime var ama önünde isim yok -> anlamsız, uydurma.
    p = parse("ekstresi")
    assert p is None


# ------------------------------------------------- rapor: genel durum (report_general)


def test_rapor_genel_rapor():
    p = parse("genel rapor")
    assert p.kind == "report_general"


def test_rapor_genel_durum():
    p = parse("genel durum")
    assert p.kind == "report_general"


def test_rapor_genel_durum_raporu():
    p = parse("genel durum raporu")
    assert p.kind == "report_general"


def test_rapor_tum_zamanlarin_raporu():
    p = parse("tüm zamanların raporu")
    assert p.kind == "report_general"


def test_rapor_herkesin_durumu():
    p = parse("herkesin durumu")
    assert p.kind == "report_general"


def test_rapor_butun_musteriler():
    p = parse("bütün müşteriler")
    assert p.kind == "report_general"


def test_rapor_tum_rapor():
    p = parse("tüm rapor")
    assert p.kind == "report_general"


def test_rapor_genel_raporu_kisi_sanilmaz():
    # "genel raporu" hem report_general hem (yanlışlıkla) report_person
    # ("raporu" kişi eki) ile eşleşebilirdi — genel kontrol önce çalışmalı,
    # "genel" bir kişi adı SANILMAMALI.
    p = parse("genel raporu")
    assert p.kind == "report_general"


def test_rapor_tum_musterilerin_durumu_ne():
    p = parse("tüm müşterilerin durumu ne")
    assert p.kind == "report_general"


# ------------------------------------------------- rapor: günlük (report_daily)


def test_rapor_gunluk_rapor():
    p = parse("günlük rapor")
    assert p.kind == "report_daily"


def test_rapor_gunun_raporu():
    p = parse("günün raporu")
    assert p.kind == "report_daily"


def test_rapor_bugunun_raporu():
    p = parse("bugünün raporu")
    assert p.kind == "report_daily"


def test_rapor_gun_raporu():
    p = parse("gün raporu")
    assert p.kind == "report_daily"


def test_rapor_bugunku_hareketler():
    p = parse("bugünkü hareketler")
    assert p.kind == "report_daily"


def test_rapor_bugun_ne_yaptik():
    p = parse("bugün ne yaptık")
    assert p.kind == "report_daily"


def test_rapor_bugun_ne_oldu():
    p = parse("bugün ne oldu")
    assert p.kind == "report_daily"


def test_rapor_bugunun_raporu_kisi_sanilmaz():
    # "bugünün raporu" da aynı çakışma riskini taşır: günlük kontrol önce
    # çalışmalı, "bugünün" bir kişi adı SANILMAMALI.
    p = parse("bugünün raporu")
    assert p.kind == "report_daily"
    assert p.person_name is None


# ------------------------------------------------- rapor: kişi (report_person) — ek çekimler


def test_rapor_isim_dokumu():
    p = parse("ayşe dökümü")
    assert p.kind == "report_person"
    assert p.person_name == "ayşe"


def test_rapor_isim_hesap_dokumunu_ver():
    p = parse("ahmetin hesap dökümünü ver")
    assert p.kind == "report_person"
    assert p.person_name == "ahmetin"


def test_rapor_isim_ekstresini():
    p = parse("mehmedin ekstresini istiyorum")
    assert p.kind == "report_person"
    assert p.person_name == "mehmedin"


def test_rapor_isim_raporunu():
    p = parse("furkanın raporunu ver")
    assert p.kind == "report_person"
    assert p.person_name == "furkanın"


# ------------------------------------------------- rapor: belirsiz cümle LLM'e düşer


def test_rapor_belirsiz_durum_raporu_regex_pes_eder():
    # "bir durum raporu" ("durum" genel niteleyicisiz) ne report_general'a
    # ne report_person'a net uyar — kural parser uydurmadan pes etmeli,
    # LLM fallback devreye girsin (bkz. test_message_processor.py).
    p = parse("bana bir durum raporu hazırla")
    assert p is None
