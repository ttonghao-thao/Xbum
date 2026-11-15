#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.db.session import engine


def hash_file(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def backup(destination):
    root = settings.data_dir.resolve()
    database = Path(engine.url.database).resolve()
    if not database.is_relative_to(root):
        raise ValueError("DATABASE_URL must be inside DATA_DIR for complete backups")
    destination = Path(destination).resolve()
    if destination.is_relative_to(root):
        raise ValueError("Backup destination must be outside DATA_DIR")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive = destination / f"xbum-{stamp}.tar.gz"
    with open(root / "maintenance.lock", "a+") as lock, tempfile.TemporaryDirectory(prefix="xbum-backup-") as tmp:
        fcntl.flock(lock, fcntl.LOCK_EX)
        snapshot = Path(tmp) / "data"
        snapshot.mkdir(mode=0o700)
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Backup refuses symbolic links")
            if (
                not path.is_file()
                or path == database
                or path.name.endswith((".lock", "-wal", "-shm"))
                or path.name.startswith(".upload-")
            ):
                continue
            target = snapshot / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        target = snapshot / database.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as source, sqlite3.connect(target) as output:
            source.backup(output)
            if (
                output.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or output.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("Database integrity check failed")
        files = {str(p.relative_to(snapshot)): hash_file(p) for p in snapshot.rglob("*") if p.is_file()}
        (snapshot / "backup-manifest.json").write_text(
            json.dumps({"format": 1, "database": str(database.relative_to(root)), "files": files}, indent=2)
        )
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(snapshot, arcname="data")
    os.chmod(archive, 0o600)
    print(json.dumps({"archive": str(archive), "sha256": hash_file(archive), "files": len(files)}))
    return archive


def restore(archive, confirmed=False):
    if not confirmed and input("恢复将替换 DATA_DIR；输入 RESTORE 继续（请先停止 API/Worker）：") != "RESTORE":
        raise ValueError("Restore not confirmed")
    root = settings.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(root / "service.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Stop API and Worker before restoring") from None
        with tempfile.TemporaryDirectory(prefix="xbum-restore-", dir=root.parent) as temp:
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar.getmembers():
                    path = Path(member.name)
                    if (
                        member.issym()
                        or member.islnk()
                        or not (member.isfile() or member.isdir())
                        or path.is_absolute()
                        or ".." in path.parts
                        or not path.parts
                        or path.parts[0] != "data"
                    ):
                        raise ValueError("Unsafe archive member")
                tar.extractall(temp, filter="data")
            incoming = Path(temp) / "data"
            manifest = json.loads((incoming / "backup-manifest.json").read_text())
            for relative, expected in manifest["files"].items():
                path = (incoming / relative).resolve()
                if not path.is_relative_to(incoming) or not path.is_file() or hash_file(path) != expected:
                    raise ValueError("Backup file integrity mismatch")
            db = (incoming / manifest["database"]).resolve()
            expected_db = Path(engine.url.database).resolve()
            if not expected_db.is_relative_to(root) or str(expected_db.relative_to(root)) != manifest["database"]:
                raise ValueError("Backup database location does not match current configuration")
            with sqlite3.connect(db) as connection:
                if (
                    connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or connection.execute("PRAGMA foreign_key_check").fetchall()
                ):
                    raise ValueError("Backup database is corrupt")
            previous = root.with_name(root.name + ".before-restore-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
            os.replace(root, previous)
            os.replace(incoming, root)
            for path in root.rglob("*"):
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
            os.chmod(root, 0o700)
            print(json.dumps({"restored": str(root), "previous_data": str(previous), "files": len(manifest["files"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("path")
    parser.add_argument("--confirm-restore", action="store_true")
    args = parser.parse_args()
    backup(args.path) if args.action == "backup" else restore(args.path, args.confirm_restore)
