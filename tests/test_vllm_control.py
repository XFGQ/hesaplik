"""vLLM uzaktan aç/kapat ("Yol B", bkz. app/services/vllm_control.py):

- settings.vllm_desired okuma/yazma (bozuk/eksik değer güvenli "off"a düşer),
- is_pending saf mantığı,
- admin uç noktaları (/api/admin/vllm-control — X-Admin-Password, routes.py
  ile aynı doğrudan-çağrı test deseni, bkz. test_admin_llm.py),
- Bosna'nın çektiği token korumalı uç (/api/vllm-desired) — admin şifresinden
  AYRI bir mekanizma, yanlış/eksik/yapılandırılmamış token 401.

Host script'i (scripts/vllm-control.sh) burada test edilmez (entegrasyon,
elle denenecek — bkz. test_admin_backups.py'deki aynı gerekçe restore-apply
için)."""

import pytest
from fastapi import HTTPException

from app.api.routes import admin_vllm_control_status, admin_vllm_control_update, vllm_desired
from app.api.routes import require_vllm_control_token
from app.config import settings
from app.models import AuditLog
from app.schemas import AdminVllmControlIn
from app.services import llm_provider, vllm_control
from sqlalchemy import select


def _stub_reachable(monkeypatch, *, vllm: bool) -> None:
    async def _vllm_healthy():
        return vllm

    monkeypatch.setattr(llm_provider, "vllm_healthy", _vllm_healthy)
    llm_provider.reset_health_cache()


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    client = _FakeClient()


# --------------------------------------------------------------- vllm_desired (DB)


async def test_vllm_desired_hic_ayarlanmamissa_off_doner(session):
    assert await vllm_control.get_vllm_desired(session) == "off"


async def test_vllm_desired_set_sonra_get_dogru_deger_doner(session):
    await vllm_control.set_vllm_desired(session, "on")
    assert await vllm_control.get_vllm_desired(session) == "on"


async def test_vllm_desired_gecersiz_deger_set_edilemez(session):
    with pytest.raises(ValueError):
        await vllm_control.set_vllm_desired(session, "bogus")


async def test_vllm_desired_bozuk_db_degeri_off_sayilir(session):
    from app.models import Setting

    session.add(Setting(key="vllm_desired", value="eski-surum-degeri"))
    await session.flush()
    assert await vllm_control.get_vllm_desired(session) == "off"


# --------------------------------------------------------------- is_pending (saf)


def test_is_pending_on_istenip_erisilemiyorsa_true():
    assert vllm_control.is_pending("on", reachable=False) is True


def test_is_pending_on_istenip_erisiliyorsa_false():
    assert vllm_control.is_pending("on", reachable=True) is False


def test_is_pending_off_istenip_hala_erisiliyorsa_true():
    assert vllm_control.is_pending("off", reachable=True) is True


def test_is_pending_off_istenip_erisilemiyorsa_false():
    assert vllm_control.is_pending("off", reachable=False) is False


# --------------------------------------------------------------- admin uç noktaları


async def test_admin_vllm_control_status_varsayilan_off_ve_erisilemiyor(session, monkeypatch):
    _stub_reachable(monkeypatch, vllm=False)
    out = await admin_vllm_control_status(session=session)
    assert out.desired == "off"
    assert out.reachable is False
    assert out.pending is False


async def test_admin_vllm_control_update_tercihi_db_ye_yazar(session, monkeypatch):
    _stub_reachable(monkeypatch, vllm=False)
    out = await admin_vllm_control_update(
        AdminVllmControlIn(desired="on"), request=_FakeRequest(), session=session
    )
    assert out.desired == "on"
    assert out.reachable is False
    assert out.pending is True   # "on" istendi ama henüz erişilemiyor -> başlatılıyor
    assert await vllm_control.get_vllm_desired(session) == "on"


async def test_admin_vllm_control_update_gecersiz_deger_422(session):
    with pytest.raises(HTTPException) as exc:
        await admin_vllm_control_update(
            AdminVllmControlIn(desired="bogus"), request=_FakeRequest(), session=session
        )
    assert exc.value.status_code == 422


async def test_admin_vllm_control_update_audit_log_yazar(session, monkeypatch):
    _stub_reachable(monkeypatch, vllm=True)
    await admin_vllm_control_update(
        AdminVllmControlIn(desired="on"), request=_FakeRequest(), session=session
    )

    kayit = (await session.execute(select(AuditLog))).scalars().one()
    assert kayit.action == "set_vllm_desired"
    assert kayit.entity == "settings"
    assert kayit.entity_id == "vllm_desired"
    assert kayit.before == {"value": "off"}
    assert kayit.after == {"value": "on"}
    assert "admin-panel@" in kayit.actor


async def test_admin_vllm_control_update_zaten_istenen_durumdaysa_pending_false(session, monkeypatch):
    # "on" isteniyor VE vLLM zaten erişilebiliyor -> geçiş yok, pending False.
    _stub_reachable(monkeypatch, vllm=True)
    out = await admin_vllm_control_update(
        AdminVllmControlIn(desired="on"), request=_FakeRequest(), session=session
    )
    assert out.pending is False


# --------------------------------------------------------------- Bosna'nın çektiği uç (token)


async def test_vllm_control_token_yapilandirilmamissa_401(monkeypatch):
    monkeypatch.setattr(settings, "vllm_control_token", None)
    with pytest.raises(HTTPException) as exc:
        await require_vllm_control_token("herhangi-bir-token")
    assert exc.value.status_code == 401


async def test_vllm_control_token_verilmezse_401(monkeypatch):
    monkeypatch.setattr(settings, "vllm_control_token", "gizli-token")
    with pytest.raises(HTTPException) as exc:
        await require_vllm_control_token(None)
    assert exc.value.status_code == 401


async def test_vllm_control_token_yanlissa_401(monkeypatch):
    monkeypatch.setattr(settings, "vllm_control_token", "gizli-token")
    with pytest.raises(HTTPException) as exc:
        await require_vllm_control_token("yanlis-token")
    assert exc.value.status_code == 401


async def test_vllm_control_token_dogruysa_gecer(monkeypatch):
    monkeypatch.setattr(settings, "vllm_control_token", "gizli-token")
    await require_vllm_control_token("gizli-token")  # exception atmamalı


async def test_vllm_desired_ucu_tercihi_doner(session):
    await vllm_control.set_vllm_desired(session, "on")
    out = await vllm_desired(session=session)
    assert out.desired == "on"
