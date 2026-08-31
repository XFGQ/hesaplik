"""Sistem prompt'undaki few-shot örneklerinin testleri.

Gerçek LLM'e ASLA bağlanılmaz — burada denetlenen şey modelin çıktısı
değil, PROMPT'un kendisi: örnekler geçerli JSON mu, şemaya uyuyor mu ve
bize öğrettiğimiz yönü (debt/payment) gerçekten söylüyor mu. Örnek yanlışsa
model de yanlış öğrenir; özellikle YÖN hatası parayı ters yazar
(CLAUDE.md > "LLM son çare, regex birincil": para yönü kritik).

Modelin bu prompt'la GERÇEKTEN doğru yön verdiği ayrıca
scripts/llm_manual_check.py ile elle (canlı Ollama'ya karşı) doğrulanır.
"""

import json
import re

import pytest

from app.services.llm_prompt import SYSTEM_PROMPT
from app.services.llm_provider import parsed_intent_from_json

# Prompt'taki örnekler:  "cümle" ->\n{json}
ORNEK_DESENI = re.compile(r'^"(?P<cumle>[^"]+)" ->\n(?P<json>\{.*\})$', re.MULTILINE)

SEMA_ALANLARI = {
    "kind", "person_name", "qty", "unit", "product", "amount",
    "district", "islem", "tur", "kisi",
}


def _ornekler() -> dict[str, dict]:
    ornekler = {m.group("cumle"): json.loads(m.group("json")) for m in ORNEK_DESENI.finditer(SYSTEM_PROMPT)}
    assert ornekler, "prompt'tan hiç örnek çıkarılamadı — desen mi bozuldu?"
    return ornekler


ORNEKLER = _ornekler()


# --------------------------------------------------------------- YÖN (debt/payment)
# CLAUDE.md: "sattım/verdim" = mal ONA gitti = debt; "aldım/ödedi" = para
# BANA geldi = payment. 3. şahıs "aldı" (o aldı) yine debt.

YON_ORNEKLERI = [
    ("ali veliye 20 balya saman sattım 3000 lira", "debt"),
    ("mehmete 500 verdim", "debt"),
    ("ahmet 10 çuval yem aldı 1500 borç", "debt"),
    ("mehmetten 5000 aldım", "payment"),
    ("mehmet bugün 2000 lira ödedi", "payment"),
    ("ahmet 20 balya borcunu 15000 tl ödedi", "payment"),
]


@pytest.mark.parametrize("cumle,beklenen_kind", YON_ORNEKLERI)
def test_prompt_yon_orneklerini_dogru_ogretiyor(cumle, beklenen_kind):
    assert cumle in ORNEKLER, f"prompt'ta yön örneği eksik: {cumle!r}"
    assert ORNEKLER[cumle]["kind"] == beklenen_kind


def test_prompt_sattim_ornegi_urun_ve_tutari_ayiriyor():
    # "sattım" örneği yalnızca yönü değil, mal/para ayrımını da öğretmeli:
    # 20 balya MAL ölçüsü (qty/unit), 3000 lira PARA (amount).
    ornek = ORNEKLER["ali veliye 20 balya saman sattım 3000 lira"]
    assert ornek["qty"] == 20
    assert ornek["unit"] == "balya"
    assert ornek["product"] == "saman"
    assert ornek["amount"] == 3000


def test_prompt_kurallarinda_sattim_acikca_borc_diyor():
    # Küçük model (qwen2.5:3b) örnekleri kaçırabilir; kural metninde de
    # açıkça yazmalı ki tek kaynağa bağlı kalmasın.
    kurallar = SYSTEM_PROMPT.split("Örnekler:")[0]
    assert "sattım" in kurallar
    assert "aldım" in kurallar


def test_prompt_yonu_karsit_ciftle_ogretiyor():
    # Aynı kök ("al-") iki yöne gidiyor: "aldım" (ben) payment, "aldı" (o)
    # debt. İkisi de örneklerde bulunmazsa model birini diğerine ezer.
    kindler = {c: o["kind"] for c, o in ORNEKLER.items()}
    assert any(k == "payment" and "aldım" in c for c, k in kindler.items())
    assert any(k == "debt" and "aldı " in c for c, k in kindler.items())


# --------------------------------------------------------------- örneklerin tutarlılığı


@pytest.mark.parametrize("cumle", sorted(ORNEKLER))
def test_prompt_ornekleri_semaya_uyuyor(cumle):
    fazla = set(ORNEKLER[cumle]) - SEMA_ALANLARI
    assert not fazla, f"{cumle!r} örneğinde şema dışı alan: {fazla}"


@pytest.mark.parametrize("cumle", sorted(ORNEKLER))
def test_prompt_ornekleri_kodun_kabul_ettigi_cikti(cumle):
    """Her örnek, kendi cümlesiyle birlikte parsed_intent_from_json'dan
    geçmeli. Bu, isim doğrulamasını (hallucination koruması) da kapsar:
    prompt'ta ham metinde geçmeyen bir isim örneklenirse kod onu reddeder
    ve model boşuna öğrenmiş olur."""
    data = ORNEKLER[cumle]
    intent = parsed_intent_from_json(data, cumle)

    if data["kind"] is None and data["islem"] is None:
        assert intent is None  # "bugün hava çok güzel" — kasten çözümsüz
        return

    assert intent is not None, f"{cumle!r} örneği kod tarafından reddedildi"
    beklenen_isim = data["person_name"] or data["kisi"]
    if beklenen_isim:
        assert intent.person_name == beklenen_isim


