"""Admin LLM yönetimi: tercih güncelleme (/api/admin/llm).

Route handler'ları burada doğrudan çağrılır (routes.py'de başka hiçbir uç
nokta için de FastAPI TestClient kullanılmıyor, aynı desene uyulur) — bu
yüzden `require_auth`'un FastAPI Depends kablolaması test edilmez, o
app/services/auth.py ve tests/test_auth.py'nin işi. Burada yalnızca
tercih güncelleme mantığı test edilir."""

import pytest
from fastapi import HTTPException

from app.api.routes import admin_llm_status, admin_llm_update
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
