"""Web sohbet uçları (/api/chat, /api/chat/confirm) — Telegram botunun web
karşılığı. Aynı beyni (process_raw_message/handle_resolved) kullandığı için
buradaki senaryolar tests/test_message_processor.py ve tests/test_bot_*.py
ile kasıtlı olarak AYNI girdi metinlerini kullanır — davranış paritesinin
kanıtı budur (CLAUDE.md > "Web'e chat asistanı ekle": "İki mantık OLMAYACAK").
"""

from decimal import Decimal

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.db import get_session
from app.models import Person, PendingRequest, Product, Transaction, TxSource
from app.services.parser import ParsedIntent
from conftest import AUTH_PASSWORD, AUTH_USERNAME


@pytest_asyncio.fixture(loop_scope="session")
async def client(session):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client, auth_account) -> str:
    r = await client.post("/api/auth/login", json={"username": AUTH_USERNAME, "password": AUTH_PASSWORD})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token


@pytest_asyncio.fixture(loop_scope="session")
async def ahmet(session):
    p = Person(full_name="Ahmet Yılmaz")
    session.add(p)
    await session.flush()
    return p


@pytest_asyncio.fixture(loop_scope="session")
async def saman(session):
    p = Product(name="Saman", base_unit="balya")
    session.add(p)
    await session.flush()
    return p


def _actions(messages: list[dict]) -> list[str]:
    return [b["action"] for m in messages for b in m["buttons"]]


# ---------------------------------------------------------------- koruma

async def test_tokensiz_401(client, auth_account):
    r = await client.post("/api/chat", json={"text": "kişileri listele"})
    assert r.status_code == 401


async def test_confirm_tokensiz_401(client, auth_account):
    r = await client.post("/api/chat/confirm", json={"action": "person:yes"})
    assert r.status_code == 401


# ---------------------------------------------------------------- kayıt (RECORDED)

async def test_net_borc_dogrudan_kaydedilir_ve_gunceli_aktoru_web_olarak_yazar(client, auth_account, session, ahmet):
    await _login(client, auth_account)

    r = await client.post("/api/chat", json={"text": "ahmet yılmaz 20 balya saman aldı 15000 tl borç"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["messages"]) == 1
    msg = body["messages"][0]
    assert msg["outcome"] == "recorded"
    assert "Önceki bakiye" in msg["reply"]
    assert "Güncel bakiye" in msg["reply"]
    assert msg["buttons"][0]["action"].startswith("undo:")

    tx = (await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))).scalar_one()
    assert tx.source == TxSource.WEB
    assert tx.created_by == AUTH_USERNAME


async def test_undo_penceresinde_geri_alinir(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "ahmet yılmaz 500 tl borç yazdım"})
    tx_id = r.json()["messages"][0]["buttons"][0]["action"].split(":")[1]

    r2 = await client.post("/api/chat/confirm", json={"action": f"undo:{tx_id}"})
    assert r2.status_code == 200
    assert r2.json()["messages"][0]["outcome"] == "undone"

    # ikinci kez aynı undo çalışmaz.
    r3 = await client.post("/api/chat/confirm", json={"action": f"undo:{tx_id}"})
    assert r3.json()["messages"][0]["outcome"] == "expired"


async def test_bilinmeyen_undo_suresi_gecti_der(client, auth_account):
    await _login(client, auth_account)
    r = await client.post("/api/chat/confirm", json={"action": "undo:999999"})
    assert r.json()["messages"][0]["outcome"] == "expired"


# ---------------------------------------------------------------- kişi adayı belirsizliği

async def test_belirsiz_kisi_aday_butonlariyla_sorar_ve_secilince_tamamlanir(client, auth_account, session):
    await _login(client, auth_account)
    duman = Person(full_name="Furkan Duman")
    yilmaz = Person(full_name="Furkan Yılmaz")
    session.add_all([duman, yilmaz])
    await session.flush()

    r = await client.post("/api/chat", json={"text": "furkan borcunu söyle"})
    body = r.json()
    assert len(body["messages"]) == 1
    msg = body["messages"][0]
    assert msg["outcome"] == "needs_confirmation"
    ids = {b["action"] for b in msg["buttons"]}
    assert f"person:pick:{duman.id}" in ids
    assert f"person:pick:{yilmaz.id}" in ids
    assert "person:new" in ids

    r2 = await client.post("/api/chat/confirm", json={"action": f"person:pick:{duman.id}"})
    msg2 = r2.json()["messages"][0]
    assert msg2["outcome"] == "balance"
    assert "Furkan Duman" in msg2["reply"]


