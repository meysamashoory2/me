"""Full PostgreSQL database backup (pg_dump, custom format)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.backup import default_backup_dir
from core.pg_backup import PgBackupError, create_pg_backup


class Command(BaseCommand):
    help = "Create a full PostgreSQL backup (.dump) via pg_dump"

    def add_arguments(self, parser):
        parser.add_argument(
            "dest", nargs="?", default=None,
            help="Destination folder or .dump file (default: <BASE_DIR>/backups)",
        )

    def handle(self, *args, **options):
        dest = options.get("dest") or str(default_backup_dir())
        try:
            result = create_pg_backup(dest)
        except PgBackupError as exc:
            raise CommandError(str(exc))
        mb = result.size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f"پشتیبان کامل ذخیره شد: {result.path} ({mb:.2f} MB)"))
