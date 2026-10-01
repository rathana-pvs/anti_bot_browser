"""Loopback API authentication for the desktop-managed backend."""

import hmac
import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


TOKEN_HEADER = "X-Manager-Token"


class DesktopSessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("MANAGER_API_TOKEN", "")
        if not expected or request.method == "OPTIONS":
            return await call_next(request)

        provided = request.headers.get(TOKEN_HEADER, "")
        if request.url.path.startswith("/shared_media/") and not provided:
            provided = request.query_params.get("access_token", "")
        if not provided or not hmac.compare_digest(provided, expected):
            return JSONResponse(status_code=403, content={"detail": "Desktop session authorization required"})
        return await call_next(request)
