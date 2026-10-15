"""Consistent, offline-restorable backup. Protect the output: it contains secrets.

Usage: python scripts/backup.py --data data --output /secure/path/backup.tar.gz
Keep backups outside the public web directory. Restore only while service is stopped.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = args.data.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        parser.error("Output exists; choose a new backup name")
    if not (data / "master.key").is_file():
        parser.error("master.key missing; retain your external CONFIG_ENCRYPTION_KEY separately")
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with tempfile.TemporaryDirectory(dir=args.output.parent, prefix=".tg-backup-") as tmp, os.fdopen(fd, "wb") as out:
        snapshot = Path(tmp) / "tg_search.db"
        with closing(sqlite3.connect(f"file:{data / 'tg_search.db'}?mode=ro", uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        with tarfile.open(fileobj=out, mode="w:gz") as archive:
            archive.add(snapshot, arcname="data/tg_search.db")
            archive.add(data / "master.key", arcname="data/master.key")
    print(f"Backup saved: {args.output}. Contains encrypted Telegram session and its key; store securely.")


if __name__ == "__main__":
    main()
