"""Django settings for the Jastip proxy-purchasing platform."""

from pathlib import Path

import dj_database_url
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Core -------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:8019,http://127.0.0.1:8019")

SITE_ID = 1
SITE_NAME = "Jastip.me"
SITE_DOMAIN = os.getenv("SITE_DOMAIN", "localhost:8019")

# --- Applications -----------------------------------------------------------
INSTALLED_APPS = [
    # Unfold must precede django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.sitemaps",
    # Third-party
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "meta",
    # Local
    "apps.accounts",
    "apps.trips",
    "apps.blog",
    "apps.pages",
    "apps.notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "apps.pages.context_processors.site_globals",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---------------------------------------------------------------
# Uses DATABASE_URL when set (Postgres on staging/prod). Falls back to SQLite
# locally so development runs without DB provisioning. Run scripts/setup_db.sh
# to provision local Postgres, then set DATABASE_URL in .env.
DATABASES = {
    "default": dj_database_url.parse(
        os.getenv("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

# --- Auth -------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# allauth (v65 style)
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"
ACCOUNT_RATE_LIMITS = {"login_failed": "5/5m"}
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3
LOGIN_REDIRECT_URL = "accounts:profile"
LOGOUT_REDIRECT_URL = "pages:home"
LOGIN_URL = "account_login"

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
    }
}
SOCIALACCOUNT_LOGIN_ON_GET = True

# --- I18N -------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Jakarta")
USE_I18N = True
USE_TZ = True

# --- Static / media ---------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email ------------------------------------------------------------------
if env_bool("EMAIL_USE_SMTP", False):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("EMAIL_HOST", "")
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "admin@jastip.me")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Jastip.me <admin@jastip.me>")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@jastip.me")

# --- Business config --------------------------------------------------------
PLATFORM_COMMISSION_PERCENT = float(os.getenv("PLATFORM_COMMISSION_PERCENT", "2.5"))

# --- AdSense ----------------------------------------------------------------
# e.g. "ca-pub-1234567890123456"; blank disables the script + ad slots.
ADSENSE_CLIENT = os.getenv("ADSENSE_CLIENT", "")

# --- WhatsApp bot -----------------------------------------------------------
WHATSAPP_BOT_URL = os.getenv("WHATSAPP_BOT_URL", "http://127.0.0.1:8090")
WHATSAPP_BOT_TOKEN = os.getenv("WHATSAPP_BOT_TOKEN", "change-me")
WHATSAPP_ENABLED = env_bool("WHATSAPP_ENABLED", False)

# --- Security (prod) --------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# --- Unfold admin theme -----------------------------------------------------
from config.unfold import UNFOLD  # noqa: E402,F401