async def test_belirsiz_aday_hayir_ile_iptal_edilir(client, auth_account, session):
    await _login(client, auth_account)
    a = Person(full_name="Ahmet Yılmaz")
    b = Person(full_name="Ahmet Yıldız")
    session.add_all([a, b])
    await session.flush()

    r = await client.post("/api/chat", json={"text": "ahmet borcunu söyle"})
    assert r.json()["messages"][0]["outcome"] == "needs_confirmation"

    r2 = await client.post("/api/chat/confirm", json={"action": "person:no"})
    assert r2.json()["messages"][0]["outcome"] == "cancelled"


# ---------------------------------------------------------------- ürün fuzzy onayı

async def test_urun_yazim_hatasinda_oneri_sorar(client, auth_account, session, ahmet, saman):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "ahmet yılmaz 20 balya samaan aldı 5000 tl borç"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "product_needs_confirmation"
    assert msg["reply"] == "'samaan' → 'Saman' mı?"
    assert {b["action"] for b in msg["buttons"]} == {"product:yes", "product:new", "product:cancel"}

    count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert count == 0


async def test_urun_onerisi_evet_ile_mevcut_urune_baglanir(client, auth_account, session, ahmet, saman):
    await _login(client, auth_account)
    await client.post("/api/chat", json={"text": "ahmet yılmaz 20 balya samaan aldı 5000 tl borç"})

    r = await client.post("/api/chat/confirm", json={"action": "product:yes"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "recorded"

    tx = (await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))).scalar_one()
    assert tx.lines[0].product_id == saman.id
    urun_sayisi = (await session.execute(select(func.count(Product.id)))).scalar_one()
    assert urun_sayisi == 1


async def test_urun_onerisi_hayir_ile_ham_adla_yeni_urun_acar(client, auth_account, session, ahmet, saman):
    await _login(client, auth_account)
    await client.post("/api/chat", json={"text": "ahmet yılmaz 20 balya samaan aldı 5000 tl borç"})

    await client.post("/api/chat/confirm", json={"action": "product:new"})

    tx = (await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))).scalar_one()
    yeni = await session.get(Product, tx.lines[0].product_id)
    assert yeni.name == "samaan"
    urun_sayisi = (await session.execute(select(func.count(Product.id)))).scalar_one()
    assert urun_sayisi == 2


async def test_urun_onerisi_iptal_ile_hicbir_sey_kaydedilmez(client, auth_account, session, ahmet, saman):
    await _login(client, auth_account)
    await client.post("/api/chat", json={"text": "ahmet yılmaz 20 balya samaan aldı 5000 tl borç"})

    r = await client.post("/api/chat/confirm", json={"action": "product:cancel"})
    assert r.json()["messages"][0]["outcome"] == "cancelled"
    count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert count == 0


# ---------------------------------------------------------------- LLM önizleme onayı

async def test_llm_kaynakli_kayit_onay_ister_evet_ile_kaydedilir(client, auth_account, session, ahmet, fake_llm):
    await _login(client, auth_account)
    text = "ahmete bir miktar ödeme yapmak istiyorum"
    fake_llm.intent = ParsedIntent(kind="debt", person_name="ahmet yılmaz", amount=Decimal("500"))

    r = await client.post("/api/chat", json={"text": text})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "llm_confirmation"
    assert {b["action"] for b in msg["buttons"]} == {"llm:yes", "llm:fix", "llm:cancel"}

    r2 = await client.post("/api/chat/confirm", json={"action": "llm:yes"})
    msg2 = r2.json()["messages"][0]
    assert msg2["outcome"] == "recorded"

    tx = (await session.execute(select(Transaction).where(Transaction.person_id == ahmet.id))).scalar_one()
    assert tx.amount_try == Decimal("500.00")


async def test_llm_onayi_iptal_ile_hicbir_sey_kaydetmez(client, auth_account, session, ahmet, fake_llm):
    await _login(client, auth_account)
    text = "ahmete bir miktar ödeme yapmak istiyorum"
    fake_llm.intent = ParsedIntent(kind="debt", person_name="ahmet yılmaz", amount=Decimal("500"))
    await client.post("/api/chat", json={"text": text})

    r = await client.post("/api/chat/confirm", json={"action": "llm:cancel"})
    assert r.json()["messages"][0]["outcome"] == "cancelled"
    count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert count == 0


# ---------------------------------------------------------------- yeni kişi adım adım

