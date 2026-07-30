"""Tek mesajda birden çok işlem — bölme testleri (CLAUDE.md > "Tek mesajda
birden çok istek", Grup 5). Yanlış bölmektense tek bırak ilkesi kritik:
normal tek cümleler ASLA yanlışlıkla bölünmemeli."""

from app.services.message_splitter import split_into_requests


# --------------------------------------------------------------- görev tanımındaki örnekler


def test_ayracsiz_iki_islem_bolunur():
    result = split_into_requests("mehmetten 5000 aldım aliye 500 mal gitti")
    assert result == ["mehmetten 5000 aldım", "aliye 500 mal gitti"]


def test_satir_sonu_ile_iki_islem_bolunur():
    result = split_into_requests("mehmet 20 saman aldı 1000 borç\nali 500 ödedi")
    assert result == ["mehmet 20 saman aldı 1000 borç", "ali 500 ödedi"]


def test_bakiye_sorgusu_bolunmez():
    assert split_into_requests("furkan bakiye") == ["furkan bakiye"]


def test_normal_tek_kayit_yanlislikla_bolunmez():
    text = "ahmet 20 balya saman aldı 15000 borç"
    assert split_into_requests(text) == [text]


def test_belirsiz_yarim_cumle_tek_birakilir():
    assert split_into_requests("borç") == ["borç"]


def test_bos_metin_tek_elemanli_doner():
    assert split_into_requests("") == [""]


def test_alakasiz_cumle_bolunmez():
    text = "bugün hava çok güzel"
    assert split_into_requests(text) == [text]


# --------------------------------------------------------------- " ve " bağlacı


def test_ve_baglaciyla_net_islemler_bolunur():
    result = split_into_requests("ahmet 500 tl ödedi ve mehmet 300 tl borç yazdım")
    assert result == ["ahmet 500 tl ödedi", "mehmet 300 tl borç yazdım"]


def test_isim_icinde_ve_gecen_kelime_bolunmez():
    # "veli" kelimesi "ve" alt dizesini içerir ama ayrı bir kelime değil —
    # \bve\b (kelime sınırı) bunu yanlışlıkla ayırmamalı.
    text = "ahmet veli 500 tl borç yazdım"
    assert split_into_requests(text) == [text]


def test_ve_ile_ayrilan_parcalardan_biri_anlamsizsa_bolunmez():
    # İkinci parça ("falan filan") hiçbir şeye parse olmuyor — tüm metin
    # tek işlem sayılmalı (şüphede tek bırak).
    text = "ahmet 500 tl ödedi ve falan filan"
    assert split_into_requests(text) == [text]


# --------------------------------------------------------------- ayraçsız art arda kalıp — ek testler


def test_ayracsiz_iki_borc_kaydi_bolunur():
    result = split_into_requests("ahmet 500 verdim mehmet 300 aldı")
    assert result == ["ahmet 500 verdim", "mehmet 300 aldı"]


def test_tek_sayi_iceren_normal_cumle_bolunmez():
    # İki sayı içeren (adet + tutar) TEK bir kayıt cümlesi yanlışlıkla iki
    # işlenmiş parçaya bölünmemeli — bu, en riskli yanlış-bölme senaryosu.
    text = "ahmet yılmaz 20 balya saman aldı 15000 tl borç"
    assert split_into_requests(text) == [text]


def test_kisa_kayit_bicimi_bolunmez():
    text = "ahmet 30 saman 5000tl"
    assert split_into_requests(text) == [text]


def test_urun_kalemli_borc_cumlesi_bolunmez():
    text = "ahmet yılmaz 20 balya saman aldı 1500 lira borç"
    assert split_into_requests(text) == [text]


def test_kisa_iki_kelimelik_metin_pattern_denenmez():
    # 4 tokendan az olan hiçbir metin ayraçsız bölme için denenmemeli.
    assert split_into_requests("ahmet borcu") == ["ahmet borcu"]


def test_soru_cumlesi_bolunmez():
    text = "ahmet yılmaz borcunu söyle"
    assert split_into_requests(text) == [text]


# --------------------------------------------------------------- daha karmaşık gerçekçi senaryolar


def test_uc_satirla_uc_isleme_bolunur():
    text = "ahmet 1000 tl borç yazdım\nmehmet 500 tl ödedi\nali 200 tl borç yazdım"
    result = split_into_requests(text)
    assert result == [
        "ahmet 1000 tl borç yazdım",
        "mehmet 500 tl ödedi",
        "ali 200 tl borç yazdım",
    ]


def test_satirlardan_biri_anlamsizsa_yine_de_bolunur():
    # Satır sonu KOŞULSUZ bölünür (kullanıcının kendi Enter'ı, belirsizlik
    # yok) — bir satır anlaşılmasa bile (parse None) DİĞER satır işlenmeli
    # (CLAUDE.md > "Tek mesajda birden çok istek": "biri anlaşılmadı diye
    # hepsi durmasın"). Bu, " ve "/ayraçsız kalıptan (nereden böleceğimizi
    # tahmin ettiğimiz, bu yüzden daha temkinli davranılan) FARKLI bir durum.
    text = "ahmet 1000 tl borç yazdım\nasdkjaslkdj alakasız satır"
    result = split_into_requests(text)
    assert result == ["ahmet 1000 tl borç yazdım", "asdkjaslkdj alakasız satır"]


def test_bos_satirlar_atlanir():
    text = "ahmet 1000 tl borç yazdım\n\nmehmet 500 tl ödedi\n"
    result = split_into_requests(text)
    assert result == ["ahmet 1000 tl borç yazdım", "mehmet 500 tl ödedi"]


def test_liste_sorgulari_da_bolunebilir():
    result = split_into_requests("kişileri listele\nborçluları listele")
    assert result == ["kişileri listele", "borçluları listele"]
