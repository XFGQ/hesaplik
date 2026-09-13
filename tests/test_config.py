"""Telegram yönetici ayarlarının tipi: Telegram chat id'yi int verir,
.env ise her şeyi metin olarak okur. Karşılaştırma str/int uyuşmazlığına
takılırsa yönetici komutları (/durum, /yedek) sessizce kimseye çalışmaz."""

from app.config import Settings


def test_admin_chat_id_envden_int_olarak_okunur(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", " 4242 ")
    ayarlar = Settings()

    assert ayarlar.telegram_admin_chat_id_int == 4242
    assert isinstance(ayarlar.telegram_admin_chat_id_int, int)


def test_grup_chat_idsi_negatif_olabilir(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "-1001234567890")

    assert Settings().telegram_admin_chat_id_int == -1001234567890


def test_admin_chat_id_bos_ya_da_bozuksa_none(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "")
    assert Settings().telegram_admin_chat_id_int is None

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "@yonetici")
    assert Settings().telegram_admin_chat_id_int is None


def test_admin_ids_int_listesi_bozuk_parca_atlanir(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", " 11, 22 ,abc,,")
    idler = Settings().telegram_admin_ids_list

    assert idler == [11, 22]
    assert all(isinstance(i, int) for i in idler)
