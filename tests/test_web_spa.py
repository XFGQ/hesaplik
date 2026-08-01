"""Üretimde web ve API tek porttan servis edilir (FastAPI, 8100).
Buradaki testler yol ayrımını korur: /api API'nindir, /assets statik
dosyalardır, geri kalan her şey React'in index.html'ine düşer."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.main import _mount_spa

INDEX = "<!doctype html><html><body><div id=root></div></body></html>"


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """Sahte bir `npm run build` çıktısı."""
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX, encoding="utf-8")
    (d / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (d / "manifest.webmanifest").write_text('{"name":"Hesaplık"}', encoding="utf-8")
    monkeypatch.setattr(settings, "web_dist", str(d))
    return d


def _client() -> TestClient:
    """Gerçek app ile aynı sırayla kurulmuş küçük bir uygulama:
    önce API, sonra statik + catch-all."""
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    _mount_spa(app)
    return TestClient(app)


def test_kok_index_html_doner(dist):
    r = _client().get("/")
    assert r.status_code == 200
    assert "<div id=root>" in r.text


def test_react_yolu_index_html_e_duser(dist):
    """/kisiler, /rapor gibi client-side route'lar 404 değil index.html."""
    c = _client()
    for yol in ("/kisiler", "/rapor", "/kisiler/42/defter"):
        r = c.get(yol)
        assert r.status_code == 200, yol
        assert "<div id=root>" in r.text, yol


def test_index_onbellege_alinmaz(dist):
    # Yeni deploy'da eski index, artık var olmayan asset hash'lerini isterdi.
    assert _client().get("/kisiler").headers["cache-control"] == "no-cache"


def test_assets_statik_servis_edilir(dist):
    r = _client().get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.text == "console.log(1)"
    assert "javascript" in r.headers["content-type"]


def test_dist_icindeki_diger_dosyalar(dist):
    r = _client().get("/manifest.webmanifest")
    assert r.status_code == 200
    assert "Hesaplık" in r.text


def test_api_yolu_catch_all_tarafindan_ezilmez(dist):
    r = _client().get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_bilinmeyen_api_yolu_404_json(dist):
    """index.html dönmemeli: istemci 200 + HTML alıp JSON sanmasın."""
    r = _client().get("/api/olmayan-uc-nokta")
    assert r.status_code == 404
    assert "<div id=root>" not in r.text


def test_dist_disina_cikilamaz(dist, tmp_path):
    gizli = tmp_path / "gizli.txt"
    gizli.write_text("parola", encoding="utf-8")
    r = _client().get("/%2E%2E/gizli.txt")
    assert "parola" not in r.text


def test_dist_yoksa_yalnizca_api_calisir(tmp_path, monkeypatch):
    """Geliştirmede build alınmamışsa uygulama çökmemeli."""
    monkeypatch.setattr(settings, "web_dist", str(tmp_path / "yok"))
    c = _client()
    assert c.get("/api/health").json() == {"ok": True}
    assert c.get("/kisiler").status_code == 404
