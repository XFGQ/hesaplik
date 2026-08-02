"""Admin paneli koruması: tek şifre (.env ADMIN_PASSWORD) + süreli çerez.

Neden JWT/kütüphane değil: tek kullanıcı, tek şifre, tek sunucu. Token
`son_kullanma.imza` biçiminde; imza HMAC-SHA256, anahtarı ADMIN_PASSWORD'ün
kendisi. Şifre değiştirilirse eski tokenlar kendiliğinden geçersiz olur,
ayrıca bir "oturumları kapat" mekanizması gerekmez.

İki katı kural:

1. **ADMIN_PASSWORD boşsa panel tamamen kapalıdır.** Giriş 503 döner, korumalı
   uçlar 401. Yapılandırılmamış bir kurulum yanlışlıkla herkese açık olmaz.
2. **Şifre karşılaştırması sabit zamanlıdır** (`hmac.compare_digest`) —
   zamanlama farkından şifre sızmaz.

Token istemciye httpOnly çerez olarak verilir (JS okuyamaz, XSS ile
çalınamaz); `Authorization: Bearer <token>` başlığı da kabul edilir, komut
satırından/testten kullanmak için.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException, Request

from app.config import settings

COOKIE_NAME = "hesaplik_admin"


class AdminDisabled(Exception):
    """ADMIN_PASSWORD ayarlanmamış — panel kapalı."""


def is_enabled() -> bool:
    return bool(settings.admin_password)


def check_password(candidate: str) -> bool:
    """Şifreyi sabit zamanlı karşılaştırır. Panel kapalıysa her zaman False:
    boş şifre boş girdiyle eşleşip içeri almaz."""
    if not is_enabled():
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), settings.admin_password.encode("utf-8"))


def _sign(expires_at: int) -> str:
    return hmac.new(
        settings.admin_password.encode("utf-8"),
        str(expires_at).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def create_token(now: float | None = None) -> str:
    """`<son_kullanma_epoch>.<imza>`. Panel kapalıyken çağrılmaz."""
    if not is_enabled():
        raise AdminDisabled
    started = int(now if now is not None else time.time())
    expires_at = started + settings.admin_session_hours * 3600
    return f"{expires_at}.{_sign(expires_at)}"


def verify_token(token: str | None, now: float | None = None) -> bool:
    if not token or not is_enabled():
        return False

    raw_expiry, _, signature = token.partition(".")
    if not signature:
        return False
    try:
        expires_at = int(raw_expiry)
    except ValueError:
        return False

    # Önce imza (sabit zamanlı), sonra süre: geçersiz imzalı bir token'ın
    # süresi hakkında bilgi vermeyelim.
    if not hmac.compare_digest(signature, _sign(expires_at)):
        return False
    return expires_at > (now if now is not None else time.time())


def token_max_age() -> int:
    return settings.admin_session_hours * 3600


def extract_token(request: Request) -> str | None:
    """Önce çerez (tarayıcı), sonra Authorization: Bearer (curl/test)."""
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie

    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


async def require_admin(request: Request) -> None:
    """/api/admin/* uçlarının bağımlılığı. Geçersiz/eksik token → 401.
    Hangi sebep olduğu söylenmez (şifre yok mu, süresi mi doldu) — dışarıya
    kurulum bilgisi sızmasın."""
    if not verify_token(extract_token(request)):
        raise HTTPException(401, "Yetkisiz")


# Router'larda okunaklı olsun diye hazır bağımlılık.
AdminRequired = Depends(require_admin)


def new_password_hint() -> str:
    """Kurulum kolaylığı: .env'e yazılabilecek rastgele bir şifre önerir.
    Yalnızca CLI/dokümantasyon için, hiçbir uçtan dönmez."""
    return secrets.token_urlsafe(18)