# --------------------------------------------------------------- create_person
# Kural parser çözemezse (serbest cümle) kişi ekleme LLM'e düşer; LLM'in
# TEMİZ isim döndürmesi gerekir — "furkan duman adlı kişiyi sisteme" gibi
# komut kelimeleriyle dolu bir isimle kişi eşleştirmesi asla tutmaz.

CREATE_PERSON_ORNEKLERI = [
    ("furkan duman adlı kişiyi sisteme kayıt et", "furkan duman"),
    ("ercüment çözer kişisini ekle", "ercüment çözer"),
]


@pytest.mark.parametrize("cumle,beklenen_isim", CREATE_PERSON_ORNEKLERI)
def test_prompt_create_person_ornegi_temiz_isim_ogretiyor(cumle, beklenen_isim):
    assert cumle in ORNEKLER, f"prompt'ta create_person örneği eksik: {cumle!r}"
    ornek = ORNEKLER[cumle]
    assert ornek["kind"] == "create_person"
    assert ornek["person_name"] == beklenen_isim
    assert ornek["amount"] is None  # kişi ekleme bir para kaydı DEĞİL


def test_prompt_kurallarinda_create_person_komut_kelimeleri_yaziyor():
    # Küçük model örneği kaçırabilir; kural metninde de "bu kelimeler isme
    # dahil değil" açıkça yazmalı.
    kurallar = SYSTEM_PROMPT.split("Örnekler:")[0]
    assert "create_person" in kurallar
    for kelime in ("adlı", "kişisini", "sisteme"):
        assert kelime in kurallar


@pytest.mark.parametrize("cumle,beklenen_isim", CREATE_PERSON_ORNEKLERI)
def test_create_person_json_kod_tarafindan_kabul_edilir(cumle, beklenen_isim):
    # VALID_KINDS'a eklendi mi + isim doğrulaması (hallucination koruması)
    # çok kelimeli ismi elemiyor mu?
    intent = parsed_intent_from_json(ORNEKLER[cumle], cumle)
    assert intent is not None
    assert intent.kind == "create_person"
    assert intent.person_name == beklenen_isim


def test_create_person_isimsiz_json_reddedilir():
    # Kişisiz bir "kişi ekle" niyeti anlamsızdır — kod uydurmaz, reddeder.
    data = {"kind": "create_person", "person_name": None}
    assert parsed_intent_from_json(data, "birini ekle") is None


# --------------------------------------------------------------- 2026-08-31 anlama
# genişletmesi: regex'in kaçırdıklarını LLM de doğru anlamalı. Bu kalıpların
# çoğu artık regex'te çözülüyor (bkz. tests/test_parser.py); prompt'taki
# örnekler İKİNCİ katman — kullanıcı kalıbı daha da bozuk yazarsa (regex
# eşiğinin dışında) LLM aynı niyete varmalı, uydurmamalı.

GENISLETME_ORNEKLERI = [
    ("ali 1000 borçlandı", "debt"),
    ("ahmet borcunu ödedi", "payment"),
    ("mehmet kaç para", "balance_query"),
    ("kişler", "list_all"),
    ("toplam kaç saman satıldı", "product_query"),
]


@pytest.mark.parametrize("cumle,beklenen_kind", GENISLETME_ORNEKLERI)
def test_prompt_genisletme_orneklerini_ogretiyor(cumle, beklenen_kind):
    assert cumle in ORNEKLER, f"prompt'ta örnek eksik: {cumle!r}"
    assert ORNEKLER[cumle]["kind"] == beklenen_kind


def test_prompt_borclandi_kuralda_da_yaziyor():
    # "borçlandı" bir YÖN kelimesidir (kişi borçlandı = debt); tek örneğe
    # bırakılmaz, kural metninde de geçmeli.
    kurallar = SYSTEM_PROMPT.split("Örnekler:")[0]
    assert "BORÇLANDI" in kurallar or "borçlandı" in kurallar


def test_prompt_tutarsiz_borc_kapanisi_amount_uydurmuyor():
    # "ahmet borcunu ödedi": tutar söylenmemiş. LLM amount UYDURMAMALI —
    # güncel bakiyeyi kod teklif edip kullanıcıya onaylatır.
    ornek = ORNEKLER["ahmet borcunu ödedi"]
    assert ornek["amount"] is None


def test_tutarsiz_tahsilat_borc_kapanisi_olarak_isaretlenir():
    # parsed_intent_from_json, tutarsız bir tahsilatı borç kapanışı sayar
    # (close_debt) — aksi halde intent_resolver bunu sessizce "anlaşılamadı"
    # sayardı.
    intent = parsed_intent_from_json(ORNEKLER["ahmet borcunu ödedi"], "ahmet borcunu ödedi")
    assert intent is not None
    assert intent.kind == "payment"
    assert intent.amount is None
    assert intent.close_debt is True


def test_tutarli_tahsilat_borc_kapanisi_sayilmaz():
    intent = parsed_intent_from_json(
        ORNEKLER["mehmet bugün 2000 lira ödedi"], "mehmet bugün 2000 lira ödedi"
    )
    assert intent.close_debt is False


def test_urun_sorgusu_json_kod_tarafindan_kabul_edilir():
    # product_query VALID_KINDS'a eklendi mi + kişisiz kabul ediliyor mu?
    cumle = "toplam kaç saman satıldı"
    intent = parsed_intent_from_json(ORNEKLER[cumle], cumle)
    assert intent is not None
    assert intent.kind == "product_query"
    assert intent.product == "saman"
    assert intent.person_name is None


def test_prompt_yazim_hatasi_toleransi_kuralda_yaziyor():
    kurallar = SYSTEM_PROMPT.split("Örnekler:")[0]
    for kelime in ("kişler", "ksiler"):
        assert kelime in kurallar
