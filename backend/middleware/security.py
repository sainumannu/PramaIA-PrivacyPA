"""
Middleware di sicurezza (§55.6, §95, §132):
- request id propagato a log, risposta e audit trail;
- security header;
- rate limiting (in memoria, per singola istanza).
"""
import os
import re
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.utils.request_context import request_id_var

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Accetta un X-Request-ID ben formato dal proxy, altrimenti ne genera uno."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith(_DOCS_PATHS):
            headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if request.url.path.startswith("/api"):
            headers.setdefault("Cache-Control", "no-store")
        forwarded_https = request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        if request.url.scheme == "https" or forwarded_https:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Finestra scorrevole di 60 s per client. Limite generale su /api e più stretto su
    /api/setup. Stato in memoria: con più istanze va affiancato da un limite al reverse proxy.
    """

    def __init__(self, app, default_per_minute: int | None = None, setup_per_minute: int | None = None):
        super().__init__(app)
        self.default_limit = default_per_minute or int(os.getenv("RATE_LIMIT_PER_MINUTE", "600"))
        self.setup_limit = setup_per_minute or int(os.getenv("RATE_LIMIT_SETUP_PER_MINUTE", "20"))
        self.trust_proxy = os.getenv("TRUST_PROXY", "false").lower() == "true"
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._last_purge = time.monotonic()

    def _client(self, request: Request) -> str:
        if self.trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if forwarded:
                return forwarded
        return request.client.host if request.client else "unknown"

    def _purge(self, now: float) -> None:
        if now - self._last_purge < 60:
            return
        self._last_purge = now
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] > 60]:
            del self._hits[key]

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api") or path == "/api/health" or request.method == "OPTIONS":
            return await call_next(request)
        bucket, limit = ("setup", self.setup_limit) if path.startswith("/api/setup") else ("api", self.default_limit)
        now = time.monotonic()
        self._purge(now)
        hits = self._hits[(self._client(request), bucket)]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(60 - (now - hits[0])))
            return JSONResponse(
                {"detail": "Troppe richieste, riprovare tra poco"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
        return await call_next(request)
