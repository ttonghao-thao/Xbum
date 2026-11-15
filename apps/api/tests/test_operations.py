import importlib.util
import io
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.models import Household
from app.db.session import SessionLocal, engine
from app.services.storage import storage

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("backup_restore", ROOT / "scripts" / "backup_restore.py")
backup_restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_restore)


def test_backup_restore_integrity_and_media_roundtrip(tmp_path):
    with SessionLocal() as db:
        h = Household(name="备份测试")
        db.add(h)
        db.commit()
        hid = h.id
    key = f"{hid}/test.bin"
    storage.put_original(key, b"non-personal-synthetic-bytes")
    archive = backup_restore.backup(tmp_path / "backups")
    with SessionLocal() as db:
        db.get(Household, hid).name = "修改后的名称"
        db.commit()
    engine.dispose()
    backup_restore.restore(archive, True)
    with SessionLocal() as db:
        assert db.get(Household, hid).name == "备份测试"
    assert storage.read_media(key).read_bytes() == b"non-personal-synthetic-bytes"
    assert archive.stat().st_mode & 0o777 == 0o600


def test_restore_rejects_traversal_and_links(tmp_path):
    for name, kind in [("data/../../escape", tarfile.REGTYPE), ("data/link", tarfile.SYMTYPE)]:
        archive = tmp_path / ("bad-" + str(len(name)) + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = "/etc"
            info.size = 0
            tar.addfile(info, io.BytesIO())
        with pytest.raises(ValueError, match="Unsafe archive"):
            backup_restore.restore(archive, True)


def test_restore_refuses_running_services(client, tmp_path):
    with pytest.raises(ValueError, match="Stop API and Worker"):
        backup_restore.restore(tmp_path / "does-not-matter.tar.gz", True)


def test_real_alembic_upgrade_downgrade_upgrade(tmp_path):
    database = tmp_path / "migrations" / "album.sqlite3"
    env = {**os.environ, "DATA_DIR": str(database.parent), "DATABASE_URL": f"sqlite:///{database}"}
    for command in [("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head"), ("check",)]:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *command], cwd=ROOT, env=env, text=True, capture_output=True
        )
        assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(database) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"photos", "faces", "jobs", "story_slides", "audio_memories", "alembic_version"} <= tables
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_inference_endpoint_cannot_target_external_hosts():
    for url in ["https://api.example.com", "http://192.168.1.20:11434", "https://localhost:11434"]:
        with pytest.raises(ValueError):
            Settings(ollama_base_url=url)
    assert Settings(ollama_base_url="http://127.0.0.1:11434").ollama_base_url.endswith("11434")


def test_live_model_contract_and_network_defaults():
    from app.ai.providers import SigLIP2, normalize
    import numpy as np

    assert np.linalg.norm(normalize([3.0, 4.0])) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        normalize([float("nan")])
    with pytest.raises(ValueError):
        normalize([0, 0])
    assert SigLIP2.dimension == 768
