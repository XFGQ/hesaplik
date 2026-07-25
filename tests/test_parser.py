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
