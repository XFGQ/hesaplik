"""OllamaProvider/VLLMProvider ve dinamik kaynak seçimi testleri. Gerçek
Ollama/vLLM'e ASLA bağlanılmaz — httpx MockTransport ile ağ çağrısı taklit
edilir, sağlık kontrolleri ise vllm_healthy/ollama_healthy monkeypatch'iyle."""

import json
from decimal import Decimal

import httpx
import pytest

from app.models import Setting
from app.services import llm_provider
from app.services.llm_prompt import RESPONSE_JSON_SCHEMA, SYSTEM_PROMPT
from app.services.llm_provider import (
    LLM_PRIMARY_DEFAULT,
    OllamaProvider,
    VLLMProvider,
    get_active_provider,
    get_llm_primary,
    get_status,
    parsed_intent_from_json,
    select_source,
    set_llm_primary,
)


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _chat_response(content: dict | str) -> httpx.Response:
    body = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"message": {"role": "assistant", "content": body}})


# --------------------------------------------------------------- parsed_intent_from_json


def test_json_borc_urunlu_dogru_cevrilir():
    data = {
        "kind": "debt", "person_name": "furkan", "qty": 20, "unit": "balya",
        "product": "saman", "amount": 15000, "district": None,
    }
    intent = parsed_intent_from_json(data)
    assert intent.kind == "debt"
    assert intent.person_name == "furkan"
    assert intent.qty == Decimal("20")
    assert intent.unit == "balya"
    assert intent.product == "saman"
    assert intent.amount == Decimal("15000")


def test_json_bakiye_sorgusu_dogru_cevrilir():
    data = {
        "kind": "balance_query", "person_name": "duman", "qty": None,
        "unit": None, "product": None, "amount": None, "district": None,
    }
    intent = parsed_intent_from_json(data)
    assert intent.kind == "balance_query"
    assert intent.person_name == "duman"
    assert intent.amount is None


def test_json_ilce_listesi_dogru_cevrilir():
    data = {
        "kind": "list_district", "person_name": None, "qty": None,
        "unit": None, "product": None, "amount": None, "district": "bergama",
    }
    intent = parsed_intent_from_json(data)
    assert intent.kind == "list_district"
    assert intent.district == "bergama"


def test_json_kind_null_ise_none_doner():
    data = {"kind": None, "person_name": None, "qty": None, "unit": None,
            "product": None, "amount": None, "district": None}
    assert parsed_intent_from_json(data) is None


def test_json_bilinmeyen_kind_none_doner():
    data = {"kind": "sohbet", "person_name": "ahmet"}
    assert parsed_intent_from_json(data) is None


def test_json_kisisiz_borc_none_doner():
    # "kind: debt" ama person_name yok -> anlamsız, LLM çözemedi sayılır.
    data = {"kind": "debt", "person_name": None, "amount": 500}
    assert parsed_intent_from_json(data) is None


def test_json_gecersiz_tutar_null_olur():
    data = {"kind": "debt", "person_name": "ahmet", "amount": "bilmiyorum"}
    intent = parsed_intent_from_json(data)
    assert intent.amount is None


# --------------------------------------------------------------- KRİTİK: LLM isim bozuyor
# (CLAUDE.md 2026-07-27) — LLM'in uydurduğu bir isim ham metinde hiç
# geçmiyorsa (ör. "mehmetten" -> "mehtap") güvenilmemeli. raw_text
# verilmediğinde (eski/doğrudan birim testleri gibi) doğrulama atlanır.


def test_json_raw_text_yoksa_dogrulama_atlanir():
    data = {"kind": "payment", "person_name": "ahmet", "amount": 500}
    intent = parsed_intent_from_json(data)
    assert intent.person_name == "ahmet"


def test_json_isim_ham_metinle_ortusuyorsa_kabul_edilir():
    data = {"kind": "payment", "person_name": "ahmet", "amount": 500}
    intent = parsed_intent_from_json(data, "ahmete 500 tl verdim")
    assert intent.person_name == "ahmet"


