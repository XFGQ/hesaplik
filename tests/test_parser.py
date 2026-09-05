from decimal import Decimal

import pytest

from app.services import parser
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


def test_tahsilat_yonu_den_ekiyle_aldim():
    # "aldı" (o aldı) = borç ama "aldım" (ben aldım) = tahsilat — fiil
    # çekimi yönü zaten kodluyor (bkz. CLAUDE.md "LLM son çare").
    p = parse("mehmetten 5000 aldım")
    assert p.kind == "payment"
    assert p.person_name == "mehmetten"
    assert p.amount == Decimal("5000")
    assert p.qty is None


def test_tahsilat_bitisik_bin_carpimi_3milyon_degil():
    # Kritik regresyon: "3bin" 3 milyon DEĞİL 3000 olmalı.
    p = parse("aliden 3bin lira aldım")
    assert p.kind == "payment"
    assert p.person_name == "aliden"
    assert p.amount == Decimal("3000")


def test_borc_yonu_e_ekiyle_verdim():
    p = parse("mehmete 3000 verdim")
    assert p.kind == "debt"
    assert p.person_name == "mehmete"
    assert p.amount == Decimal("3000")
    assert p.qty is None


def test_borc_urunlu_bitisik_bin_ve_borc_isaretcisi():
    p = parse("ahmet 20 balya saman aldı 15bin borç")
    assert p.kind == "debt"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("20")
    assert p.unit == "balya"
    assert p.product == "saman"
    assert p.amount == Decimal("15000")


def test_tahsilat_odedi_tl_siz():
    p = parse("ali 500 ödedi")
    assert p.kind == "payment"
    assert p.person_name == "ali"
    assert p.amount == Decimal("500")
    assert p.qty is None


def test_tahsilat_uc_sahis_verdi():
    p = parse("ahmet 20 balya aldı")
    assert p.kind == "debt"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("20")


def test_tahsilat_tahsil_ettim():
    p = parse("mehmetten tahsil ettim 2000 tl")
    assert p.kind == "payment"
    assert p.person_name == "mehmetten"
    assert p.amount == Decimal("2000")


def test_borc_verdik_coguldan():
    p = parse("ahmete 1000 verdik")
    assert p.kind == "debt"
    assert p.amount == Decimal("1000")


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


def test_borc_kapanisi_tahsilat_olarak_cozulur():
    # Bug (2026-07-26): "borcunu" bir bakiye anahtar kelimesi olduğu için
    # bu tahsilat cümlesi yanlışlıkla "ahmet yılmaz 20 balya" diye anlamsız
    # bir isimle sahte bir bakiye sorgusuna dönüşüyordu; ilk çözüm cümleyi
    # (yavaş) LLM'e devretmekti.
    #
    # 2026-08-31 genişletmesi: "borcunu ödedi" düzenli bir kalıptır ve
    # regex'in kesin çözmesi gerekir (CLAUDE.md > "LLM son çare, regex
    # birincil"). Anahtar kelime düşürülür, kalan cümle normal tahsilat
    # akışından geçer — kişi ve tutar doğru ayrılır.
    p = parse("ahmet yılmaz 20 balya borcunu 15000 tl ödedi")
    assert p.kind == "payment"
    assert p.person_name == "ahmet yılmaz"
    assert p.qty == Decimal("20")
    assert p.unit == "balya"
    assert p.amount == Decimal("15000")
    assert p.close_debt is False  # tutar SÖYLENMİŞ, bakiyeden türetilmeyecek


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


def test_sorgu_insanlari_listele():
    p = parse("insanları listele")
    assert p.kind == "list_all"


def test_sorgu_musterileri_listele():
    p = parse("müşterileri listele")
    assert p.kind == "list_all"


def test_sorgu_hepsini_listele():
    p = parse("hepsini listele")
    assert p.kind == "list_all"


def test_sorgu_kisileri_goster():
    p = parse("kişileri göster")
    assert p.kind == "list_all"


def test_sorgu_kisileri_getir():
    p = parse("kişileri getir")
    assert p.kind == "list_all"


def test_sorgu_herkesi_goster():
    p = parse("herkesi göster")
    assert p.kind == "list_all"


def test_sorgu_bare_insanlar():
    p = parse("insanlar")
    assert p.kind == "list_all"


def test_sorgu_bare_musteriler():
    p = parse("müşteriler")
    assert p.kind == "list_all"


def test_sorgu_bare_listele_tek():
    p = parse("listele")
    assert p.kind == "list_all"


def test_sorgu_musteri_listesi():
    p = parse("müşteri listesi")
    assert p.kind == "list_all"


def test_sorgu_kisi_listesi():
    p = parse("kişi listesi")
    assert p.kind == "list_all"


def test_sorgu_butun_musteriler_rapor_sanilir_liste_degil():
    # "bütün müşteriler" report_general'ın kendi kalıbı (nitelik+isim) —
    # bare liste kontrolüne düşüp list_all sanılmamalı (bkz. parse()
    # sıralaması: report_general bare liste kontrolünden önce denenir).
    p = parse("bütün müşteriler")
    assert p.kind == "report_general"


def test_sorgu_ahmeti_listele_kisiye_dusmez():
    # "ahmeti" bilinen bir liste kelimesi/ilçe eki değil — yanlışlıkla
    # list_all/list_district sanılmamalı.
    p = parse("ahmeti listele")
    assert p is None


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


# ------------------------------------------------- kişi bilgisi (CLAUDE.md > "DÜZELTME —
# 'bilgi ver' belirsiz, SOR"): net iletişim niyeti (person_contact) sorulmadan
# çalışır, belirsiz "bilgi ver" (info_menu) bot'a üç seçenek sordurur.


def test_esma_borcu_ne_bakiye_sorgusu_sormadan():
    p = parse("esma borcu ne")
    assert p.kind == "balance_query"
    assert p.person_name == "esma"


def test_bilgi_ver_belirsiz_info_menu_doner():
    p = parse("esma bilgi ver")
    assert p.kind == "info_menu"
    assert p.person_name == "esma"


