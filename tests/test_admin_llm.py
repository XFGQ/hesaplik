"""Admin LLM yönetimi: şifre koruması (/api/admin/llm) ve tercih güncelleme.

Route handler'ları burada doğrudan çağrılır (routes.py'de başka hiçbir uç
nokta için de FastAPI TestClient kullanılmıyor, aynı desene uyulur);
require_admin'in FastAPI Depends kablolaması değil, ondan çağrılan saf
_check_admin_password fonksiyonu test edilir."""

import pytest
from fastapi import HTTPException

from app.api.routes import _check_admin_password, admin_llm_status, admin_llm_update
from app.config import settings
from app.schemas import AdminLLMPreferenceIn
from app.services import llm_provider


def _stub_health(monkeypatch, *, vllm: bool, ollama: bool, nvidia: bool = False) -> None:
    async def _vllm_healthy():
        return vllm

    async def _ollama_healthy():
        return ollama

    async def _nvidia_healthy():
        return nvidia

    monkeypatch.setattr(llm_provider, "vllm_healthy", _vllm_healthy)
    monkeypatch.setattr(llm_provider, "ollama_healthy", _ollama_healthy)
    monkeypatch.setattr(llm_provider, "nvidia_healthy", _nvidia_healthy)
    llm_provider.reset_health_cache()


# --------------------------------------------------------------- şifre koruması


def test_admin_sifre_yapilandirilmamissa_503(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", None)
    with pytest.raises(HTTPException) as exc:
        _check_admin_password("herhangi")
    assert exc.value.status_code == 503


def test_admin_sifre_bos_stringse_de_503(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "")
    with pytest.raises(HTTPException) as exc:
        _check_admin_password("herhangi")
    assert exc.value.status_code == 503


def test_admin_sifre_verilmezse_401(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "gizli")
    with pytest.raises(HTTPException) as exc:
        _check_admin_password(None)
    assert exc.value.status_code == 401


def test_admin_sifre_yanlissa_401(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "gizli")
    with pytest.raises(HTTPException) as exc:
        _check_admin_password("yanlis")
    assert exc.value.status_code == 401


def test_admin_sifre_dogruysa_gecer(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "gizli")
    _check_admin_password("gizli")  # exception atmamalı


# --------------------------------------------------------------- durum + güncelleme


async def test_admin_llm_status_aktif_kaynagi_dogru_bildirir(session, monkeypatch):
    _stub_health(monkeypatch, vllm=True, ollama=False)
    out = await admin_llm_status(session=session)
    assert out.primary == "auto"
    assert out.active == "vllm"
    assert out.vllm.ok is True
    assert out.ollama.ok is False
    assert out.nvidia.ok is False


async def test_admin_llm_status_nvidia_saglikliysa_aktif_nvidia_olur(session, monkeypatch):
    _stub_health(monkeypatch, vllm=True, ollama=False, nvidia=True)
    out = await admin_llm_status(session=session)
    assert out.active == "nvidia"
    assert out.nvidia.ok is True


async def test_admin_llm_update_tercihi_db_ye_yazar(session, monkeypatch):
    _stub_health(monkeypatch, vllm=False, ollama=True)
    out = await admin_llm_update(AdminLLMPreferenceIn(llm_primary="ollama"), session=session)

    assert out.primary == "ollama"
    assert out.active == "ollama"
    assert await llm_provider.get_llm_primary(session) == "ollama"


async def test_admin_llm_update_nvidia_zorla_tercihi_db_ye_yazar(session, monkeypatch):
    _stub_health(monkeypatch, vllm=True, ollama=True, nvidia=True)
    out = await admin_llm_update(AdminLLMPreferenceIn(llm_primary="nvidia"), session=session)

    assert out.primary == "nvidia"
    assert out.active == "nvidia"
    assert await llm_provider.get_llm_primary(session) == "nvidia"


async def test_admin_llm_update_gecersiz_deger_422(session):
    with pytest.raises(HTTPException) as exc:
        await admin_llm_update(AdminLLMPreferenceIn(llm_primary="bogus"), session=session)
    assert exc.value.status_code == 422