def test_json_isim_uydurulmussa_reddedilir():
    # LLM "mehmetten" yerine "mehtap" uydurmuş: ham metinde böyle bir
    # kelime yok, kayıt niyeti kişisiz sayılır ve None döner.
    data = {"kind": "payment", "person_name": "mehtap", "amount": 5000}
    intent = parsed_intent_from_json(data, "mehmetten 5000 aldım")
    assert intent is None


def test_json_isim_ekli_haliyle_aynen_donduysa_kabul_edilir():
    # LLM ismi hiç dokunmadan (ekli haliyle) döndürmüş — CLAUDE.md kuralı
    # bu şekilde bekliyor, koruma bunu reddetmemeli.
    data = {"kind": "payment", "person_name": "mehmetten", "amount": 5000}
    intent = parsed_intent_from_json(data, "mehmetten 5000 aldım")
    assert intent.person_name == "mehmetten"


def test_json_isim_koku_donduyse_kabul_edilir():
    # LLM eki kendi soymuş ("mehmet"), yasak ama harf uydurmamış — ham
    # metinle hâlâ yeterince örtüşüyor, güvenilir kabul edilmeli.
    data = {"kind": "payment", "person_name": "mehmet", "amount": 5000}
    intent = parsed_intent_from_json(data, "mehmetten 5000 aldım")
    assert intent.person_name == "mehmet"


def test_json_kisa_isim_ekliyken_yanlislikla_reddedilmez():
    # Önceki (saf difflib) eşik, kısa isim + ek kombinasyonlarında geçerli
    # isimleri reddediyordu (ör. "ali"/"aliden" oranı eşiğin altına
    # düşüyordu). Kök eşleşmesi bunu düzeltir.
    data = {"kind": "payment", "person_name": "ali", "amount": 200}
    intent = parsed_intent_from_json(data, "aliden 200 aldım")
    assert intent.person_name == "ali"


def test_json_isim_dogrulama_deterministik():
    # Aynı (person_name, raw_text) girdisi her çağrıda aynı sonucu vermeli
    # — koruma saf/pure bir fonksiyon olmalı, LLM örneklemesinden bağımsız.
    data = {"kind": "payment", "person_name": "mehmetten", "amount": 5000}
    raw_text = "mehmetten 5000 aldım"
    results = [parsed_intent_from_json(data, raw_text).person_name for _ in range(3)]
    assert results == ["mehmetten"] * 3


def test_json_rapor_kisi_uydurulmussa_menu_doner():
    data = {"kind": None, "islem": "rapor", "tur": "kisi", "kisi": "mehtap"}
    intent = parsed_intent_from_json(data, "mehmetin ekstresini ver")
    assert intent.kind == "report_menu"


# --------------------------------------------------------------- rapor (islem/tur/kisi)


def test_json_rapor_genel():
    data = {"kind": None, "islem": "rapor", "tur": "genel", "kisi": None}
    intent = parsed_intent_from_json(data)
    assert intent.kind == "report_general"


def test_json_rapor_gunluk():
    data = {"kind": None, "islem": "rapor", "tur": "gunluk", "kisi": None}
    intent = parsed_intent_from_json(data)
    assert intent.kind == "report_daily"


def test_json_rapor_kisi():
    data = {"kind": None, "islem": "rapor", "tur": "kisi", "kisi": "ahmet"}
    intent = parsed_intent_from_json(data)
    assert intent.kind == "report_person"
    assert intent.person_name == "ahmet"


def test_json_rapor_kisi_isimsizse_menu_doner():
    # tur "kisi" ama kişi adı boş -> uydurma, menü sorulsun.
    data = {"kind": None, "islem": "rapor", "tur": "kisi", "kisi": None}
    intent = parsed_intent_from_json(data)
    assert intent.kind == "report_menu"


def test_json_rapor_tur_belirsiz_menu_doner():
    data = {"kind": None, "islem": "rapor", "tur": None, "kisi": None}
    intent = parsed_intent_from_json(data)
    assert intent.kind == "report_menu"


def test_json_rapor_tur_bilinmeyen_menu_doner():
    data = {"kind": None, "islem": "rapor", "tur": "her_ihtimale_karsi", "kisi": None}
    intent = parsed_intent_from_json(data)
    assert intent.kind == "report_menu"


