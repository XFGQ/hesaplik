import pytest

from app.services import request_queue as rq
from app.services.request_queue import RequestQueueError

CHAT = "12345"


async def test_create_batch_dort_metin_dort_beklemede_kayit(session):
    batch_id, kayitlar = await rq.create_batch(
        session,
        CHAT,
        [
            "mehmetten 5000 aldım",
            "aliye 500 mal gitti",
            "ahmet 30 balya saman 2000tl",
            "veliden 100 tahsil ettim",
        ],
    )

    assert len(kayitlar) == 4
    assert {k.batch_id for k in kayitlar} == {batch_id}
    assert {k.chat_id for k in kayitlar} == {CHAT}
    assert [k.durum for k in kayitlar] == [rq.BEKLEMEDE] * 4
    assert [k.sira_no for k in kayitlar] == [1, 2, 3, 4]
    assert kayitlar[0].raw_text == "mehmetten 5000 aldım"
    assert kayitlar[3].raw_text == "veliden 100 tahsil ettim"
    assert all(k.sonuc is None and k.hata is None for k in kayitlar)


async def test_create_batch_bos_ve_bosluklu_metinleri_atlar(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["  ilk istek ", "", "   ", "ikinci"])

    assert [k.raw_text for k in kayitlar] == ["ilk istek", "ikinci"]
    assert [k.sira_no for k in kayitlar] == [1, 2]


async def test_create_batch_bos_liste_reddedilir(session):
    with pytest.raises(RequestQueueError, match="Boş istek"):
        await rq.create_batch(session, CHAT, ["", "   "])


async def test_ayri_batchler_ayri_batch_id_alir(session):
    b1, _ = await rq.create_batch(session, CHAT, ["a"])
    b2, _ = await rq.create_batch(session, CHAT, ["b"])

    assert b1 != b2


async def test_next_pending_en_kucuk_sira_noyu_doner(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["birinci", "ikinci", "üçüncü"])

    nxt = await rq.next_pending(session, CHAT)
    assert nxt is not None
    assert nxt.id == kayitlar[0].id
    assert nxt.sira_no == 1
    assert nxt.raw_text == "birinci"


async def test_next_pending_tamamlaninca_sonrakine_gecer(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["birinci", "ikinci", "üçüncü"])

    ilk = await rq.next_pending(session, CHAT)
    await rq.mark(session, ilk.id, rq.TAMAMLANDI, sonuc="kaydedildi")

    ikinci = await rq.next_pending(session, CHAT)
    assert ikinci.sira_no == 2
    assert ikinci.raw_text == "ikinci"

    await rq.mark(session, ikinci.id, rq.BASARISIZ, hata="anlaşılamadı")

    ucuncu = await rq.next_pending(session, CHAT)
    assert ucuncu.sira_no == 3


async def test_next_pending_isleniyor_olani_atlar(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["birinci", "ikinci"])

    # Onay bekleyen istek 'işleniyor' kalır; kuyruk ondan sonrakini vermez
    # demiyoruz — 'beklemede' olmadığı için sıradaki 'beklemede' döner.
    await rq.mark(session, kayitlar[0].id, rq.ISLENIYOR)

    nxt = await rq.next_pending(session, CHAT)
    assert nxt.id == kayitlar[1].id


async def test_next_pending_bos_kuyrukta_none_doner(session):
    assert await rq.next_pending(session, CHAT) is None


async def test_next_pending_hepsi_bitince_none_doner(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["birinci", "ikinci"])
    for k in kayitlar:
        await rq.mark(session, k.id, rq.TAMAMLANDI)

    assert await rq.next_pending(session, CHAT) is None


async def test_next_pending_baska_chatin_istegini_dondurmez(session):
    await rq.create_batch(session, "99999", ["başkasının isteği"])

    assert await rq.next_pending(session, CHAT) is None


async def test_mark_durum_gecisleri(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["mehmetten 5000 aldım"])
    req = kayitlar[0]
    assert req.durum == rq.BEKLEMEDE

    isleniyor = await rq.mark(session, req.id, rq.ISLENIYOR)
    assert isleniyor.durum == rq.ISLENIYOR
    assert isleniyor.sonuc is None

    tamam = await rq.mark(session, req.id, rq.TAMAMLANDI, sonuc="5.000 TL tahsilat eklendi")
    assert tamam.durum == rq.TAMAMLANDI
    assert tamam.sonuc == "5.000 TL tahsilat eklendi"


async def test_mark_hata_yazar(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["anlaşılmaz bir cümle"])

    req = await rq.mark(session, kayitlar[0].id, rq.BASARISIZ, hata="anlaşılamadı")

    assert req.durum == rq.BASARISIZ
    assert req.hata == "anlaşılamadı"


async def test_mark_updated_at_yenilenir(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["bir istek"])
    await session.commit()
    onceki = kayitlar[0].updated_at

    req = await rq.mark(session, kayitlar[0].id, rq.ISLENIYOR)
    await session.commit()

    assert req.updated_at >= onceki


async def test_mark_gecersiz_durum_reddedilir(session):
    _, kayitlar = await rq.create_batch(session, CHAT, ["bir istek"])

    with pytest.raises(RequestQueueError, match="Geçersiz durum"):
        await rq.mark(session, kayitlar[0].id, "yarim")


async def test_mark_olmayan_istek_reddedilir(session):
    with pytest.raises(RequestQueueError, match="bulunamadı"):
        await rq.mark(session, 999999, rq.TAMAMLANDI)


async def test_batch_summary_dogru_sayar(session):
    batch_id, kayitlar = await rq.create_batch(
        session, CHAT, ["bir", "iki", "üç", "dört"]
    )
    await rq.mark(session, kayitlar[0].id, rq.TAMAMLANDI, sonuc="ok")
    await rq.mark(session, kayitlar[1].id, rq.TAMAMLANDI, sonuc="ok")
    await rq.mark(session, kayitlar[2].id, rq.BASARISIZ, hata="anlaşılamadı")

    ozet = await rq.batch_summary(session, batch_id)

    assert ozet.batch_id == batch_id
    assert ozet.toplam == 4
    assert ozet.tamamlandi == 2
    assert ozet.basarisiz == 1
    assert ozet.beklemede == 1
    assert ozet.isleniyor == 0
    assert ozet.iptal == 0


async def test_batch_summary_yeni_batchte_hepsi_beklemede(session):
    batch_id, _ = await rq.create_batch(session, CHAT, ["bir", "iki"])

    ozet = await rq.batch_summary(session, batch_id)

    assert ozet.toplam == 2
    assert ozet.beklemede == 2
    assert ozet.tamamlandi == 0


async def test_batch_summary_baska_batchi_saymaz(session):
    b1, k1 = await rq.create_batch(session, CHAT, ["bir", "iki"])
    _, k2 = await rq.create_batch(session, CHAT, ["üç"])
    await rq.mark(session, k2[0].id, rq.TAMAMLANDI)

    ozet = await rq.batch_summary(session, b1)

    assert ozet.toplam == 2
    assert ozet.tamamlandi == 0


async def test_batch_summary_olmayan_batch_sifir(session):
    ozet = await rq.batch_summary(session, "yok-boyle-bir-batch")

    assert ozet.toplam == 0
    assert ozet.beklemede == 0
