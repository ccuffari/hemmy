"""Verifica dei JWT Supabase (ES256/RS256) per le route FastAPI.

Supabase firma i JWT con chiavi asimmetriche (ES256 di default su progetti nuovi).
Le chiavi pubbliche sono esposte sul JWKS endpoint e vengono cache-ate in memoria.

Configurazione attesa in .env:
    SUPABASE_URL=https://xxxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=eyJ...   (per il DB)

La verifica NON richiede la service_role: usa solo il JWKS pubblico.
"""
from __future__ import annotations

import os
import time
import threading
from typing import Any

import httpx
import jwt  # PyJWT
from fastapi import HTTPException, Request

_SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")

# URL JWKS: standard Supabase GoTrue
_JWKS_URL = (
    os.environ.get("SUPABASE_JWKS_URL")
    or (f"{_SUPABASE_URL}/auth/v1/.well-known/jwks.json" if _SUPABASE_URL else "")
)

# Issuer atteso nel claim 'iss'
_EXPECTED_ISSUER = f"{_SUPABASE_URL}/auth/v1" if _SUPABASE_URL else ""

# Cache JWKS (le chiavi ruotano raramente)
_jwks_lock = threading.Lock()
_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600


def _fetch_jwks() -> list[dict[str, Any]]:
    """Scarica (o riusa dalla cache) il JWKS pubblico di Supabase."""
    now = time.time()
    with _jwks_lock:
        if _jwks_cache["keys"] and (now - _jwks_cache["fetched_at"]) < _JWKS_TTL_SECONDS:
            return _jwks_cache["keys"]
        if not _JWKS_URL:
            raise HTTPException(
                status_code=500,
                detail="SUPABASE_URL non configurata nel backend",
            )
        try:
            r = httpx.get(_JWKS_URL, timeout=5.0)
            r.raise_for_status()
            keys = r.json().get("keys", [])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"JWKS non raggiungibile: {exc}"
            ) from exc
        _jwks_cache["keys"] = keys
        _jwks_cache["fetched_at"] = now
        return keys


def _signing_key_for(token: str) -> Any:
    """Trova la chiave pubblica che corrisponde al 'kid' dell'header JWT."""
    try:
        headers = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"JWT header non valido: {exc}") from exc

    kid = headers.get("kid")
    if not kid:
        raise HTTPException(status_code=401, detail="JWT senza 'kid'")

    for k in _fetch_jwks():
        if k.get("kid") != kid:
            continue
        kty = k.get("kty", "")
        if kty == "EC":
            return jwt.algorithms.ECAlgorithm.from_jwk(k)
        if kty == "RSA":
            return jwt.algorithms.RSAAlgorithm.from_jwk(k)
        raise HTTPException(status_code=401, detail=f"JWKS kty non supportato: {kty}")

    raise HTTPException(status_code=401, detail="Signing key non trovata per questo 'kid'")


def verify_supabase_jwt(token: str) -> dict[str, Any]:
    """Verifica firma, scadenza, issuer e audience. Ritorna il payload."""
    key = _signing_key_for(token)
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256"],       # Supabase moderno
            audience="authenticated",             # claim 'aud' dei JWT utente
            issuer=_EXPECTED_ISSUER or None,
            options={"require": ["exp", "sub"]},
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token scaduto") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Token non valido: {exc}") from exc


def extract_token(request: Request) -> str | None:
    """Bearer header, oppure ?token= per SSE (EventSource non manda header)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    tok = request.query_params.get("token")
    return tok.strip() if tok else None