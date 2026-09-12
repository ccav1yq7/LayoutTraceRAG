import hashlib
import secrets
import time

from fastapi import HTTPException, Request
from sqlalchemy import text


class Auth:
    def __init__(self, repository):
        self.repository = repository

    def issue(self, principal, *, token=None, ttl=28800):
        token = token or secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self.repository.engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO sg_http_auth VALUES (:h,:p,:c,:e) ON CONFLICT(token_hash) DO UPDATE SET principal=excluded.principal,csrf=excluded.csrf,expires=excluded.expires"
                ),
                {
                    "h": hashlib.sha256(token.encode()).hexdigest(),
                    "p": principal,
                    "c": csrf,
                    "e": time.time() + ttl,
                },
            )
        return token, csrf

    def identity(self, request: Request):
        authorization = request.headers.get("authorization", "")
        bearer = authorization.startswith("Bearer ")
        token = authorization[7:] if bearer else request.cookies.get("sg_auth", "")
        if not token:
            raise HTTPException(401, detail={"code": "AUTH_REQUIRED"})
        with self.repository.engine.connect() as c:
            row = (
                c.execute(
                    text("SELECT * FROM sg_http_auth WHERE token_hash=:h"),
                    {"h": hashlib.sha256(token.encode()).hexdigest()},
                )
                .mappings()
                .one_or_none()
            )
        if not row or row["expires"] < time.time():
            raise HTTPException(401, detail={"code": "AUTH_EXPIRED"})
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            self.origin(request)
            if not bearer and not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), row["csrf"]
            ):
                raise HTTPException(403, detail={"code": "CSRF_REJECTED"})
        return dict(row)

    def origin(self, request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, detail={"code": "ORIGIN_REJECTED"})