def test_bilgi_ver_hitapli_isimle_info_menu_doner():
    p = parse("esma abla bilgi ver")
    assert p.kind == "info_menu"
    assert p.person_name == "esma abla"


def test_bilgi_ver_soyadli_isim_korunur():
    # "şeker" bilinen bir hitap değil, soyad olarak korunmalı.
    p = parse("esma şeker bilgi ver")
    assert p.kind == "info_menu"
    assert p.person_name == "esma şeker"


def test_bilgi_tek_basina_info_menu_doner():
    p = parse("ahmet bilgisi")
    assert p.kind == "info_menu"
    assert p.person_name == "ahmet"


def test_bilgilerini_ver_info_menu_doner():
    p = parse("ahmet bilgilerini ver")
    assert p.kind == "info_menu"
    assert p.person_name == "ahmet"


def test_isimsiz_bilgi_ver_none_doner():
    p = parse("bilgi ver")
    assert p is None


def test_telefonu_person_contact_doner():
    p = parse("esma telefonu")
    assert p.kind == "person_contact"
    assert p.person_name == "esma"


def test_numarasi_person_contact_doner():
    p = parse("ahmet numarası ne")
    assert p.kind == "person_contact"
    assert p.person_name == "ahmet"


def test_adresi_person_contact_doner():
    p = parse("mehmet adresi ne")
    assert p.kind == "person_contact"
    assert p.person_name == "mehmet"


def test_nerede_oturuyor_person_contact_doner():
    p = parse("ahmet nerede oturuyor")
    assert p.kind == "person_contact"
    assert p.person_name == "ahmet"


def test_isimsiz_telefonu_none_doner():
    p = parse("telefonu ne")
    assert p is None


# --------------------------------------------------------------- Grup 1 (CLAUDE.md > "Bot
# sorgu anlama — kapsamlı genişletme"), madde 1: BARE (çekimsiz) bakiye anahtar
# kelimeleri — "furkan bakiye", "durum furkan" gibi kelime sırası esnek tüm
# türevler. İnflected hâller (borcu/hesabı/bakiyesi/durumu) yukarıda zaten
# test edildi; burada YALNIZCA yeni bare kelimeler (bakiye, borç, borc,
# durum, hesap, cari, cariye, alacak, alacağı) ve ters sıra (madde 1'de
# istenen "en az 20 varyasyon").