# --------------------------------------------------------------- kişi bilgisi
# (CLAUDE.md > "DÜZELTME — 'bilgi ver' belirsiz, SOR"): islem="bilgi_menu"/
# "iletisim", rapor ailesiyle aynı şema (islem/kisi), kişisiz fallback'i
# anlamsız olduğu için (ikisi de zaten bir kişiyi hedefler) None döner.


def test_json_bilgi_menu_kisi_ile_donusur():
    data = {"kind": None, "islem": "bilgi_menu", "tur": None, "kisi": "esma"}
    intent = parsed_intent_from_json(data)
    assert intent.kind == "info_menu"
    assert intent.person_name == "esma"


def test_json_bilgi_menu_kisisiz_none_doner():
    data = {"kind": None, "islem": "bilgi_menu", "tur": None, "kisi": None}
    assert parsed_intent_from_json(data) is None


def test_json_iletisim_kisi_ile_donusur():
    data = {"kind": None, "islem": "iletisim", "tur": None, "kisi": "esma"}
    intent = parsed_intent_from_json(data)
    assert intent.kind == "person_contact"
    assert intent.person_name == "esma"


def test_json_iletisim_kisisiz_none_doner():
    data = {"kind": None, "islem": "iletisim", "tur": None, "kisi": None}
    assert parsed_intent_from_json(data) is None


def test_json_iletisim_isim_uydurulmussa_none_doner():
    # Hallucination koruması (_verified_person_name) islem=="iletisim"
    # yolunda da uygulanmalı.
    data = {"kind": None, "islem": "iletisim", "tur": None, "kisi": "mehtap"}
    intent = parsed_intent_from_json(data, "mehmetten telefon numarasını ver")
    assert intent is None


# --------------------------------------------------------------- OllamaProvider


async def test_ollama_saglikli_yanit_parsed_intent_doner():
    def handler(request):
        assert request.url.path == "/api/chat"
        return _chat_response({
            "kind": "debt", "person_name": "furkan", "qty": 20, "unit": "balya",
            "product": "saman", "amount": 15000, "district": None,
        })

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:7b", client=client)
        intent = await provider.parse("furkana 20 balya saman verdim 15000 tl borç yazsana")

    assert intent is not None
    assert intent.kind == "debt"
    assert intent.person_name == "furkan"
    assert intent.amount == Decimal("15000")


async def test_ollama_sattim_borc_olarak_gecer():
    # YÖN: "sattım" = mal ONA gitti = debt. LLM doğru yönü döndürdüğünde
    # provider hattı bunu bozmadan geçirmeli — özellikle iki kelimelik ad
    # ("ali veliye") isim doğrulamasına takılmamalı, yoksa doğru yanıt
    # kişisiz kalıp None'a düşerdi.
    def handler(request):
        return _chat_response({
            "kind": "debt", "person_name": "ali veliye", "qty": 20,
            "unit": "balya", "product": "saman", "amount": 3000, "district": None,
        })

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:3b", client=client)
        intent = await provider.parse("ali veliye 20 balya saman sattım 3000 lira")

    assert intent is not None
    assert intent.kind == "debt"
    assert intent.person_name == "ali veliye"
    assert intent.qty == Decimal("20")
    assert intent.unit == "balya"
    assert intent.product == "saman"
    assert intent.amount == Decimal("3000")


async def test_ollama_ucuncu_sahis_aldi_borc_olarak_gecer():
    # "aldı" (O aldı) = debt; "aldım" (BEN aldım) = payment. Aynı kök, ters
    # yön — hat ikisini de olduğu gibi taşımalı.
    def handler(request):
        return _chat_response({
            "kind": "debt", "person_name": "ahmet", "qty": 10, "unit": "çuval",
            "product": "yem", "amount": 1500, "district": None,
        })

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:3b", client=client)
        intent = await provider.parse("ahmet 10 çuval yem aldı 1500 borç")

    assert intent is not None
    assert intent.kind == "debt"
    assert intent.amount == Decimal("1500")


