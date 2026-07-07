"""Secret-header authentication for the companion.

This is not user authentication - it's origin-gating. Any tab in the browser
can technically fetch() a localhost port, so every request must carry the
secret install.sh generated on first setup. Requests missing it, or carrying
the wrong value, get a 401 rather than being silently processed.

Implemented as ASGI middleware (rather than a per-route dependency) so future
routes (#2, #3, #5) are protected by default - nobody has to remember to add
a dependency to a new endpoint for it to be gated.

/health is exempt so a lightweight liveness check (e.g. from install.sh or a
future menu-bar indicator) doesn't need to know the secret. It intentionally
returns no information beyond "the process is up".
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from companion.config import SECRET_HEADER, read_secret

EXEMPT_PATHS = {"/health"}


class SecretAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        try:
            expected = read_secret()
        except FileNotFoundError:
            # Fail loudly: if there's no secret on disk, install.sh hasn't
            # been run (or the file was deleted). Refuse every request
            # rather than falling back to "no auth".
            return JSONResponse(
                status_code=500,
                content={"detail": "companion secret file is missing - re-run install.sh"},
            )

        provided = request.headers.get(SECRET_HEADER)
        if not provided or provided != expected:
            return JSONResponse(
                status_code=401,
                content={"detail": "missing or invalid secret"},
            )

        return await call_next(request)
