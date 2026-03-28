"""
Django settings for school_erp_demo project.

Production-ready base configuration using environment variables.
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialise environment variables
env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])


# Application definition — django-tenants multi-tenancy
# SHARED_APPS: public schema. TENANT_APPS: per-school schema.
SHARED_APPS = [
    "django_tenants",
    "apps.customers",
    "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tailwind",
    "theme",
    "apps.core",
    "apps.reports",
    "apps.accounts",
    "apps.notifications",
]

TENANT_APPS = [
    "apps.school_data",
    "apps.timetable",
    "apps.payroll",
]

INSTALLED_APPS = list(SHARED_APPS) + [a for a in TENANT_APPS if a not in SHARED_APPS]

# Tenant model (School) and domain model
TENANT_MODEL = "customers.School"  # app_label.Model
TENANT_DOMAIN_MODEL = "customers.Domain"

# When no domain matches (e.g. 127.0.0.1, localhost), use public schema for main site
SHOW_PUBLIC_IF_NO_TENANT_FOUND = True

# Database router for tenant/shared schema sync
DATABASE_ROUTERS = [
    "django_tenants.routers.TenantSyncRouter",
]
NPM_BIN_PATH = r"C:\Program Files\nodejs\npm.cmd"

MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.TenantSchemaFromUserMiddleware",
    "apps.core.middleware.SchoolFeaturesMiddleware",
    "apps.core.middleware.TrialExpiryMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "school_erp_demo.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "school_erp_demo.wsgi.application"
ASGI_APPLICATION = "school_erp_demo.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


# Database — PostgreSQL only (production config)
# No SQLite fallback. All credentials must be set in .env.
# Fails loudly if any DB_* variable is missing or empty.
# CONN_MAX_AGE: connection pooling for better concurrency.
# ATOMIC_REQUESTS: each request runs in a transaction.
def _get_db_config(key: str) -> str:
    val = env.str(key, default="")
    if not val or not str(val).strip():
        raise ImproperlyConfigured(
            f"PostgreSQL credential {key} is required. Set {key} in .env. "
            "See .env.example for template."
        )
    return str(val).strip()


DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": _get_db_config("DB_NAME"),
        "USER": _get_db_config("DB_USER"),
        "PASSWORD": _get_db_config("DB_PASSWORD"),
        "HOST": _get_db_config("DB_HOST"),
        "PORT": _get_db_config("DB_PORT"),
        "CONN_MAX_AGE": 60,
        "ATOMIC_REQUESTS": True,
    }
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Tailwind configuration
TAILWIND_APP_NAME = "theme"
INTERNAL_IPS = ["127.0.0.1"]


# Static files (CSS, JavaScript, Images)
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Custom user model
AUTH_USER_MODEL = "accounts.User"

# CSRF: allow localhost and common subdomains for development
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:8000", "http://127.0.0.1:8000"],
)
