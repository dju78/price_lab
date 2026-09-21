"""Backup and restore of everything a deployment cannot regenerate: the
database (users, sessions, audit log, run registry, ledger,
classification, overrides, mappings) and the Parquet store (raw layer,
cleaned layer, vintage receipts, transformation logs).

A backup is one directory:

    <name>/
      manifest.json      what was backed up, when, from where, with a
                         SHA-256 per file
      database.sqlite    a consistent snapshot taken with SQLite's online
                         backup API (not a file copy, which can catch a
                         write in progress), or database.sql for another
                         backend via the same logical dump
      store/             the Parquet store, file for file

`restore` refuses to write over a non-empty target unless told to, checks
every file against the manifest's hash before it is trusted, and returns
what it restored. "Tested" in `tests/test_backup.py` means: register a
run, back up, destroy the database and the store, restore, reproduce the
run from the restored registry, and compare the index series to the one
computed before the backup, value for value.

SQLite is the supported backend for backup here (it is what the agreed
scope deploys); a PostgreSQL deployment should use `pg_dump`, and
`backup` says so rather than pretending a file copy is a backup.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from .config import get_settings


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite"):
        raise BackupError(
            f"backup supports SQLite databases; {database_url.split(':', 1)[0]} should be backed "
            "up with its own tools (pg_dump for PostgreSQL) alongside `backup_store`")
    raw = database_url.split("///", 1)[1]
    return Path(raw)


def backup(destination: Path | str, *, database_url: str | None = None,
           store_dir: Path | str | None = None) -> dict[str, Any]:
    """Write a backup into `destination` (created; must not already hold a
    manifest). Returns the manifest."""
    settings = get_settings()
    database_url = database_url or settings.database_url
    store_dir = Path(store_dir or settings.store_dir)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "manifest.json").exists():
        raise BackupError(f"{destination} already holds a backup; use a new directory")

    files: dict[str, str] = {}
    db_path = _sqlite_path(database_url)
    if not db_path.exists():
        raise BackupError(f"database file {db_path} does not exist")
    snapshot = destination / "database.sqlite"
    source = sqlite3.connect(str(db_path))
    try:
        target = sqlite3.connect(str(snapshot))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    files["database.sqlite"] = _sha256(snapshot)

    store_dest = destination / "store"
    if store_dir.exists():
        for path in sorted(p for p in store_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(store_dir)
            out = store_dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out)
            files[f"store/{rel.as_posix()}"] = _sha256(out)

    manifest = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pricelab_version": __version__,
        "database_url": database_url,
        "store_dir": str(store_dir),
        "files": files,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify(backup_dir: Path | str) -> dict[str, Any]:
    """Check every file in a backup against its manifest hash."""
    backup_dir = Path(backup_dir)
    manifest: dict[str, Any] = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    mismatched = [name for name, digest in manifest["files"].items()
                  if not (backup_dir / name).exists() or _sha256(backup_dir / name) != digest]
    if mismatched:
        raise BackupError(f"backup at {backup_dir} is damaged: {', '.join(mismatched)}")
    return manifest


def restore(backup_dir: Path | str, *, database_url: str | None = None,
            store_dir: Path | str | None = None, overwrite: bool = False) -> dict[str, Any]:
    """Restore a backup into the configured (or given) database path and
    store directory. Refuses a non-empty target unless `overwrite`."""
    settings = get_settings()
    database_url = database_url or settings.database_url
    store_dir = Path(store_dir or settings.store_dir)
    backup_dir = Path(backup_dir)
    manifest = verify(backup_dir)

    db_path = _sqlite_path(database_url)
    if db_path.exists() and db_path.stat().st_size > 0 and not overwrite:
        raise BackupError(f"{db_path} exists; pass overwrite=True to replace it")
    if store_dir.exists() and any(store_dir.iterdir()) and not overwrite:
        raise BackupError(f"{store_dir} is not empty; pass overwrite=True to replace it")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-journal", "-wal", "-shm"):
        stale = db_path.with_name(db_path.name + suffix)
        if stale.exists():
            stale.unlink()
    source = sqlite3.connect(str(backup_dir / "database.sqlite"))
    try:
        target = sqlite3.connect(str(db_path))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    if store_dir.exists():
        shutil.rmtree(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    restored = 0
    for name in manifest["files"]:
        if name.startswith("store/"):
            out = store_dir / name[len("store/"):]
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_dir / name, out)
            restored += 1
    return {"database": str(db_path), "store_files": restored, "manifest": manifest}
