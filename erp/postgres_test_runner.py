from django.core.exceptions import ImproperlyConfigured
from django.test.runner import DiscoverRunner


class SafePostgresDiscoverRunner(DiscoverRunner):
    def __init__(self, *args, **kwargs):
        if not kwargs.get("keepdb", False):
            raise ImproperlyConfigured(
                "PostgreSQL integration tests require --keepdb for erp_test_db."
            )
        super().__init__(*args, **kwargs)
