"""Strict settings for the isolated ERP PostgreSQL integration database."""

import os

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403


def _required(name):
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise ImproperlyConfigured(f"Required PostgreSQL test setting is missing: {name}")
    if "\\" in value:
        raise ImproperlyConfigured(f"Invalid backslash in PostgreSQL test setting: {name}")
    return value.strip()


if os.environ.get("ERP_POSTGRES_TESTS") != "1":
    raise ImproperlyConfigured("ERP_POSTGRES_TESTS must equal 1.")

_name = _required("POSTGRES_TEST_DB_NAME")
_user = _required("POSTGRES_TEST_DB_USER")
_password = _required("POSTGRES_TEST_DB_PASSWORD")
_host = _required("POSTGRES_TEST_DB_HOST")
_port = _required("POSTGRES_TEST_DB_PORT")

if _name != "erp_test_db" or "test" not in _name.lower() or _name == "erp_db":
    raise ImproperlyConfigured("PostgreSQL tests require the dedicated erp_test_db database.")
if _user != "erp_test_user":
    raise ImproperlyConfigured("PostgreSQL tests require the dedicated erp_test_user role.")

DATABASES = {  # noqa: F405
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _name,
        "USER": _user,
        "PASSWORD": _password,
        "HOST": _host,
        "PORT": _port,
        "OPTIONS": {"connect_timeout": 5},
        "TEST": {"NAME": _name},
    }
}

TEST_RUNNER = "erp.postgres_test_runner.SafePostgresDiscoverRunner"
