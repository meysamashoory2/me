import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


class PostgresBatchSafetyTests(SimpleTestCase):
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_postgres_tests.bat"

    def run_guard(self, **values):
        env = os.environ.copy()
        for name in (
            "ERP_POSTGRES_TESTS",
            "POSTGRES_TEST_DB_NAME",
            "POSTGRES_TEST_DB_USER",
            "POSTGRES_TEST_DB_PASSWORD",
            "POSTGRES_TEST_DB_HOST",
            "POSTGRES_TEST_DB_PORT",
        ):
            env.pop(name, None)
        env.update(values)
        return subprocess.run(
            ["cmd", "/c", str(self.script)],
            cwd=self.script.parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def valid_values(self):
        return {
            "ERP_POSTGRES_TESTS": "1",
            "POSTGRES_TEST_DB_NAME": "erp_test_db",
            "POSTGRES_TEST_DB_USER": "erp_test_user",
            "POSTGRES_TEST_DB_PASSWORD": "test-only-placeholder",
            "POSTGRES_TEST_DB_HOST": "127.0.0.1",
            "POSTGRES_TEST_DB_PORT": "5432",
        }

    def test_missing_opt_in_is_rejected_before_django(self):
        values = self.valid_values()
        values.pop("ERP_POSTGRES_TESTS")
        result = self.run_guard(**values)
        self.assertEqual(result.returncode, 2)
        self.assertIn("ERP_POSTGRES_TESTS", result.stdout)

    def test_unsafe_database_names_are_rejected(self):
        for name in ("erp_db", "development"):
            values = self.valid_values()
            values["POSTGRES_TEST_DB_NAME"] = name
            result = self.run_guard(**values)
            self.assertEqual(result.returncode, 2)

    def test_missing_password_and_unexpected_user_are_rejected(self):
        values = self.valid_values()
        values.pop("POSTGRES_TEST_DB_PASSWORD")
        self.assertEqual(self.run_guard(**values).returncode, 2)
        values = self.valid_values()
        values["POSTGRES_TEST_DB_USER"] = "erp_user"
        self.assertEqual(self.run_guard(**values).returncode, 2)

    def test_script_selects_dedicated_settings_keepdb_and_scoped_labels(self):
        text = self.script.read_text(encoding="utf-8")
        self.assertIn("--settings=erp.settings_postgres_tests", text)
        self.assertIn("--keepdb", text)
        self.assertIn("planning.tests_postgresql_reservations", text)
        self.assertIn("production.tests_postgresql_execution", text)
        self.assertNotIn("erp.settings ", text)


class PostgresSettingsSafetyTests(SimpleTestCase):
    def import_settings(self, **overrides):
        env = os.environ.copy()
        for name in (
            "ERP_POSTGRES_TESTS",
            "POSTGRES_TEST_DB_NAME",
            "POSTGRES_TEST_DB_USER",
            "POSTGRES_TEST_DB_PASSWORD",
            "POSTGRES_TEST_DB_HOST",
            "POSTGRES_TEST_DB_PORT",
            "DB_NAME",
            "DB_USER",
            "DB_PASSWORD",
        ):
            env.pop(name, None)
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-c", "import erp.settings_postgres_tests"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def valid_values(self):
        return {
            "ERP_POSTGRES_TESTS": "1",
            "POSTGRES_TEST_DB_NAME": "erp_test_db",
            "POSTGRES_TEST_DB_USER": "erp_test_user",
            "POSTGRES_TEST_DB_PASSWORD": "test-only-placeholder",
            "POSTGRES_TEST_DB_HOST": "127.0.0.1",
            "POSTGRES_TEST_DB_PORT": "5432",
        }

    def test_settings_fail_fast_when_dedicated_configuration_is_missing(self):
        result = self.import_settings(DB_NAME="erp_test_db", DB_USER="erp_test_user")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERP_POSTGRES_TESTS", result.stderr)

    def test_settings_reject_production_target_and_malformed_values(self):
        values = self.valid_values()
        values["POSTGRES_TEST_DB_NAME"] = "erp_db"
        self.assertNotEqual(self.import_settings(**values).returncode, 0)
        values = self.valid_values()
        values["POSTGRES_TEST_DB_HOST"] = "127.0.0.1\\unexpected"
        self.assertNotEqual(self.import_settings(**values).returncode, 0)

    def test_valid_dedicated_configuration_imports_without_connecting(self):
        result = self.import_settings(**self.valid_values())
        self.assertEqual(result.returncode, 0, result.stderr)
