"""Tek hesap, JWT tabanlı giriş (app/services/auth.py + /api/auth/*).

En kritik davranış: **yapılandırılmamış bir kurulum asla açık kalmaz.**
AUTH_USERNAME/AUTH_PASSWORD_HASH/JWT_SECRET'tan biri boşsa hem giriş hem
`require_auth` her zaman reddeder (fail closed). Şifre düz metin
karşılaştırılmaz — yalnızca bcrypt hash'e karşı doğrulanır.
"""

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.auth import router
from app.config import settings
from app.services import auth
from conftest import AUTH_PASSWORD, AUTH_USERNAME


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _login(client, username=AUTH_USERNAME, password=AUTH_PASSWORD):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------- bcrypt

def test_verify_password_dogru_sifreyi_kabul_eder(auth_account):
    assert auth.verify_password(AUTH_PASSWORD) is True


def test_verify_password_yanlis_sifreyi_reddeder(auth_account):
    assert auth.verify_password("yanlis-sifre") is False


def test_verify_password_yapilandirilmamissa_hep_false(monkeypatch):
    monkeypatch.setattr(settings, "auth_username", "")
    monkeypatch.setattr(settings, "auth_password_hash", "")
    monkeypatch.setattr(settings, "jwt_secret", "")
    assert auth.verify_password(AUTH_PASSWORD) is False


def test_verify_password_bozuk_hash_patlamaz_false_doner(monkeypatch):
    monkeypatch.setattr(settings, "auth_username", AUTH_USERNAME)
    monkeypatch.setattr(settings, "auth_password_hash", "bu-bir-bcrypt-hash-degil")
    monkeypatch.setattr(settings, "jwt_secret", "s")
    assert auth.verify_password(AUTH_PASSWORD) is False


def test_verify_credentials_yanlis_kullanici_adi_reddeder(auth_account):
    assert auth.verify_credentials("baska-kullanici", AUTH_PASSWORD) is False


def test_verify_credentials_dogruysa_gecer(auth_account):
    assert auth.verify_credentials(AUTH_USERNAME, AUTH_PASSWORD) is True


# ---------------------------------------------------------------- JWT üretim/doğrulama

def test_create_ve_decode_token_dogru_kullaniciyi_doner(auth_account):
    token, expires_in = auth.create_access_token(AUTH_USERNAME)
    assert expires_in == settings.jwt_expire_hours * 3600
    assert auth.decode_token(token) == AUTH_USERNAME


def test_decode_token_bozuk_token_reddedilir(auth_account):
    with pytest.raises(ValueError):
        auth.decode_token("bu-bir-jwt-degil")


def test_decode_token_yanlis_secretle_imzalanmis_reddedilir(auth_account):
    yanlis = pyjwt.encode({"sub": AUTH_USERNAME}, "yanlis-secret", algorithm="HS256")
    with pytest.raises(ValueError):
        auth.decode_token(yanlis)


def test_decode_token_suresi_dolmus_reddedilir(auth_account):
    gecmis = datetime.now(timezone.utc) - timedelta(hours=1)
    token = pyjwt.encode(
        {"sub": AUTH_USERNAME, "iat": int(gecmis.timestamp()), "exp": gecmis},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        auth.decode_token(token)


def test_create_access_token_yapilandirilmamissa_patlar(monkeypatch):
    monkeypatch.setattr(settings, "auth_username", "")
    monkeypatch.setattr(settings, "auth_password_hash", "")
    monkeypatch.setattr(settings, "jwt_secret", "")
    with pytest.raises(RuntimeError):
        auth.create_access_token(AUTH_USERNAME)


# ---------------------------------------------------------------- require_auth (Depends)

async def test_require_auth_token_yoksa_401(auth_account):
    with pytest.raises(HTTPException) as exc:
        await auth.require_auth(None)
    assert exc.value.status_code == 401


async def test_require_auth_bozuk_token_401(auth_account):
    from fastapi.security import HTTPAuthorizationCredentials

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="uydurma-token")
    with pytest.raises(HTTPException) as exc:
        await auth.require_auth(creds)
    assert exc.value.status_code == 401


async def test_require_auth_gecerli_token_kullanici_adini_doner(auth_account):
    from fastapi.security import HTTPAuthorizationCredentials

    token, _ = auth.create_access_token(AUTH_USERNAME)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert await auth.require_auth(creds) == AUTH_USERNAME


# ---------------------------------------------------------------- POST /api/auth/login (HTTP)

async def test_login_yapilandirilmamissa_503(client, monkeypatch):
    """Ambient .env'de gerçek bir AUTH_* yapılandırılmış olabilir (yerel
    geliştirme) — bu test kasıtlı olarak boşaltıp "hiç kurulmamış" durumunu
    dener, ortamdan bağımsız olsun diye."""
    monkeypatch.setattr(settings, "auth_username", "")
    monkeypatch.setattr(settings, "auth_password_hash", "")
    monkeypatch.setattr(settings, "jwt_secret", "")
    r = await _login(client)
    assert r.status_code == 503


async def test_login_dogru_bilgilerle_token_doner(client, auth_account):
    r = await _login(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.jwt_expire_hours * 3600
    assert auth.decode_token(body["access_token"]) == AUTH_USERNAME


async def test_login_yanlis_sifre_401(client, auth_account):
    r = await _login(client, password="yanlis")
    assert r.status_code == 401
    # Hangisinin yanlış olduğu (kullanıcı adı mı şifre mi) belli edilmez.
    assert "kullanıcı" in r.json()["detail"].lower() or "şifre" in r.json()["detail"].lower()


async def test_login_yanlis_kullanici_adi_401(client, auth_account):
    r = await _login(client, username="baskasi")
    assert r.status_code == 401


async def test_login_sonrasi_me_calisir(client, auth_account):
    token = (await _login(client)).json()["access_token"]
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == AUTH_USERNAME


async def test_me_tokensiz_401(client, auth_account):
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_cok_fazla_yanlis_deneme_kilitler(client, auth_account):
    for _ in range(10):
        assert (await _login(client, password="yanlis")).status_code == 401

    # Kilitlendikten sonra DOĞRU şifre bile beklemeli.
    r = await _login(client)
    assert r.status_code == 429


async def test_basarili_giris_kilit_sayacini_sifirlar(client, auth_account):
    for _ in range(5):
        assert (await _login(client, password="yanlis")).status_code == 401

    assert (await _login(client)).status_code == 200

    # Sayaç sıfırlandı: 5 yeni yanlış deneme daha kilitlemez (toplam 10 değil).
    for _ in range(5):
        assert (await _login(client, password="yanlis")).status_code == 401
    assert (await _login(client)).status_code == 200