async def test_ollama_odedi_tahsilat_olarak_gecer():
    # "borcunu ödedi": cümlede "borç" geçse de yön TAHSİLAT (borç kapanıyor).
    def handler(request):
        return _chat_response({
            "kind": "payment", "person_name": "ahmet", "qty": 20, "unit": "balya",
            "product": None, "amount": 15000, "district": None,
        })

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:3b", client=client)
        intent = await provider.parse("ahmet 20 balya borcunu 15000 tl ödedi")

    assert intent is not None
    assert intent.kind == "payment"
    assert intent.amount == Decimal("15000")


async def test_ollama_baglanti_hatasinda_none_doner():
    def handler(request):
        raise httpx.ConnectError("bağlanamadı", request=request)

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:7b", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_ollama_timeoutta_none_doner():
    def handler(request):
        raise httpx.ReadTimeout("zaman aşımı", request=request)

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:7b", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_ollama_http_hata_kodunda_none_doner():
    def handler(request):
        return httpx.Response(500, text="internal error")

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:7b", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_ollama_gecersiz_json_icerikte_none_doner():
    def handler(request):
        return _chat_response("bu bir json değil")

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:7b", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_ollama_bos_icerikte_none_doner():
    def handler(request):
        return httpx.Response(200, json={"message": {"role": "assistant", "content": ""}})

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:7b", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_ollama_anlasilamayan_cumlede_none_doner():
    def handler(request):
        return _chat_response({
            "kind": None, "person_name": None, "qty": None, "unit": None,
            "product": None, "amount": None, "district": None,
        })

    async with _client_for(handler) as client:
        provider = OllamaProvider("http://localhost:11434", "qwen2.5:7b", client=client)
        intent = await provider.parse("bugün hava çok güzel")

    assert intent is None


# --------------------------------------------------------------- VLLMProvider
#
# Ollama'dan tek farkı OpenAI-uyumlu istek/yanıt şekli (/v1/chat/completions,
# choices[0].message.content); doğrulama/güvenlik katmanı aynı
# parsed_intent_from_json'dan geçtiği için burada tekrar edilmiyor, yalnızca
# HTTP hattı doğrulanıyor.


def _vllm_response(content: dict | str) -> httpx.Response:
    body = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"choices": [{"message": {"content": body}}]})


async def test_vllm_saglikli_yanit_parsed_intent_doner():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        return _vllm_response({
            "kind": "debt", "person_name": "furkan", "qty": 20, "unit": "balya",
            "product": "saman", "amount": 15000, "district": None,
        })

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "Qwen/Qwen2.5-7B-Instruct-AWQ", client=client)
        intent = await provider.parse("furkana 20 balya saman verdim 15000 tl borç yazsana")

    assert intent is not None
    assert intent.kind == "debt"
    assert intent.person_name == "furkan"
    assert intent.amount == Decimal("15000")


async def test_vllm_baglanti_hatasinda_none_doner():
    def handler(request):
        raise httpx.ConnectError("bağlanamadı", request=request)

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "model", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_vllm_timeoutta_none_doner():
    def handler(request):
        raise httpx.ReadTimeout("zaman aşımı", request=request)

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "model", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_vllm_http_hata_kodunda_none_doner():
    def handler(request):
        return httpx.Response(500, text="internal error")

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "model", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_vllm_gecersiz_json_icerikte_none_doner():
    def handler(request):
        return _vllm_response("bu bir json değil")

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "model", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_vllm_bos_choices_icerikte_none_doner():
    def handler(request):
        return httpx.Response(200, json={"choices": []})

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "model", client=client)
        intent = await provider.parse("herhangi bir cümle")

    assert intent is None


async def test_vllm_istek_govdesinde_system_prompt_ve_guided_json_var():
    # Gözlem (2026-08): vLLM 200 OK dönüp sohbet metniyle cevap verebiliyor
    # — kontrol edilmesi gereken SYSTEM_PROMPT'un gönderilip gönderilmediği
    # değil (zaten gönderiliyordu), modelin buna uymaması. guided_json bu
    # durumda çıktıyı gramer düzeyinde JSON'a zorlayan ek bir güvence.
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _vllm_response({
            "kind": "list_all", "person_name": None, "qty": None, "unit": None,
            "product": None, "amount": None, "district": None,
        })

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "model", client=client)
        await provider.parse("kişileer")

    messages = captured["body"]["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "kişileer"}
    assert captured["body"]["guided_json"] == RESPONSE_JSON_SCHEMA


async def test_vllm_kod_bloguna_sarilmis_json_ayiklanir():
    # Model bazen "İşte cevap:\n```json\n{...}\n```" gibi sarmalıyor —
    # SADECE JSON istense de. _extract_json_object bunu tolere etmeli.
    def handler(request):
        return _vllm_response(
            'İşte JSON:\n```json\n{"kind": "list_all", "person_name": null, '
            '"qty": null, "unit": null, "product": null, "amount": null, '
            '"district": null}\n```\nUmarım yardımcı olur.'
        )

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "model", client=client)
        intent = await provider.parse("kişileer")

    assert intent is not None
    assert intent.kind == "list_all"


