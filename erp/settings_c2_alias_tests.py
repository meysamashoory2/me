"""Disposable two-alias SQLite settings for C2 alias-isolation tests only."""

from .settings import *  # noqa: F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
    "c2_alias": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}