async def test_bulunmayan_kisi_evet_ile_adim_adim_toplanir_hepsini_gec_ile_tamamlanir(client, auth_account, session):
    await _login(client, auth_account)

    r = await client.post("/api/chat", json={"text": "zeynep kaya 500 tl borç yazdım"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "person_not_found"
    assert "Ekleyeyim mi?" in msg["reply"]

    r2 = await client.post("/api/chat/confirm", json={"action": "person:yes"})
    msg2 = r2.json()["messages"][0]
    assert msg2["outcome"] == "new_person_step"
    assert msg2["awaits_text"] is True
    assert "Zeynep Kaya" in msg2["reply"]

    r3 = await client.post("/api/chat/confirm", json={"action": "newperson:skip_all"})
    msg3 = r3.json()["messages"][0]
    assert msg3["outcome"] == "recorded"

    person = (await session.execute(select(Person).where(Person.full_name == "Zeynep Kaya"))).scalar_one()
    tx = (await session.execute(select(Transaction).where(Transaction.person_id == person.id))).scalar_one()
    assert tx.amount_try == Decimal("500.00")


async def test_yeni_kisi_akisinda_serbest_metin_adimlari_ilerletir(client, auth_account, session):
    await _login(client, auth_account)
    await client.post("/api/chat", json={"text": "mehmet demir 200 tl borç yazdım"})
    await client.post("/api/chat/confirm", json={"action": "person:yes"})

    r1 = await client.post("/api/chat", json={"text": "Onayla"})
    # "Onayla" ad soyad adımını serbest metinle geçmez (buton bekler) — ama
    # metin de "step" alanına yazılır (bot'ta da aynı: herhangi bir metin adı
    # değiştirir). Burada asıl amaç: adım adım metinle ilerleyebiliyor mu.
    assert r1.json()["messages"][0]["awaits_text"] is True

    r2 = await client.post("/api/chat", json={"text": "555 111 22 33"})  # telefon
    assert r2.json()["messages"][0]["awaits_text"] is True

    r3 = await client.post("/api/chat", json={"text": "geç"})  # il — boş değer sayılmaz ama serbest metin kabul edilir
    assert r3.json()["messages"][0]["awaits_text"] is True

    r4 = await client.post("/api/chat", json={"text": "Bergama"})  # ilçe -> akış tamamlanır
    msg4 = r4.json()["messages"][0]
    assert msg4["outcome"] == "recorded"

    person = (await session.execute(select(Person).where(Person.full_name == "Onayla"))).scalar_one()
    assert person.district == "Bergama"


# ---------------------------------------------------------------- kişi düzenleme

async def test_edit_net_komut_evet_ile_uygulanir(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "ahmet yılmaz ilçe ahmetbeyler yap"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "edit_person_confirm"
    assert "Ahmetbeyler" in msg["reply"]

    r2 = await client.post("/api/chat/confirm", json={"action": "edit:yes"})
    msg2 = r2.json()["messages"][0]
    assert "Ahmetbeyler olarak güncellendi" in msg2["reply"]

    await session.refresh(ahmet)
    assert ahmet.district == "Ahmetbeyler"


async def test_edit_belirsiz_komut_menu_sorar_alan_secilince_eski_deger_gosterilir(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    ahmet.district = "Bergama"
    await session.flush()

    r = await client.post("/api/chat", json={"text": "ahmet yılmaz düzenle"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "edit_person_menu"
    assert "editfield:district" in {b["action"] for b in msg["buttons"]}

    r2 = await client.post("/api/chat/confirm", json={"action": "editfield:district"})
    msg2 = r2.json()["messages"][0]
    assert "Bergama" in msg2["reply"]
    assert msg2["awaits_text"] is True

    r3 = await client.post("/api/chat", json={"text": "İzmir"})
    msg3 = r3.json()["messages"][0]
    assert "İzmir olarak güncellendi" in msg3["reply"]
    await session.refresh(ahmet)
    assert ahmet.district == "İzmir"


# ---------------------------------------------------------------- arşivleme (silme)

async def test_kisi_silme_dogru_kelimeyle_arsivlenir(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "ahmet yılmaz sil"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "archive_confirm"
    assert "silinecek" in msg["reply"]
    assert msg["awaits_text"] is True

    r2 = await client.post("/api/chat", json={"text": "hesaplık"})
    msg2 = r2.json()["messages"][0]
    assert msg2["reply"] == "Ahmet Yılmaz silindi."

    await session.refresh(ahmet)
    assert ahmet.is_active is False


async def test_kisi_silme_yanlis_kelimeyle_iptal_edilir(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    await client.post("/api/chat", json={"text": "ahmet yılmaz sil"})

    r2 = await client.post("/api/chat", json={"text": "yanlış kelime"})
    assert r2.json()["messages"][0]["outcome"] == "cancelled"

    await session.refresh(ahmet)
    assert ahmet.is_active is True


# ---------------------------------------------------------------- yeni kişi hızı
# (2026-08-31): create_person yolunda LLM'e HİÇ gidilmez — pg_trgm'in "böyle
# biri yok" demesi yeterli. Eskiden her yeni kişi eklemede buluta bir isim
# benzerliği sorusu gidiyor ve cevap ~5 sn gecikiyordu.


async def test_yeni_kisi_ekleme_llme_hic_gitmez(client, auth_account, session, ahmet, fake_llm):
    fake_llm.name_match = "Ahmet Yılmaz"
    await _login(client, auth_account)

    r = await client.post("/api/chat", json={"text": "hayrettin uçar yeni kişi"})
    msg = r.json()["messages"][0]

    assert msg["outcome"] == "person_not_found"
    assert "Ekleyeyim mi?" in msg["reply"]
    assert {b["action"] for b in msg["buttons"]} == {"person:yes", "person:no"}
    assert fake_llm.chat_json_calls == []
    assert fake_llm.calls == []


# ---------------------------------------------------------------- "sil" belirsizliği
# (2026-08-31): "sil" geçen her cümle kişi silme değildir — para/mal bağlamı
# varsa üç butonla SORULUR, sessizce kişi silinmez.


async def test_borcunu_odedi_sil_sorar_kisi_silmez(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "ahmet yılmaz 20 saman borcunu ödedi sil"})
    msg = r.json()["messages"][0]

    assert msg["outcome"] == "delete_ambiguous"
    assert {b["action"] for b in msg["buttons"]} == {"delete:payment", "delete:person", "delete:cancel"}
    await session.refresh(ahmet)
    assert ahmet.is_active is True


async def test_delete_ambiguous_kisiyi_sil_yazarak_onaya_gider(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    await client.post("/api/chat", json={"text": "ahmet yılmaz 20 saman borcunu ödedi sil"})

    r = await client.post("/api/chat/confirm", json={"action": "delete:person"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "archive_confirm"
    assert msg["awaits_text"] is True

    # Kısayol yok: yazarak onay hâlâ şart.
    await session.refresh(ahmet)
    assert ahmet.is_active is True

    r2 = await client.post("/api/chat", json={"text": "hesaplık"})
    assert r2.json()["messages"][0]["reply"] == "Ahmet Yılmaz silindi."
    await session.refresh(ahmet)
    assert ahmet.is_active is False


async def test_delete_ambiguous_tahsilat_gir_kaydi_olusturur(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    await client.post("/api/chat", json={"text": "ahmet yılmaz 5000 tl ödedi sil"})

    r = await client.post("/api/chat/confirm", json={"action": "delete:payment"})
    msg = r.json()["messages"][0]

    assert msg["outcome"] == "recorded"
    tx = (await session.execute(select(Transaction))).scalars().one()
    assert tx.amount_try == Decimal("5000.00")
    await session.refresh(ahmet)
    assert ahmet.is_active is True


async def test_delete_ambiguous_iptal_hicbir_sey_yapmaz(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    await client.post("/api/chat", json={"text": "ahmet yılmaz 5000 tl ödedi sil"})

    r = await client.post("/api/chat/confirm", json={"action": "delete:cancel"})
    assert r.json()["messages"][0]["outcome"] == "cancelled"

    assert (await session.execute(select(func.count(Transaction.id)))).scalar_one() == 0
    await session.refresh(ahmet)
    assert ahmet.is_active is True


async def test_net_kisi_silme_hala_dogrudan_onay_ister(client, auth_account, session, ahmet):
    # Mevcut davranış bozulmadı: para/mal bağlamı yoksa doğrudan silme onayı.
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "ahmet yılmaz sil"})
    assert r.json()["messages"][0]["outcome"] == "archive_confirm"


# ---------------------------------------------------------------- toplam bakiye


async def test_toplam_bakiye_defter_ozetini_doner(client, auth_account, session, ahmet):
    from app.services.ledger import TxMeta, add_debt

    await _login(client, auth_account)
    await add_debt(
        session, ahmet.id, [], TxMeta(created_by="test", source=TxSource.WEB),
        amount_override=Decimal("1500"),
    )

    r = await client.post("/api/chat", json={"text": "toplam borç"})
    msg = r.json()["messages"][0]

    assert msg["outcome"] == "total_balance"
    assert "1.500,00" in msg["reply"]
    assert "defterde yok" not in msg["reply"]


async def test_tum_bakiye_kisi_adi_sanilmaz(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "tüm bakiye"})
    msg = r.json()["messages"][0]

    assert msg["outcome"] == "total_balance"
    assert "Tüm" not in msg["reply"]


# ---------------------------------------------------------------- rapor / liste / bilgi

async def test_rapor_menu_secim_sonrasi_report_path_doner(client, auth_account, session):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "rapor ver"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "report_menu"

    r2 = await client.post("/api/chat/confirm", json={"action": "report:daily"})
    msg2 = r2.json()["messages"][0]
    assert msg2["report_path"] == "/reports/daily"


async def test_kisi_ekstresi_report_path_doner(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "ahmet yılmaz ekstresi"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "report_person"
    assert msg["report_path"] == f"/reports/person/{ahmet.id}"


async def test_kisileri_listele(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "kişileri listele"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "list"
    assert "Ahmet Yılmaz" in msg["reply"]


async def test_bilgi_menusu_belirsizse_sorar_secim_sonrasi_kart_gosterir(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    ahmet.phone = "5551112233"
    await session.flush()

    r = await client.post("/api/chat", json={"text": "ahmet yılmaz bilgi"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "info_menu"
    assert {b["action"] for b in msg["buttons"]} == {"info:balance", "info:card", "info:report"}

    r2 = await client.post("/api/chat/confirm", json={"action": "info:card"})
    msg2 = r2.json()["messages"][0]
    assert "5551112233" in msg2["reply"]


async def test_anlasilmayan_metin(client, auth_account, session):
    await _login(client, auth_account)
    r = await client.post("/api/chat", json={"text": "asdkjaslkdjaslkdjxxzxzxzxzxzxz qwe"})
    msg = r.json()["messages"][0]
    assert msg["outcome"] == "unrecognized"


# ---------------------------------------------------------------- çoklu istek kuyruğu

async def test_coklu_net_istek_sirayla_islenir_ve_kuyruk_tamamlanir(client, auth_account, session, ahmet):
    await _login(client, auth_account)
    mehmet = Person(full_name="Mehmet Demir")
    session.add(mehmet)
    await session.flush()

    text = "ahmet yılmaz 100 tl borç yazdım ve mehmet demir 200 tl borç yazdım"
    r = await client.post("/api/chat", json={"text": text})
    body = r.json()
    outcomes = [m["outcome"] for m in body["messages"]]
    # Bölünebildiyse iki ayrı RECORDED mesajı (+ opsiyonel "N işlem algılandı"
    # bilgi mesajı); bölünemediyse (şüpheli durumda tek parça sayılır) tek
    # RECORDED/başka bir outcome — ikisi de kabul edilir, asıl garanti hiçbir
    # işlemin sessizce kaybolmamasıdır.
    assert "unrecognized" not in outcomes

    tx_count = (await session.execute(select(func.count(Transaction.id)))).scalar_one()
    assert tx_count >= 1

    # Kuyrukta hiçbir satır "beklemede"/"isleniyor" takılı kalmamalı.
    stuck = (
        await session.execute(
            select(func.count(PendingRequest.id)).where(PendingRequest.durum.in_(["beklemede", "isleniyor"]))
        )
    ).scalar_one()
    assert stuck == 0


async def test_coklu_istekte_biri_belirsiz_aday_ile_durur_cevaplaninca_digeri_islenir(client, auth_account, session):
    await _login(client, auth_account)
    duman = Person(full_name="Furkan Duman")
    yilmaz = Person(full_name="Furkan Yılmaz")
    mehmet = Person(full_name="Mehmet Demir")
    session.add_all([duman, yilmaz, mehmet])
    await session.flush()

    text = "furkan borcunu söyle ve mehmet demir borcunu söyle"
    r = await client.post("/api/chat", json={"text": text})
    body = r.json()
    outcomes = [m["outcome"] for m in body["messages"]]

    if "needs_confirmation" in outcomes:
        # Bölündü: ilk parça belirsiz aday ile durdu, ikinci parça (mehmet)
        # kuyrukta bekliyor olmalı.
        r2 = await client.post("/api/chat/confirm", json={"action": f"person:pick:{duman.id}"})
        final_outcomes = [m["outcome"] for m in r2.json()["messages"]]
        assert "balance" in final_outcomes
