"""
Django settings for the ERP project.

Values that differ between environments are read from environment variables so
the same code runs locally, in Cloud Agents, and in production. Sensible
development defaults are provided so the project works out of the box.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a project-root .env file (if present) so the documented .env.example
# actually takes effect. Real OS environment variables always win over .env,
# and the sqlite throwaway path stays usable even without python-dotenv.
try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-key-change-me-in-production",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DJANGO_DEBUG", True)

# Intranet hostname for Poliran LAN access (override with DJANGO_ALLOWED_HOSTS).
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    "localhost,127.0.0.1,0.0.0.0,planning.poliran",
)

CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://planning.poliran:8000,http://planning.poliran,https://planning.poliran",
)


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "django_jalali",
    # Local apps
    "accounts",
    "catalog",
    "production",
    "planning",
    "reports",
    "core",
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
]

ROOT_URLCONF = "erp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.user_profile",
                "core.context_processors.table_layout",
            ],
        },
    },
]

WSGI_APPLICATION = "erp.wsgi.application"


# Database
# PostgreSQL is the primary database for both development and production.
# All connection settings are read from environment variables so credentials
# are never hard-coded (set them via the OS environment; see .env.example).
# Set DB_ENGINE=sqlite only for a throwaway, dependency-free local run.
DB_ENGINE = os.environ.get("DB_ENGINE", "postgresql").strip().lower()

if DB_ENGINE in {"sqlite", "sqlite3"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "erp_db"),
            "USER": os.environ.get("DB_USER", "erp_user"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "OPTIONS": {
                # Fail fast with a clear error instead of hanging forever when
                # the database host is unreachable or misconfigured.
                "connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "5")),
            },
        }
    }


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
# The UI is Persian (RTL) with Jalali (Shamsi) dates.

LANGUAGE_CODE = os.environ.get("DJANGO_LANGUAGE_CODE", "fa-ir")
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Tehran")
USE_I18N = True
USE_TZ = True

# Direction helper consumed by templates.
LANGUAGE_BIDI = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Dev/tests use a plain backend so templates render without a prior
# collectstatic run. Production uses WhiteNoise's compressed storage (gzip),
# which serves static efficiently without manifest hashing. We avoid the
# *Manifest* variant because some bundled third-party admin CSS (e.g.
# django-jalali's datepicker) references image files that are not shipped,
# which makes the strict manifest post-processing abort collectstatic.
_staticfiles_backend = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if DEBUG
    else "whitenoise.storage.CompressedStaticFilesStorage"
)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": _staticfiles_backend},
}

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Authentication redirects
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"


# Uploaded files (Excel archives in مدیریت داده‌ها, etc.)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
BACKUP_DEFAULT_DIR = BASE_DIR / "backups"
