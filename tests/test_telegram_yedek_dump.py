"""/yedek ve panelin "Telegram'a gönder"i pg_dump'ı API/bot imajının içinden
çalıştırır. İmajda postgresql-client yoktu: pg_dump bulunamıyor, yedek hiç
alınamıyordu. Gerçek döküm burada ÇALIŞTIRILMAZ; imaj sözleşmesi ve
pg_dump bulunduğunda doğru komutun kurulması kilitlenir."""

from pathlib import Path

from app.config import settings
from app.services import telegram_yedek

KOK = Path(__file__).resolve().parent.parent


def test_calisma_imajinda_postgresql_client_kurulu():
    dockerfile = (KOK / "Dockerfile").read_text()
    son_asama = dockerfile.rsplit("\nFROM ", 1)[1]

    assert "postgresql-client" in son_asama


def test_pg_dump_bulunursa_dogrudan_kullanilir_parola_ortamda(monkeypatch):
    monkeypatch.setattr(
        settings, "database_url", "postgresql+asyncpg://hesaplik:gizli@db:5432/hesaplik"
    )
    monkeypatch.setattr(
        telegram_yedek.shutil, "which", lambda ad: "/usr/bin/pg_dump" if ad == "pg_dump" else None
    )

    argv, env = telegram_yedek._dump_komutu()

    assert argv[0] == "/usr/bin/pg_dump"
    assert ["-h", "db", "-p", "5432", "-U", "hesaplik", "hesaplik"] == argv[-7:]
    assert env["PGPASSWORD"] == "gizli"
    assert "gizli" not in " ".join(argv)  # `ps`'te görünmesin
