"""Tek hesap girişi (/api/auth/*). Bkz. app/services/auth.py.

Yalnızca iki uç: `POST /login` (token üretir) ve `GET /me` (token geçerli
mi, kullanıcı adı ne). Diğer her şey — defter VE admin — `require_auth`
arkasındadır (bkz. app/api/routes.py, app/api/admin.py).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.services import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeOut(BaseModel):
    username: str


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request):
    """Doğru kullanıcı adı/şifre → JWT. Yanlış → 401, hangisinin yanlış
    olduğu belli edilmez. AUTH_* yapılandırılmamışsa 503 (sistem kapalı)."""
    if not auth.is_enabled():
        raise HTTPException(503, "Kimlik doğrulama yapılandırılmamış")

    key = auth.client_key(request)
    now = time.time()
    if auth.locked_out(key, now):
        raise HTTPException(429, "Çok fazla deneme, biraz sonra tekrar deneyin")

    if not auth.verify_credentials(body.username, body.password):
        auth.record_failure(key, now)
        raise HTTPException(401, "Kullanıcı adı veya şifre hatalı")

    auth.clear_failures(key)
    token, expires_in = auth.create_access_token(body.username)
    return LoginOut(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=MeOut)
async def me(username: str = Depends(auth.require_auth)):
    return MeOut(username=username)
