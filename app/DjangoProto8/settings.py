"""
app.DjangoProto8.settings
Django settings for DjangoProto8 project.
"""

from __future__ import annotations

import os

from DjangoProto8.config import BASE_DIR, load_dotenv, parse_bool, parse_csv

load_dotenv()

APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
DEBUG = parse_bool(
    os.environ.get("DJANGO_DEBUG"), default=(APP_ENV in {"development", "dev", "local"})
)
if DEBUG and APP_ENV in {"production", "prod"}:
    raise RuntimeError("Refusing to start with DJANGO_DEBUG enabled in production.")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-unsafe-secret-key"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY is required when DJANGO_DEBUG is false.")

ALLOWED_HOSTS = parse_csv(os.environ.get("DJANGO_ALLOWED_HOSTS")) or [
    "djangoproto8.glitter.kr",
]
if APP_ENV in {"development", "dev", "local", "test"}:
    for _host in ("127.0.0.1", "localhost"):
        if _host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_host)
if APP_ENV in {"production", "prod"} and not ALLOWED_HOSTS:
    raise RuntimeError("DJANGO_ALLOWED_HOSTS must be set in production.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "authflow",
    "api",
    "thinking",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "api.middleware.ip_block.IPBlockMiddleware",
    "api.middleware.security_headers.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "DjangoProto8.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "DjangoProto8.context_processors.global_template_context",
            ],
        },
    },
]

WSGI_APPLICATION = "DjangoProto8.wsgi.application"
ASGI_APPLICATION = "DjangoProto8.asgi.application"

DB_ENGINE = os.environ.get("APP_DB_ENGINE", "").strip().lower()
if not DB_ENGINE:
    DB_ENGINE = "sqlite" if APP_ENV == "test" else "mysql"

if DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("APP_SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("APP_DB_NAME", ""),
            "USER": os.environ.get("APP_DB_USER", ""),
            "PASSWORD": os.environ.get("APP_DB_PASSWORD", ""),
            "HOST": os.environ.get("APP_DB_HOST", "127.0.0.1"),
            "PORT": int(os.environ.get("APP_DB_PORT", "3306")),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }

if APP_ENV in {"production", "prod"} and DB_ENGINE != "sqlite":
    required_db_vars = ("APP_DB_NAME", "APP_DB_USER", "APP_DB_PASSWORD")
    missing = [name for name in required_db_vars if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing required database environment variables: {', '.join(missing)}"
        )

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("APP_TIME_ZONE", "Asia/Seoul")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = str(BASE_DIR / "staticfiles")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/auth/"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()

CORS_ALLOW_ALL_ORIGINS = parse_bool(os.environ.get("CORS_ALLOW_ALL"), default=False)
CORS_ALLOWED_ORIGINS = parse_csv(os.environ.get("CORS_ALLOWED_ORIGINS"))

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
CSRF_TRUSTED_ORIGINS = parse_csv(os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS")) or [
    "https://djangoproto8.glitter.kr"
]

SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
if APP_ENV == "test":
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

SECURE_HSTS_INCLUDE_SUBDOMAINS = parse_bool(
    os.environ.get("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS"), default=True
)
SECURE_HSTS_PRELOAD = parse_bool(
    os.environ.get("DJANGO_SECURE_HSTS_PRELOAD"), default=True
)
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "djangoproto8-default",
    }
}
