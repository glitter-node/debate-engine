from django.http import HttpRequest


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        response = self.get_response(request)
        path = request.path or ""

        response["X-Frame-Options"] = "DENY"
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Permitted-Cross-Domain-Policies"] = "none"
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response["Permissions-Policy"] = (
            "accelerometer=(),camera=(),geolocation=(),gyroscope=(),magnetometer=(),microphone=(),payment=(),usb=(),browsing-topics=()"
        )
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        response["Cross-Origin-Embedder-Policy"] = "require-corp"
        response["Content-Security-Policy"] = self._build_csp()

        if response.status_code >= 400:
            response["Cache-Control"] = "no-store"
            return response

        if path.startswith(("/static/", "/data/")):
            response["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

        response["Cache-Control"] = "no-store"
        return response

    def _build_csp(self) -> str:
        return (
            "default-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "style-src 'self'; "
            "style-src-attr 'none'; "
            "script-src 'self' https://accounts.google.com; "
            "script-src-attr 'none'; "
            "connect-src 'self' https://accounts.google.com; "
            "frame-src 'self' https://accounts.google.com https://accounts.googleusercontent.com; "
            "child-src 'self' https://accounts.google.com https://accounts.googleusercontent.com; "
            "media-src 'self'; "
            "manifest-src 'self'; "
            "worker-src 'self' blob:; "
            "upgrade-insecure-requests"
        )
