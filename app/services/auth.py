"""Tek hesap, JWT tabanlı kimlik doğrulama.

Giriş yapan HER ŞEYE erişir (defter + admin panel) — ayrı roller yok, tek
kullanıcı adı/şifre çifti `.env`de durur. Şifre asla düz metin
saklanmaz/karşılaştırılmaz: `AUTH_PASSWORD_HASH` bcrypt hash'idir.

İki katı "fail closed" kural:

1. **AUTH_USERNAME / AUTH_PASSWORD_HASH / JWT_SECRET'tan biri boşsa sistem
   tamamen kapalıdır.** Giriş 503 döner, `require_auth` her zaman 401.
   Yapılandırılmamış bir kurulum yanlışlıkla herkese açık olmaz.
2. **Şifre karşılaştırması bcrypt'in kendi sabit-zamanlı `checkpw`'ı ile.**

Token stateless'tır (JWT): sunucu tarafında oturum listesi tutulmaz, bu
yüzden "çıkış yap" salt istemci tarafında token'ı silmektir. JWT_SECRET
değiştirilirse tüm açık tokenlar kendiliğinden geçersiz olur.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

ALGORITHM = "HS256"

# Kaba kuvvet freni: aynı IP'den kısa sürede çok sayıda yanlış şifre
# denemesi kilitlenir. Tek kullanıcılı sistem için bellekte tutmak yeterli
# — süreç yeniden başlarsa sayaç sıfırlanır, kalıcı bir tablo gerekmez.
_MAX_FAILURES = 10
_LOCKOUT_SECONDS = 300
_failures: dict[str, list[float]] = defaultdict(list)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "bilinmeyen"


def locked_out(key: str, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    recent = [t for t in _failures[key] if now - t < _LOCKOUT_SECONDS]
    _failures[key] = recent
    return len(recent) >= _MAX_FAILURES


def record_failure(key: str, now: float | None = None) -> None:
    _failures[key].append(now if now is not None else time.time())


def clear_failures(key: str) -> None:
    _failures.pop(key, None)


def is_enabled() -> bool:
    return bool(settings.auth_username and settings.auth_password_hash and settings.jwt_secret)


def verify_password(candidate: str) -> bool:
    """Adayı bcrypt hash'iyle karşılaştırır. Yapılandırılmamışsa veya hash
    bozuksa her zaman False — asla istisna sızdırıp 500 döndürmez."""
    if not is_enabled():
        return False
    try:
        return bcrypt.checkpw(candidate.encode("utf-8"), settings.auth_password_hash.encode("utf-8"))
    except ValueError:
        return False


def verify_credentials(username: str, password: str) -> bool:
    """Kullanıcı adı VE şifre doğru mu. `verify_password` (gerçek hash'e
    karşı bcrypt) her zaman çalıştırılır — kullanıcı adı yanlış olsa bile —
    ki yanıt süresinden "kullanıcı adı doğru muydu" sızmasın."""
    if not is_enabled():
        return False
    username_ok = hmac.compare_digest(username, settings.auth_username)
    password_ok = verify_password(password)
    return username_ok and password_ok


def create_access_token(username: str, now: datetime | None = None) -> tuple[str, int]:
    """`(token, expires_in_seconds)` döner. Yapılandırılmamışsa çağrılmaz."""
    if not is_enabled():
        raise RuntimeError("auth yapılandırılmamış")
    started = now if now is not None else datetime.now(timezone.utc)
    expires_in = settings.jwt_expire_hours * 3600
    expires_at = started + timedelta(seconds=expires_in)
    payload = {"sub": username, "iat": int(started.timestamp()), "exp": expires_at}
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, expires_in


def decode_token(token: str) -> str:
    """Geçerliyse kullanıcı adını (`sub`) döner. İmza/format/süre hatası,
    ya da sistem yapılandırılmamışsa `ValueError` fırlatır."""
    if not is_enabled():
        raise ValueError("auth yapılandırılmamış")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError as e:
        raise ValueError("geçersiz token") from e
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise ValueError("geçersiz token")
    return sub


_bearer_scheme = HTTPBearer(auto_error=False)


async def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Korumalı uçların bağımlılığı. Geçerliyse kullanıcı adını (JWT `sub`)
    döner — `created_by`/audit alanlarında gerçek kullanıcı adı olarak
    kullanılabilir. Geçersiz/eksik/süresi dolmuş token → 401, sebep
    söylenmez (dışarıya kurulum bilgisi sızmasın)."""
    if creds is None or not creds.credentials:
        raise HTTPException(401, "Yetkisiz")
    try:
        return decode_token(creds.credentials)
    except ValueError as e:
        raise HTTPException(401, "Yetkisiz") from e


# Router'larda okunaklı olsun diye hazır bağımlılık.
AuthRequired = Depends(require_auth)