async def test_vllm_sohbet_metni_json_icermiyorsa_none_doner():
    # Gerçek repro (2026-08): model TAMAMEN sohbet cevabı veriyor, JSON hiç
    # yok. Ayıklanacak bir şey olmadığı için None dönmeli — sistem çökmez,
    # kural parser + "elle gir" ile devam eder.
    def handler(request):
        return _vllm_response("Kişiler hakkında daha fazla bilgi verebilmem için lütfen...")

    async with _client_for(handler) as client:
        provider = VLLMProvider("http://10.100.0.2:8000", "model", client=client)
        intent = await provider.parse("kişileer")

    assert intent is None


# --------------------------------------------------------------- select_source
#
# Saf karar fonksiyonu (ağa çıkmaz) — get_active_provider ve get_status'un
# ikisinin de aynı mantığı paylaştığını garanti eder.


def test_select_source_none_her_zaman_kapali():
    assert select_source("none", vllm_ok=True, ollama_ok=True) == "none"


def test_select_source_ollama_zorla_vllme_bakmaz():
    # vLLM sağlıklı olsa bile "ollama" tercihi Ollama'yı seçer — GPU'yu
    # kullanıcı kendi kullanmak istediğinde "tek tuş kapat" senaryosu.
    assert select_source("ollama", vllm_ok=True, ollama_ok=False) == "ollama"


def test_select_source_vllm_zorla_saglikliysa_vllm():
    assert select_source("vllm", vllm_ok=True, ollama_ok=True) == "vllm"


def test_select_source_vllm_zorla_erisilemezse_none_ollamaya_duşmez():
    # Kullanıcı özellikle vLLM istemiştir; erişilemezse none'a düşer,
    # sessizce Ollama'ya kaymaz.
    assert select_source("vllm", vllm_ok=False, ollama_ok=True) == "none"


def test_select_source_auto_vllm_saglikliysa_vllm():
    assert select_source("auto", vllm_ok=True, ollama_ok=True) == "vllm"


def test_select_source_auto_vllm_cokerse_ollamaya_duser():
    assert select_source("auto", vllm_ok=False, ollama_ok=True) == "ollama"


def test_select_source_auto_ikisi_de_cokerse_none():
    assert select_source("auto", vllm_ok=False, ollama_ok=False) == "none"


def test_select_source_taninmayan_deger_auto_gibi_davranir():
    assert select_source("bozuk-deger", vllm_ok=True, ollama_ok=False) == "vllm"
    assert select_source("bozuk-deger", vllm_ok=False, ollama_ok=False) == "none"


# --------------------------------------------------------------- tercih (DB)


async def test_llm_primary_hic_ayarlanmamissa_auto_doner(session):
    assert await get_llm_primary(session) == LLM_PRIMARY_DEFAULT == "auto"


async def test_llm_primary_set_sonra_get_dogru_deger_doner(session):
    await set_llm_primary(session, "ollama")
    assert await get_llm_primary(session) == "ollama"


async def test_llm_primary_gecersiz_deger_set_edilemez(session):
    with pytest.raises(ValueError):
        await set_llm_primary(session, "bogus")


async def test_llm_primary_bozuk_db_degeri_auto_sayilir(session):
    # Bozuk/eski bir değer sistemi LLM'siz bırakmaz, güvenli varsayılana
    # düşer (CLAUDE.md ilkesiyle tutarlı: hatalı veri sessizce çökertmez).
    session.add(Setting(key="llm_primary", value="eski-surum-degeri"))
    await session.flush()
    assert await get_llm_primary(session) == "auto"


