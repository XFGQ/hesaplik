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
