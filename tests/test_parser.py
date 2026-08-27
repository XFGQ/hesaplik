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