# --------------------------------------------------------------- get_active_provider


def _stub_health(monkeypatch, *, vllm: bool, ollama: bool) -> None:
    async def _vllm_healthy():
        return vllm

    async def _ollama_healthy():
        return ollama

    monkeypatch.setattr(llm_provider, "vllm_healthy", _vllm_healthy)
    monkeypatch.setattr(llm_provider, "ollama_healthy", _ollama_healthy)
    llm_provider.reset_health_cache()


async def test_get_active_provider_auto_vllm_saglikliyse_vllm_secilir(session, monkeypatch):
    _stub_health(monkeypatch, vllm=True, ollama=False)
    provider = await get_active_provider(session)
    assert isinstance(provider, VLLMProvider)


async def test_get_active_provider_auto_vllm_cokerse_ollamaya_duser(session, monkeypatch):
    _stub_health(monkeypatch, vllm=False, ollama=True)
    provider = await get_active_provider(session)
    assert isinstance(provider, OllamaProvider)


async def test_get_active_provider_auto_ikisi_de_cokerse_none_doner(session, monkeypatch):
    _stub_health(monkeypatch, vllm=False, ollama=False)
    provider = await get_active_provider(session)
    assert provider is None


async def test_get_active_provider_ollama_tercihinde_vllme_hic_dokunulmaz(session, monkeypatch):
    calls: list[str] = []

    async def _vllm_healthy():
        calls.append("vllm")
        return True

    monkeypatch.setattr(llm_provider, "vllm_healthy", _vllm_healthy)
    llm_provider.reset_health_cache()

    await set_llm_primary(session, "ollama")
    provider = await get_active_provider(session)

    assert isinstance(provider, OllamaProvider)
    assert calls == []


async def test_get_active_provider_vllm_zorla_erisilemezse_none_ollamaya_duşmez(session, monkeypatch):
    _stub_health(monkeypatch, vllm=False, ollama=True)
    await set_llm_primary(session, "vllm")
    provider = await get_active_provider(session)
    assert provider is None


async def test_get_active_provider_none_tercihinde_hic_saglik_kontrolu_yapmaz(session, monkeypatch):
    calls: list[str] = []

    async def _vllm_healthy():
        calls.append("vllm")
        return True

    async def _ollama_healthy():
        calls.append("ollama")
        return True

    monkeypatch.setattr(llm_provider, "vllm_healthy", _vllm_healthy)
    monkeypatch.setattr(llm_provider, "ollama_healthy", _ollama_healthy)
    llm_provider.reset_health_cache()

    await set_llm_primary(session, "none")
    provider = await get_active_provider(session)

    assert provider is None
    assert calls == []


# --------------------------------------------------------------- get_status


async def test_get_status_ollama_tercihinde_bile_ikisini_de_kontrol_eder(session, monkeypatch):
    # get_active_provider "ollama" tercihinde vLLM'e hiç dokunmaz (yukarıdaki
    # test), ama admin panelin durumu (get_status) ikisini de her zaman
    # gösterir — admin gerçek durumu görmeli, yalnızca aktif olanı değil.
    _stub_health(monkeypatch, vllm=True, ollama=True)
    await set_llm_primary(session, "ollama")

    status = await get_status(session)

    assert status.primary == "ollama"
    assert status.active == "ollama"
    assert status.vllm.ok is True
    assert status.ollama.ok is True


async def test_get_status_kaynak_bilgilerini_configten_dolduruyor(session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vllm_url", "http://10.100.0.2:8000")
    monkeypatch.setattr(settings, "vllm_model", "Qwen/Qwen2.5-7B-Instruct-AWQ")
    monkeypatch.setattr(settings, "ollama_url", "http://ollama:11434")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:3b")
    _stub_health(monkeypatch, vllm=False, ollama=False)

    status = await get_status(session)

    assert status.vllm.url == "http://10.100.0.2:8000"
    assert status.vllm.model == "Qwen/Qwen2.5-7B-Instruct-AWQ"
    assert status.ollama.url == "http://ollama:11434"
    assert status.ollama.model == "qwen2.5:3b"
    assert status.active == "none"
