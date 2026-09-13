"""Native, full-database PostgreSQL backup/restore via pg_dump / pg_restore.

This is the professional backup path for the PostgreSQL deployment: a single
consistent dump of the *entire* database (schema + all data, including tables
not listed in the sectional JSON backup), restorable with pg_restore. The
sectional JSON backup in ``core.backup`` remains for selective/portable exports.

Credentials are taken from the active database settings; the password is passed
to the tools via the PGPASSWORD environment variable (never on the command line).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone


class PgBackupError(RuntimeError):
    """Raised for any backup/restore problem, with a user-facing Persian message."""


def is_postgres() -> bool:
    return "postgresql" in (settings.DATABASES.get("default", {}).get("ENGINE", "") or "")


def _conn() -> dict[str, str]:
    if not is_postgres():
        raise PgBackupError(
            "پشتیبان کامل پایگاه‌داده فقط برای PostgreSQL است (DB_ENGINE=postgresql)."
        )
    db = settings.DATABASES["default"]
    name = db.get("NAME")
    if not name:
        raise PgBackupError("نام پایگاه‌داده تنظیم نشده است.")
    return {
        "name": str(name),
        "user": str(db.get("USER") or ""),
        "password": str(db.get("PASSWORD") or ""),
        "host": str(db.get("HOST") or "127.0.0.1"),
        "port": str(db.get("PORT") or "5432"),
    }


def _tool(name: str) -> str:
    """Locate pg_dump/pg_restore: PG_BIN_DIR setting/env, PATH, then Windows installs."""
    exe = name + (".exe" if os.name == "nt" else "")
    for base in (getattr(settings, "PG_BIN_DIR", "") or "", os.environ.get("PG_BIN_DIR") or ""):
        if base:
            candidate = Path(base) / exe
            if candidate.is_file():
                return str(candidate)
    found = shutil.which(name) or shutil.which(exe)
    if found:
        return found
    if os.name == "nt":
        for root in (Path(r"C:\Program Files\PostgreSQL"), Path(r"C:\Program Files (x86)\PostgreSQL")):
            if root.is_dir():
                for ver in sorted(root.iterdir(), reverse=True):
                    candidate = ver / "bin" / exe
                    if candidate.is_file():
                        return str(candidate)
    raise PgBackupError(
        f"ابزار «{name}» پیدا نشد. PostgreSQL را نصب کنید یا مسیر پوشهٔ bin آن را "
        "در متغیر محیطی PG_BIN_DIR قرار دهید."
    )


def _env(conn: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    if conn["password"]:
        env["PGPASSWORD"] = conn["password"]
    return env


def suggested_filename() -> str:
    return "erp-db-" + timezone.now().strftime("%Y%m%d-%H%M%S") + ".dump"


def _resolve_dest(raw: str) -> Path:
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise PgBackupError("آدرس مقصد را وارد کنید.")
    path = Path(text).expanduser()
    if path.suffix.lower() in {".dump", ".sql", ".backup"}:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    path.mkdir(parents=True, exist_ok=True)
    return path / suggested_filename()


@dataclass
class PgBackupResult:
    path: str
    size: int


def create_pg_backup(dest: str) -> PgBackupResult:
    """Dump the whole database to a custom-format (.dump) file."""
    conn = _conn()
    dest_file = _resolve_dest(dest)
    cmd = [
        _tool("pg_dump"), "-Fc", "--no-owner", "--no-privileges",
        "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
        "-d", conn["name"], "-f", str(dest_file),
    ]
    proc = subprocess.run(cmd, env=_env(conn), capture_output=True, text=True)
    if proc.returncode != 0:
        raise PgBackupError("pg_dump ناموفق بود: " + (proc.stderr or "").strip()[:600])
    return PgBackupResult(path=str(dest_file), size=dest_file.stat().st_size)


def restore_pg_backup(source: str) -> None:
    """Restore the whole database from a .dump, dropping existing objects first."""
    conn = _conn()
    src = Path(str(source or "").strip().strip('"').strip("'")).expanduser()
    if not src.is_file():
        raise PgBackupError("پرونده پشتیبان پیدا نشد.")
    cmd = [
        _tool("pg_restore"), "--clean", "--if-exists", "--no-owner", "--no-privileges",
        "-h", conn["host"], "-p", conn["port"], "-U", conn["user"],
        "-d", conn["name"], str(src),
    ]
    proc = subprocess.run(cmd, env=_env(conn), capture_output=True, text=True)
    if proc.returncode != 0:
        raise PgBackupError("pg_restore ناموفق بود: " + (proc.stderr or "").strip()[:800])
