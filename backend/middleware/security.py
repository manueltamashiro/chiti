"""
middleware/security.py — Security headers middleware.

Adds CSP, X-Frame-Options, HSTS and other headers to every HTTP response.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to every response:
    - Content-Security-Policy
    - X-Frame-Options
    - X-Content-Type-Options
    - Referrer-Policy
    - Strict-Transport-Security (behind Cloudflare TLS)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' blob:; "   # Monaco needs blob:
            "style-src 'self' 'unsafe-inline'; "           # Tailwind needs inline
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self' ws: wss:; "               # WebSocket
            "worker-src blob:;"                            # Monaco web workers
        )
        response.headers["X-Powered-By"] = ""  # remove framework fingerprint
        return response