def test_bakiye_bare_isim_sonra_bakiye():
    p = parse("furkan bakiye")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_bakiye_sonra_isim():
    p = parse("bakiye furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_isim_sonra_borc():
    p = parse("furkan borc")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_isim_sonra_borc_turkce():
    p = parse("furkan borç")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_borc_sonra_isim():
    p = parse("borç furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_isim_sonra_durum():
    p = parse("furkan durum")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_durum_sonra_isim():
    p = parse("durum furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_isim_sonra_hesap():
    p = parse("furkan hesap")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_hesap_sonra_isim():
    p = parse("hesap furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_isim_sonra_cari():
    p = parse("furkan cari")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_cari_sonra_isim():
    p = parse("cari furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_isim_sonra_cariye():
    p = parse("furkan cariye")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_cariye_sonra_isim():
    p = parse("cariye furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_isim_sonra_alacak():
    p = parse("furkan alacak")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_alacak_sonra_isim():
    p = parse("alacak furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_isim_sonra_alacagi():
    p = parse("furkan alacağı")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_alacagi_sonra_isim():
    p = parse("alacağı furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_toplam_borc_isimden_sonra():
    p = parse("furkan toplam borç")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_toplam_borc_isimden_once():
    p = parse("toplam borç furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_guncel_bakiye_isimden_sonra():
    p = parse("furkan güncel bakiye")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_guncel_bakiye_isimden_once():
    p = parse("güncel bakiye furkan")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_bakiye_bare_soyadli_isim():
    p = parse("furkan duman bakiye")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan duman"


# Güvenlik freni: bare anahtar kelimenin HER İKİ tarafında da (dolgu hariç)
# kelime kalırsa net bir "isim + anahtar" kalıbı değildir, uydurulmaz.
def test_bakiye_bare_iki_taraf_da_doluysa_none():
    # Regresyon: "durum" artık bare bir bakiye anahtar kelimesi ama bu tümce
    # alakasız bir serbest cümle — mevcut davranış (None, LLM'e bırak)
    # korunmalı (bkz. test_rapor_belirsiz_durum_raporu_regex_pes_eder).
    p = parse("bana bir durum raporu hazırla")
    assert p is None


def test_bakiye_bare_borc_fiiliyle_beraberse_denenmez():
    # "borç" hem bare bakiye anahtarı hem tutar işaretçisi/kind sinyali —
    # gerçek bir borç cümlesinde (fiil + tutar var) bakiye sorgusuna
    # dönüşmemeli, normal debt akışı çalışmalı.
    p = parse("ahmet 20 balya saman aldı 15000 tl borç")
    assert p.kind == "debt"


def test_bakiye_bare_tutarli_borc_denenmez():
    # Fiil yok ama tutar var: "furkan 5000 borç" yine bir borç cümlesidir,
    # bakiye sorgusu değil.
    p = parse("furkan 5000 borç")
    assert p.kind == "debt"
    assert p.amount == Decimal("5000")


def test_bakiye_bare_isim_sonra_borclu():
    p = parse("ahmet ne kadar borçlu")
    assert p.kind == "balance_query"
    assert p.person_name == "ahmet"


def test_bakiye_bare_ne_kadar():
    # Hiçbir bakiye anahtar kelimesi yok, sadece sondan "ne kadar" —
    # yine de bakiye sorgusu sayılmalı (bkz. _try_bare_ne_kadar_query).
    p = parse("ahmet ne kadar")
    assert p.kind == "balance_query"
    assert p.person_name == "ahmet"


def test_bakiye_bare_ne_kadar_soyadli():
    p = parse("furkan duman ne kadar")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan duman"


def test_bakiye_bare_ne_kadar_borc_fiiliyle_beraberse_denenmez():
    # Sondan "ne kadar" gelmiyor (araya borç fiili giriyor) — bu güvenli
    # bir bakiye kalıbı değil, uydurulmaz.
    p = parse("ahmet ne kadar borç verdim")
    assert p.kind != "balance_query"


# --------------------------------------------------------------- Grup 1, madde 4: fiilsiz
# liste sorguları ("kişiler", "tüm kişiler", "kişileri say", "sistemdeki
# kişiler", "kimler var") ve fiilsiz ilçe sorgusu ("bergamalılar").


def test_sorgu_bare_kisiler():
    p = parse("kişiler")
    assert p.kind == "list_all"


def test_sorgu_bare_tum_kisiler():
    p = parse("tüm kişiler")
    assert p.kind == "list_all"


def test_sorgu_bare_kisileri_say():
    p = parse("kişileri say")
    assert p.kind == "list_all"


def test_sorgu_bare_sistemdeki_kisiler():
    p = parse("sistemdeki kişiler")
    assert p.kind == "list_all"


def test_sorgu_bare_kimler_var():
    p = parse("kimler var")
    assert p.kind == "list_all"


def test_sorgu_bare_bergamalilar():
    p = parse("bergamalılar")
    assert p.kind == "list_district"
    assert p.district == "bergama"


def test_sorgu_bare_bergamadakiler():
    p = parse("bergamadakiler")
    assert p.kind == "list_district"
    assert p.district == "bergama"


def test_sorgu_bare_bergamadaki():
    p = parse("bergamadaki")
    assert p.kind == "list_district"
    assert p.district == "bergama"


def test_sorgu_bergama_daki_listele():
    p = parse("bergamadaki listele")
    assert p.kind == "list_district"
    assert p.district == "bergama"


def test_sorgu_bare_borclular_district_sanilmaz():
    # "borçlular" da "-lar" ile bitiyor ama bu bilinen bir liste kelimesi,
    # ilçe eki SANILMAMALI (district="borç" gibi anlamsız bir sonuç
    # üretmemeli) — list_debtors olmalı (bkz. _try_bare_list_query).
    p = parse("borçlular")
    assert p.kind == "list_debtors"


def test_sorgu_bare_kim_borclu():
    p = parse("kim borçlu")
    assert p.kind == "list_debtors"


def test_sorgu_bare_borclu_olanlar():
    p = parse("borçlu olanlar")
    assert p.kind == "list_debtors"


def test_sorgu_bare_kim_alacakli():
    p = parse("kim alacaklı")
    assert p.kind == "list_creditors"


# --------------------------------------------------------------- Grup 1, madde 5: tek kelime
# = arama (komut/fiil yoksa). "{isim} bakiye" gibi komutlu ifadelerden
# FARKLI olarak burada TEK kelime ve hiçbir anahtar/fiil yok.


def test_arama_tek_isim():
    p = parse("ahmet")
    assert p.kind == "search"
    assert p.query == "ahmet"


def test_arama_tek_soyad():
    p = parse("duman")
    assert p.kind == "search"
    assert p.query == "duman"


def test_arama_ilce_adi_eksiz():
    p = parse("bergama")
    assert p.kind == "search"
    assert p.query == "bergama"


def test_arama_ile_komutlu_ifade_farkli():
    # "ahmet bakiye" iki kelime VE komutlu (bare "bakiye" anahtarı) —
    # arama DEĞİL, doğrudan bakiye sorgusu olmalı.
    p = parse("ahmet bakiye")
    assert p.kind == "balance_query"
    assert p.query is None


def test_arama_tek_kelime_fiil_ise_arama_sayilmaz():
    # "aldı" tek başına bir fiil/komut — arama moduna düşmemeli (madde 5:
    # "hiçbir komut/fiil yoksa"). Kayıt için de yetersiz (isim yok), None.
    p = parse("aldı")
    assert p is None


def test_arama_tek_kelime_rapor_ise_arama_sayilmaz():
    # "rapor" bilinen bir komut (report_menu), arama değil.
    p = parse("rapor")
    assert p.kind == "report_menu"


def test_arama_tek_sayi_arama_sayilmaz():
    p = parse("500")
    assert p is None


# --------------------------------------------------------------- Grup 2 (CLAUDE.md > "Bot
# kayıt akışı"), madde 2: kısa kayıt biçimi — fiil YOKSA ama {isim} {adet}
# {ürün} {tutar} yapısı net ise borç varsayılır.


def test_kisa_kayit_bicimi_fiilsiz_borc():
    p = parse("ahmet 30 saman 5000tl")
    assert p.kind == "debt"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("30")
    assert p.unit is None
    assert p.product == "saman"
    assert p.amount == Decimal("5000")


def test_kisa_kayit_bicimi_birimli():
    p = parse("ahmet 30 balya saman 5000 tl")
    assert p.kind == "debt"
    assert p.qty == Decimal("30")
    assert p.unit == "balya"
    assert p.product == "saman"
    assert p.amount == Decimal("5000")


def test_kisa_kayit_bicimi_soyadli_isim():
    p = parse("furkan duman 20 kg arpa 2000tl")
    assert p.kind == "debt"
    assert p.person_name == "furkan duman"
    assert p.product == "arpa"


def test_kisa_kayit_bicimi_urun_eksikse_belirsiz_llme_birak():
    # Yapı net değil: isim + tutar var ama adet/ürün yok — uydurmadan pes et.
    p = parse("ahmet 5000tl")
    assert p is None


def test_kisa_kayit_bicimi_adet_eksikse_belirsiz_llme_birak():
    p = parse("ahmet saman 5000tl")
    assert p is None


def test_kisa_kayit_bicimi_fiil_varsa_bu_yoldan_gitmez():
    # Fiil zaten var, normal debt akışı çalışır (regresyon değil, sadece
    # kısa-kayıt fonksiyonunun devreye girmediğini doğrular).
    p = parse("ahmet 30 balya saman aldı 5000 tl borç")
    assert p.kind == "debt"
    assert p.qty == Decimal("30")


# --------------------------------------------------------------- Grup 2, madde 4: yeni kişi
# OLUŞTURMA türevleri — SADECE kişi ekleme, borç YOK.


def test_create_person_adinda_yeni_kisi_olustur():
    p = parse("ahmet adında yeni kişi oluştur")
    assert p.kind == "create_person"
    assert p.person_name == "ahmet"
    assert p.amount is None


def test_create_person_adinda_kisi_kayit_et():
    p = parse("ahmet adında kişi kayıt et")
    assert p.kind == "create_person"
    assert p.person_name == "ahmet"


def test_create_person_soyadli_kayit_et():
    p = parse("ahmet duman kayıt et")
    assert p.kind == "create_person"
    assert p.person_name == "ahmet duman"


def test_create_person_olustur():
    p = parse("ahmet yıldırım oluştur")
    assert p.kind == "create_person"
    assert p.person_name == "ahmet yıldırım"


def test_create_person_yeni_kisi():
    p = parse("ahmet yıldırım yeni kişi")
    assert p.kind == "create_person"
    assert p.person_name == "ahmet yıldırım"


def test_create_person_yeni_isim():
    p = parse("ahmet yıldırım yeni isim")
    assert p.kind == "create_person"
    assert p.person_name == "ahmet yıldırım"


def test_create_person_borc_ile_karismaz():
    # Normal bir borç cümlesi create_person'a yanlışlıkla düşmemeli.
    p = parse("ahmet 20 balya saman aldı 15000 tl borç")
    assert p.kind == "debt"


# --- İsim TEMİZLEME: komut kelimeleri isme karışmamalı (2026-08-30 bug).
# Parser create_person'ı yakalıyordu ama "furkan duman adlı kişiyi sisteme"
# gibi komut kelimeleriyle dolu bir "isim" üretiyordu — o isimle kişi
# eşleştirmesi hiçbir zaman tutmaz.


@pytest.mark.parametrize(
    "cumle,beklenen",
    [
        ("furkan duman adlı kişiyi sisteme kayıt et", "furkan duman"),
        ("furkan duman adında kişiyi deftere kaydet", "furkan duman"),
        ("furkan duman isimli kişiyi listeye ekle", "furkan duman"),
        ("furkan duman isminde bir kişi oluştur", "furkan duman"),
        ("serpil çiçek kişisini kayıt et", "serpil çiçek"),
        ("serpil çiçek kişiyi ekle", "serpil çiçek"),
        ("serpil çiçek sistemine kaydet", "serpil çiçek"),
        ("serpil çiçek kayıtlara ekle", "serpil çiçek"),
        ("yeni kişi yıldız tilbe", "yıldız tilbe"),
        ("yeni isim yıldız tilbe", "yıldız tilbe"),
        ("yıldız tilbe defterime ekle", "yıldız tilbe"),
        ("yıldız tilbe sisteme gir", "yıldız tilbe"),
        ("yıldız tilbe aç", "yıldız tilbe"),
    ],
)
def test_create_person_isim_komut_kelimelerinden_temizlenir(cumle, beklenen):
    p = parse(cumle)
    assert p is not None, f"{cumle!r} yakalanmadı"
    assert p.kind == "create_person"
    assert p.person_name == beklenen


# --- Yeni TETİKLEYİCİLER: eskiden None dönüp LLM'e giden kalıplar.


@pytest.mark.parametrize(
    "cumle,beklenen",
    [
        ("ercüment çözer kişisini ekle", "ercüment çözer"),
        ("faruk caner sisteme ekle", "faruk caner"),
        ("faruk caner deftere ekle", "faruk caner"),
        ("ali veli ekle", "ali veli"),
        ("ahmet duman kaydet", "ahmet duman"),
        ("ahmet duman sisteme kaydet", "ahmet duman"),
    ],
)
def test_create_person_yeni_tetikleyiciler(cumle, beklenen):
    p = parse(cumle)
    assert p is not None, f"{cumle!r} hâlâ yakalanmıyor (LLM'e düşüyor)"
    assert p.kind == "create_person"
    assert p.person_name == beklenen
    assert p.amount is None


# --- GERÇEK İSİM KORUMASI: aşırı temizleme yapılmamalı, para cümleleri
# create_person'a düşmemeli.


@pytest.mark.parametrize("cumle", ["ahmet yılmaz", "mehmet kaya"])
def test_create_person_duz_isim_tetiklemez(cumle):
    # Komut kelimesi olmayan düz bir isim create_person DEĞİLDİR (tek
    # kelime olmadığı için arama da değil) — parser çözemez, LLM'e kalır.
    assert parse(cumle) is None


def test_create_person_ortadaki_kelime_korunur():
    # Aşırı temizleme koruması: dolgu kelimeleri yalnızca isim öbeğinin
    # BAŞINDAN ve SONUNDAN ayıklanır; ortadaki (gerçek ad-soyad olabilecek)
    # kelimeye dokunulmaz.
    p = parse("ali kişi duman ekle")
    assert p.kind == "create_person"
    assert p.person_name == "ali kişi duman"


def test_create_person_tek_basina_komut_kelimesi_isim_degil():
    # "ekle" tek başına bir isim değildir — isim boş kalırsa niyet üretilmez.
    assert parse("ekle") is None
    assert parse("sisteme ekle") is None


@pytest.mark.parametrize(
    "cumle",
    [
        "ahmete 20 balya saman ekle 5000 tl",
        "ahmet 20 balya saman aldı 15000 tl borç",
        "mehmet 2000 lira ödedi",
        "ahmete 3000 verdim",
    ],
)
def test_create_person_para_mal_cumlesini_calmaz(cumle):
    # Para/mal bağlamı olan bir cümle "ekle" gibi bir eylem kelimesi taşısa
    # bile ASLA create_person olmaz — kayıt niyeti korunur.
    p = parse(cumle)
    assert p is None or p.kind != "create_person"


def test_create_person_bakiye_ve_liste_komutlarini_bozmaz():
    assert parse("furkan bakiye").kind == "balance_query"
    assert parse("bergamalıları listele").kind == "list_district"
    assert parse("kişileri listele").kind == "list_all"


def test_create_person_silme_komutunu_calmaz():
    # "oluştur"/"aç" artık create_person tetikleyicisi ama silme kontrolü
    # önce çalışır: "furkanı sil yeniden oluştur" hâlâ archive_and_recreate.
    assert parse("furkanı sil yeniden oluştur").kind == "archive_and_recreate"
    assert parse("furkanı sıfırla yeniden aç").kind == "archive_and_recreate"


# --------------------------------------------------------------- Grup 3: kişi
# silme/arşivleme (CLAUDE.md > "Bot kişi silme = arşivleme"). HİÇBİR ŞEY
# gerçekten silinmez, bu niyet yalnızca arşivle+pasifleştir akışını tetikler.


def test_archive_person_furkani_sil():
    p = parse("furkanı sil")
    assert p.kind == "archive_person"
    assert p.person_name == "furkanı"


def test_archive_person_furkan_sil_eksiz():
    p = parse("furkan sil")
    assert p.kind == "archive_person"
    assert p.person_name == "furkan"


def test_archive_person_kaldir():
    p = parse("furkanı kaldır")
    assert p.kind == "archive_person"
    assert p.person_name == "furkanı"


def test_archive_person_arsivle():
    p = parse("furkanı arşivle")
    assert p.kind == "archive_person"
    assert p.person_name == "furkanı"


def test_archive_person_sifirla():
    p = parse("furkanı sıfırla")
    assert p.kind == "archive_person"
    assert p.person_name == "furkanı"


def test_archive_and_recreate_sil_yeniden_olustur():
    p = parse("furkanı sil yeniden oluştur")
    assert p.kind == "archive_and_recreate"
    assert p.person_name == "furkanı"


def test_archive_and_recreate_sifirla_yeniden_ac():
    p = parse("furkanı sıfırla yeniden aç")
    assert p.kind == "archive_and_recreate"
    assert p.person_name == "furkanı"


def test_archive_person_soyadli_isim_korunur():
    p = parse("furkan duman sil")
    assert p.kind == "archive_person"
    assert p.person_name == "furkan duman"


def test_archive_person_olustur_kelimesi_tek_basina_yeniden_olmadan_recreate_saymaz():
    # "yeniden" yoksa "oluştur" kelimesi tek başına recreate tetiklemez —
    # zaten create_person'ın kendi kelimeleriyle çakışmaması için bu ayrım
    # kritik (bkz. modül üstü ARCHIVE_* yorumu).
    p = parse("furkanı sil")
    assert p.kind == "archive_person"


# --------------------------------------------------------------- Grup 4: kişi
# düzenleme (CLAUDE.md > "Silme mesajı + kişi düzenleme"). NET komutlar alan+
# değeri doğrudan taşır; BELİRSİZ komutlar bot'un alan menüsü sormasını tetikler.


def test_edit_person_net_ilce_yap():
    p = parse("mehmet ilçe ahmetbeyler yap")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"
    assert p.field == "district"
    assert p.new_value == "ahmetbeyler"


def test_edit_person_net_ismi_iyelik_ekiyle():
    # "mehmetin" ham bırakılır (ek soyma merkezi olarak intent_resolver'da).
    p = parse("mehmetin ismi akif yap")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmetin"
    assert p.field == "full_name"
    assert p.new_value == "akif"


def test_edit_person_net_isim_ekssiz():
    p = parse("mehmet isim akif yap")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"
    assert p.field == "full_name"
    assert p.new_value == "akif"


def test_edit_person_net_telefon_yap():
    p = parse("mehmet telefon 5551234567 yap")
    assert p.kind == "edit_person"
    assert p.field == "phone"
    assert p.new_value == "5551234567"


def test_edit_person_net_il_yap():
    p = parse("mehmet il izmir yap")
    assert p.kind == "edit_person"
    assert p.field == "city"
    assert p.new_value == "izmir"


def test_edit_person_net_ad_soyad_iki_kelimelik_alan():
    p = parse("mehmet ad soyad akif yildiz yap")
    assert p.kind == "edit_person"
    assert p.field == "full_name"
    assert p.new_value == "akif yildiz"


def test_edit_person_net_atama_fiili_yoksa_none():
    # "yap" olmadan NET kalıp tetiklenmemeli — belirsiz akışa da düşmez
    # çünkü "ilçe" bir düzenleme tetikleyicisi (düzenle/değiştir) değil.
    p = parse("mehmet ilçe ahmetbeyler")
    assert p is None


def test_edit_person_menu_duzenle():
    p = parse("mehmet düzenle")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"
    assert p.field is None
    assert p.new_value is None


def test_edit_person_menu_adli_kisiyi_duzenle():
    p = parse("mehmet adlı kişiyi düzenle")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"
    assert p.field is None


def test_edit_person_menu_isim_degistir():
    p = parse("mehmet isim değiştir")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"
    assert p.field is None


def test_edit_person_menu_telefon_duzenle_yine_tam_menu():
    # Alan kelimesi ("telefon") geçse bile değer verilmediği için CLAUDE.md
    # gereği yine TAM menü sorulur, doğrudan "yeni telefon?" sorulmaz.
    p = parse("mehmet telefon düzenle")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"
    assert p.field is None


def test_edit_person_menu_isim_degisiklik():
    p = parse("mehmet isim değişiklik")
    assert p.kind == "edit_person"
    assert p.field is None


def test_edit_person_menu_yazim_hatasi_duzenlee():
    p = parse("mehmet düzenlee")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"


def test_edit_person_menu_yazim_hatasi_dzenle():
    p = parse("mehmet dzenle")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"


def test_edit_person_menu_yazim_hatasi_transpozisyon_degistir():
    p = parse("mehmet dğeiştir")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"


def test_edit_person_menu_yazim_hatasi_transpozisyon_degisiklik():
    p = parse("mehmet dğeişiklik")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet"


def test_edit_person_menu_soyadli_isim_korunur():
    p = parse("mehmet yılmaz düzenle")
    assert p.kind == "edit_person"
    assert p.person_name == "mehmet yılmaz"


def test_edit_person_borc_cumlesiyle_karismaz():
    p = parse("ahmet 20 balya saman aldı 15000 tl borç")
    assert p.kind == "debt"


def test_edit_person_archive_ile_karismaz():
    p = parse("furkanı sil")
    assert p.kind == "archive_person"


# --------------------------------------------------------------- "sil" bağlam
# ayrımı (2026-08-31): "sil" geçen her cümle kişi silme DEĞİLDİR. Cümlede
# para/mal bağlamı da varsa niyet belirsizdir ve sorulur (delete_ambiguous);
# bağlam yoksa mevcut archive_person davranışı AYNEN korunur.


def test_borcunu_odedi_sil_kisi_silme_sayilmaz():
    p = parse("furkan duman 20 saman borcunu ödedi sil")
    assert p.kind == "delete_ambiguous"


def test_borcunu_odedi_sil_ismi_dogru_ayiklar():
    # Eskiden cümlenin TAMAMI kişi adı sanılıyordu ("furkan duman 20 saman
    # borcunu ödedi") — artık yalnızca baştaki ad-soyad alınır.
    p = parse("furkan duman 20 saman borcunu ödedi sil")
    assert p.person_name == "furkan duman"


def test_tahsilat_fiilli_sil_belirsiz_sayilir():
    p = parse("mehmet 5000 tl ödedi sil")
    assert p.kind == "delete_ambiguous"
    assert p.person_name == "mehmet"


def test_borc_fiilli_sil_de_belirsiz_sayilir():
    p = parse("ahmet 20 balya saman aldı 15000 tl borç sil")
    assert p.kind == "delete_ambiguous"
    assert p.person_name == "ahmet"


def test_sadece_isim_ve_sil_hala_archive():
    # Mevcut davranış bozulmamalı: para/mal bağlamı YOKSA doğrudan silme.
    p = parse("furkanı sil")
    assert p.kind == "archive_person"
    assert p.person_name == "furkanı"


def test_soyadli_sil_hala_archive():
    p = parse("furkan duman sil")
    assert p.kind == "archive_person"
    assert p.person_name == "furkan duman"


def test_sil_yeniden_olustur_hala_archive_and_recreate():
    p = parse("furkanı sil yeniden oluştur")
    assert p.kind == "archive_and_recreate"


def test_hesabini_sil_hala_archive():
    # "hesabını" bir bakiye kelimesi ama para/mal bağlamı değil — bu cümle
    # eskiden olduğu gibi kişi silmedir.
    p = parse("furkanın hesabını sil")
    assert p.kind == "archive_person"


def test_isim_cikarilamayan_sil_cumlesi_llme_birakilir():
    # Baştan bir ad-soyad öbeği yoksa isim UYDURULMAZ: parser pes eder
    # (None), cümle LLM'e devredilir.
    assert parse("20 balya saman 5000 tl ödedi sil") is None


def test_sil_olmayan_tahsilat_cumlesi_etkilenmez():
    p = parse("furkan 5000 ödedi")
    assert p.kind == "payment"
    assert p.person_name == "furkan"
    assert p.amount == Decimal("5000")


def test_strip_delete_words_silme_fiilini_ayiklar():
    from app.services.parser import strip_delete_words

    assert strip_delete_words("furkan duman 20 saman borcunu ödedi sil") == (
        "furkan duman 20 saman borcunu ödedi"
    )


# --------------------------------------------------------------- toplam bakiye
# (2026-08-31): "tüm bakiye"/"toplam borç" bir KİŞİ sorgusu değil, defterin
# tamamının özetidir. Eskiden "tüm"/"total" kişi adı sanılıp "defterde yok"
# deniyordu.


@pytest.mark.parametrize(
    "metin",
    [
        "tüm bakiye",
        "toplam borç",
        "toplam alacak",
        "genel bakiye",
        "sistemdeki toplam borç",
        "total borç",
        "güncel toplam",
        "güncel total borç",
        "toplam borç ne kadar",
        "bütün bakiyeler",
        "toplam",
    ],
)
def test_toplam_bakiye_niyeti(metin):
    p = parse(metin)
    assert p.kind == "total_balance"
    assert p.person_name is None


def test_toplam_kelimesi_kisi_adi_sayilmaz():
    p = parse("tüm bakiye")
    assert p.person_name is None
    assert p.query is None


def test_kisi_adi_varsa_toplam_degil_kisi_bakiyesi():
    # "furkan toplam borç" -> tek kişinin bakiyesi ("toplam" burada dolgu).
    p = parse("furkan toplam borç")
    assert p.kind == "balance_query"
    assert p.person_name == "furkan"


def test_genel_durum_hala_genel_rapor():
    # "genel durum" = genel durum RAPORU (PDF) — toplam bakiye niyeti bunu
    # gölgelememeli.
    assert parse("genel durum").kind == "report_general"


def test_tum_zamanlarin_raporu_hala_genel_rapor():
    assert parse("tüm zamanların raporu").kind == "report_general"


def test_tum_kisileri_listele_hala_liste():
    assert parse("tüm kişileri listele").kind == "list_all"


# --------------------------------------------------------------- 2026-08-31 anlama
# genişletmesi: yazım toleranslı liste komutları, "borçlandı"/"borcunu ödedi",
# "kaç para", "{ilçe}den kimler var", ürün/stok sorgusu.
# CLAUDE.md > "LLM son çare, regex birincil": bu kalıpların hepsi DÜZENLİ,
# regex'in kesin ve anında çözmesi gerekir — LLM'e hiç gitmemeli.


@pytest.mark.parametrize(
    "metin",
    [
        "kişler",
        "ksiler",
        "kişileer",
        "kişilerr",
        "kişleri listele",
        "kişiler listesi",
        "kisiler listesi",
        "kişi listesi",
        "müşteri listesi",
        "tüm kişler",
    ],
)
def test_yazim_hatali_kisi_listesi_komutlari(metin):
    p = parse(metin)
    assert p.kind == "list_all"
    assert p.person_name is None


@pytest.mark.parametrize(
    "metin",
    [
        "ahmet",          # gerçek bir isim — liste komutu SANILMAMALI
        "duman",
        "bergama",
        "kiler",          # kısa gerçek kelime, mesafe 2 ama 6 harften kısa
        "işler",          # ilk harf farklı ("kişiler" ile karıştırılmamalı)
    ],
)
def test_fuzzy_liste_yanlis_pozitif_yapmaz(metin):
    p = parse(metin)
    assert p.kind != "list_all"


def test_borclu_listesi_bakiye_sorgusu_sanilmaz():
    # Eskiden "borçlu" bir bare bakiye anahtar kelimesi olduğu için
    # "borçlu listesi" -> balance_query(person="listesi") gibi anlamsız bir
    # sonuç veriyordu.
    p = parse("borçlu listesi")
    assert p.kind == "list_debtors"


def test_alacakli_listesi():
    assert parse("alacaklı listesi").kind == "list_creditors"


# --------------------------------------------------------------- "borçlandı" = borç


def test_borclandi_tutarla_borc_kaydi():
    p = parse("ali 1000 borçlandı")
    assert p.kind == "debt"
    assert p.person_name == "ali"
    assert p.amount == Decimal("1000")


def test_borclandi_urunlu():
    p = parse("ahmet 20 balya saman borçlandı 5000 tl")
    assert p.kind == "debt"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("20")
    assert p.unit == "balya"
    assert p.product == "saman"
    assert p.amount == Decimal("5000")


# --------------------------------------------------------------- borç kapanışı


def test_borcunu_odedi_tahsilat():
    p = parse("ali borcunu ödedi")
    assert p.kind == "payment"
    assert p.person_name == "ali"
    assert p.amount is None
    # Tutar söylenmemiş: uydurulmaz, güncel bakiye teklif edilip onaylatılır.
    assert p.close_debt is True


def test_borcunu_kapatti_tahsilat():
    p = parse("mehmet borcunu kapattı")
    assert p.kind == "payment"
    assert p.person_name == "mehmet"
    assert p.close_debt is True


def test_urunlu_borcunu_odedi():
    p = parse("ahmet 20 saman borcunu ödedi")
    assert p.kind == "payment"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("20")
    assert p.product == "saman"
    assert p.close_debt is True


def test_borcunu_odedi_tutar_soylenmisse_close_debt_kapali():
    p = parse("ahmet borcunu 5000 tl ödedi")
    assert p.kind == "payment"
    assert p.amount == Decimal("5000")
    assert p.close_debt is False


def test_borc_kelimesi_borc_fiiliyle_hala_celisir():
    # Borç kapanışı YALNIZCA tahsilat fiiliyle geçerlidir; borç fiiliyle
    # birlikte yön çelişir, uydurulmaz (LLM'e bırakılır).
    assert parse("ahmet borcunu aldı 500 tl") is None


def test_silme_fiili_hala_belirsiz_kalir():
    # "sil" + para/mal bağlamı: borç kapanışı bu ayrımı EZMEMELİ, soru
    # sorulmalı (CLAUDE.md > "'sil' bağlam ayrımı").
    p = parse("furkan duman 20 saman borcunu ödedi sil")
    assert p.kind == "delete_ambiguous"
    assert p.person_name == "furkan duman"


# --------------------------------------------------------------- "kaç para"


@pytest.mark.parametrize(
    "metin,beklenen_isim",
    [
        ("mehmet kaç para", "mehmet"),
        ("furkan duman kaç para", "furkan duman"),
        ("ahmet kaç lira", "ahmet"),
        ("ahmet ne kadar", "ahmet"),
    ],
)
def test_kac_para_bakiye_sorgusu(metin, beklenen_isim):
    p = parse(metin)
    assert p.kind == "balance_query"
    assert p.person_name == beklenen_isim


def test_kac_para_kayit_cumlesini_bozmaz():
    # Bir borç fiili varsa bakiye sorgusu SANILMAMALI.
    p = parse("mehmete kaç para verdim")
    assert p.kind != "balance_query"


# --------------------------------------------------------------- ilçeden kimler var


@pytest.mark.parametrize(
    "metin",
    [
        "bergamadan kimler var",
        "bergamadaki kimler",
        "bergamada kim var",
        "bergamadan kimler",
    ],
)
def test_ilceden_kimler_var(metin):
    p = parse(metin)
    assert p.kind == "list_district"
    assert p.district == "bergama"


def test_ilce_eki_ler_ile_biten_ilce_bozulmaz():
    # "Ahmetbeyler" ilçe adının KENDİSİ "-ler" ile bitiyor, eki sökülmemeli.
    p = parse("ahmetbeylerden kimler var")
    assert p.kind == "list_district"
    assert p.district == "ahmetbeyler"


def test_kimler_var_tek_basina_hala_list_all():
    # İlçe kelimesi olmadan "kimler var" herkesi listeler, ilçe sorgusu değil.
    assert parse("kimler var").kind == "list_all"


# --------------------------------------------------------------- ürün/stok sorgusu
# Grup C: defter stok/fiyat TUTMAZ. Niyet yine de tanınır ki bot "bu özellik
# henüz yok" desin — tanınmasa cümle bir kişi adı ya da bir kayıt sanılırdı.


@pytest.mark.parametrize(
    "metin,beklenen_urun",
    [
        ("toplam kaç saman satıldı", "saman"),
        ("ne kadar arpa var", "arpa"),
        ("saman fiyatı", "saman"),
        ("arpa stoğu", "arpa"),
        ("kaç balya saman satıldı", "saman"),
    ],
)
def test_urun_sorgusu_taninir(metin, beklenen_urun):
    p = parse(metin)
    assert p.kind == "product_query"
    assert p.product == beklenen_urun
    assert p.person_name is None


@pytest.mark.parametrize(
    "metin",
    [
        "ahmete toplam 5 balya saman sattım 1000 tl",  # gerçek borç kaydı
        "ahmet 20 balya saman aldı 1500 tl",
        "ne kadar borcu var",                          # bakiye sorusu
        "furkan toplam borç",
    ],
)
def test_urun_sorgusu_kayitlari_ve_bakiyeyi_bozmaz(metin):
    p = parse(metin)
    assert p is None or p.kind != "product_query"


# --------------------------------------------------------------- koşan format
#
# CLAUDE.md > "Koşan format": "70-20-50" = 70 vardı, 20 değişti, 50 oldu.
# Yön ilk-son karşılaştırmasından, kaydedilen sayı üçlünün FARKINDAN çıkar.
# Form tarafının ikizi: web/src/lib/running.test.ts.


def test_kosan_azalis_borc():
    p = parse("ahmet 70-20-50 saman")
    assert p.kind == "debt"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("20")
    assert p.product == "saman"
    assert p.running is True
    assert (p.running_before, p.running_change, p.running_after) == (
        Decimal("70"), Decimal("20"), Decimal("50"),
    )
    assert p.amount is None  # TL ayrı girilir, uydurulmaz


def test_kosan_artis_tahsilat():
    p = parse("ahmet 70-30-100 saman tahsilat")
    assert p.kind == "payment"
    assert p.person_name == "ahmet"
    assert p.qty == Decimal("30")
    assert p.product == "saman"


def test_kosan_buyuk_sayilar():
    p = parse("furkan duman 1000-285-715 saman")
    assert p.kind == "debt"
    assert p.person_name == "furkan duman"
    assert p.qty == Decimal("285")


@pytest.mark.parametrize(
    "metin",
    [
        "ahmet 70-20-50 saman",
        "ahmet 70+20+50 saman",
        "ahmet 70*20*50 saman",
        "ahmet 70 - 20 - 50 saman",
        "ahmet 70 +20+ 50 saman",
    ],
)
def test_kosan_ayrac_salt_gorsel(metin):
    """Ayraç matematik işareti DEĞİL: üçü de aynı işlemi anlatır."""
    p = parse(metin)
    assert p.kind == "debt"
    assert p.qty == Decimal("20")
    assert p.person_name == "ahmet"


def test_kosan_matematik_tutmuyorsa_kaydedilmez_sorulur():
    p = parse("ahmet 70-25-50 saman")
    assert p.kind == "running_mismatch"  # borç/tahsilat DEĞİL: soru
    assert p.qty == Decimal("20")  # önerilen doğru fark
    assert p.running_change == Decimal("25")  # kullanıcının yazdığı


def test_kosan_duzeltme_orta_sayiyi_farka_esitler():
    assert parser.correct_running_text("Ahmet 70-25-50 saman") == "Ahmet 70-20-50 saman"
    # Ayraç varyantı da tek biçime toplanır, cümlenin geri kalanı korunur.
    assert parser.correct_running_text("Ahmet 70 + 25 + 50 saman 5000 tl") == (
        "Ahmet 70-20-50 saman 5000 tl"
    )


def test_kosan_urun_soylenmezse_varsayilan_saman():
    p = parse("ahmet 70-20-50")
    assert p.kind == "debt"
    assert p.product == "saman"


def test_kosan_urun_ve_birim_soylenirse_kullanilir():
    p = parse("ahmet 70-20-50 balya arpa")
    assert p.kind == "debt"
    assert p.unit == "balya"
    assert p.product == "arpa"


def test_kosan_tutar_ayni_cumlede_verilebilir():
    p = parse("ahmet 70-20-50 saman 5000 tl")
    assert p.kind == "debt"
    assert p.qty == Decimal("20")
    assert p.amount == Decimal("5000")
    assert p.person_name == "ahmet"


def test_kosan_baglam_kelimesi_ismi_bozmaz():
    p = parse("ahmet duman 70-20-50 saman aldı")
    assert p.kind == "debt"
    assert p.person_name == "ahmet duman"
    assert p.product == "saman"


@pytest.mark.parametrize(
    "metin",
    [
        "12-05-2026",              # tarih
        "2026-05-12",              # tarih (ters)
        "ahmet 12-05-2026 saman",  # tarih, isimle birlikte de olsa
        "0532-456-789",            # baştaki sıfır: adet olamaz
        "0532-456-78-90",          # dört parça: telefon
        "70-20-50",                # kime yazılacağı söylenmemiş
        "ahmet 70-0-70 saman",     # hareket yok
    ],
)
def test_kosan_yanlis_tetiklenmez(metin):
    p = parse(metin)
    assert p is None or p.kind not in ("debt", "payment", "running_mismatch")


def test_kosan_ciplak_uclu_arama_da_sayilmaz():
    """Eskiden "70-20-50" bir kişi ARAMASINA dönüşüyordu (yanlış)."""
    assert parse("70-20-50") is None
    assert parse("12-05-2026") is None


@pytest.mark.parametrize(
    "metin",
    [
        "ahmet 500 tl borç",
        "ahmet 20 saman aldı",
        "ahmet 20 balya saman aldı 15000 tl borç",
        "ahmet 30 saman 5000tl",
    ],
)
def test_kosan_normal_kayitlari_bozmaz(metin):
    p = parse(metin)
    assert p.kind in ("debt", "payment")
    assert p.running is False


def test_kosan_net_komutlar_hala_kazanir():
    """Silme/düzenleme gibi net komutlar koşan formattan ÖNCE gelir."""
    assert parse("ahmeti sil").kind == "archive_person"
    assert parse("ahmet bakiye").kind == "balance_query"
    assert parse("kişileri listele").kind == "list_all"


def test_kosan_parse_running_dogrudan():
    r = parser.parse_running("70-20-50")
    assert (r.before, r.change, r.after) == (Decimal("70"), Decimal("20"), Decimal("50"))
    assert r.qty == Decimal("20") and r.kind == "debt" and r.consistent is True
    # Tarihe benzeyen ama matematiği TUTAN üçlü geçerlidir.
    assert parser.parse_running("10-5-5").qty == Decimal("5")
    # İki üçlü varsa hangisi olduğu belirsiz: hiçbiri.
    assert parser.parse_running("70-20-50 ve 30-10-20") is None
