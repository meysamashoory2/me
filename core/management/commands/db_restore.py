"""Full PostgreSQL database restore (pg_restore) from a .dump file."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.pg_backup import PgBackupError, restore_pg_backup


class Command(BaseCommand):
    help = "Restore a full PostgreSQL backup (.dump) via pg_restore (drops existing objects)"

    def add_arguments(self, parser):
        parser.add_argument("source", help="Path to the .dump file to restore")
        parser.add_argument(
            "--yes", action="store_true",
            help="Confirm the restore (it replaces the current database contents).",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError(
                "بازیابی محتوای فعلی پایگاه‌داده را جایگزین می‌کند. برای تأیید، --yes را اضافه کنید."
            )
        try:
            restore_pg_backup(options["source"])
        except PgBackupError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS("بازیابی کامل پایگاه‌داده انجام شد."))
