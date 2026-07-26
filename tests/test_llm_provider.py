"""OllamaProvider testleri. Gerçek Ollama'ya ASLA bağlanılmaz — httpx
MockTransport ile ağ çağrısı taklit edilir."""

import json
from decimal import Decimal

import httpx

from app.services.llm_provider import OllamaProvider, parsed_intent_from_json


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
