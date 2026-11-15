"""Operator commands use isolated workspaces and synthetic data, never the real DATA_DIR."""

import fcntl
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from conftest import PASSWORD, picture
from test_lifecycle import ROOT, Service


@pytest.fixture
def managed(tmp_path):
    service = Service(tmp_path)
    (tmp_path / "scripts/manage.py").write_text((ROOT / "scripts/manage.py").read_text())
    try:
        yield service
    finally:
        service.close()


def cli(service, *args, input=None, env=None):
    return subprocess.run(
        [sys.executable, "scripts/manage.py", *args],
        cwd=service.root,
        env={**service.env, **(env or {})},
        input=input,
        text=True,
        capture_output=True,
        timeout=45,
    )


def populate(service):
    service.launch()
    service.ready()
    with httpx.Client(base_url=service.base, trust_env=False) as client:
        result = client.post(
            "/api/v1/auth/setup",
            json={
                "username": "opsadmin",
                "display_name": "合成运维用户",
                "household_name": "合成测试家庭",
                "password": PASSWORD,
            },
        )
        assert result.status_code == 200, result.text
        client.headers["X-CSRF-Token"] = result.json()["csrf"]
        client.patch("/api/v1/settings", json={"jobs_paused": True, "embedding_enabled": False, "vlm_enabled": False})
        uploaded = client.post("/api/v1/photos/upload", files={"file": ("synthetic.png", picture(), "image/png")})
        assert uploaded.status_code == 200, uploaded.text
        return result.json(), client.cookies.get("xbum_session")


def test_status_all_accounts_and_photos_are_complete_and_sanitized(managed):
    account, token = populate(managed)
    with sqlite3.connect(Path(managed.env["DATA_DIR"]) / "test.sqlite3") as db:
        photo_id = db.execute("SELECT id FROM photos").fetchone()[0]
        db.execute(
            "INSERT INTO embeddings (id, photo_id, model_id, model_version, dimension, data_blob, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "synthetic-vector",
                photo_id,
                "test-only-model",
                "fake-vector-1",
                2,
                b"synthetic-binary-vector",
                "2026-01-01",
            ),
        )
        db.execute(
            "UPDATE jobs SET result_json=?",
            (
                json.dumps(
                    {
                        "diagnostic": "test-only",
                        "vector": [91, 92],
                        "nested": {"token": "test-secret-never-output"},
                    }
                ),
            ),
        )
    result = subprocess.run(
        managed.command("status-all"), cwd=managed.root, env=managed.env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["service"]["running"] and report["api"]["reachable"]
    assert report["database"]["counts"]["users"] == 1
    assert report["database"]["counts"]["photos"] == 1
    assert report["database"]["foreign_key_errors"] == 0
    assert report["database"]["users"][0]["username"] == "opsadmin"
    assert report["database"]["users"][0]["memberships"][0]["role"] == "owner"
    assert report["database"]["tables"]["photos"][0]["visibility"] == "private"
    assert report["database"]["tables"]["embeddings"][0]["model_id"] == "test-only-model"
    assert "test-secret-never-output" not in result.stdout and '"vector"' not in result.stdout
    assert PASSWORD not in result.stdout and token not in result.stdout and account["csrf"] not in result.stdout
    for secret_field in ["password_hash", "token_hash", "csrf", "lease_token", "embedding_blob", "data_blob"]:
        assert f'"{secret_field}"' not in result.stdout
    assert cli(managed, "users").returncode == 0
    photos = json.loads(cli(managed, "photos").stdout)
    assert photos["total"] == 1 and photos["items"][0]["uploader_id"] == account["user"]["id"]
    assert managed.run("stop").returncode == 0
    path = Path(managed.env["DATA_DIR"]) / "test.sqlite3"
    original = path.read_bytes()
    stopped = cli(managed, "status", "--all", "--json")
    assert stopped.returncode == 0
    assert not json.loads(stopped.stdout)["service"]["running"]
    assert path.read_bytes() == original


def test_status_does_not_initialize_a_missing_database(managed):
    result = cli(managed, "status", "--json")
    assert result.returncode == 0, result.stderr
    assert not json.loads(result.stdout)["database"]["initialized"]
    assert not Path(managed.env["DATA_DIR"]).exists()


def test_factory_reset_requires_confirmation_then_removes_all_application_data(managed):
    account, _ = populate(managed)
    root = Path(managed.env["DATA_DIR"])
    media = root / account["household"]["id"]
    assert media.is_dir()
    config = managed.root / ".env"
    config.write_text("APP_ENV=development\n")
    weights = Path(managed.env["MODEL_DIR"])
    weights.mkdir()
    (weights / "synthetic-weight-marker").write_bytes(b"test-not-real-model")
    backups = managed.root / "backups"
    backups.mkdir()
    (backups / "retained").write_bytes(b"test-backup-marker")
    rejected = cli(managed, "factory-reset", input="wrong\n")
    assert rejected.returncode != 0 and "未确认" in rejected.stderr
    assert managed.tree() and media.is_dir()
    reset = subprocess.run(
        managed.command("factory-reset"),
        cwd=managed.root,
        env=managed.env,
        input="RESET_XBUM\n",
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert reset.returncode == 0, reset.stdout + reset.stderr
    assert not media.exists() and config.read_text() == "APP_ENV=development\n"
    assert (weights / "synthetic-weight-marker").read_bytes() == b"test-not-real-model"
    assert (backups / "retained").is_file()
    with sqlite3.connect(root / "test.sqlite3") as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'"):
            assert db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not json.loads(cli(managed, "status", "--json").stdout)["service"]["running"]
    managed.launch()
    managed.ready()
    assert httpx.get(managed.base + "/api/v1/auth/status", trust_env=False).json()["needs_setup"] is True


@pytest.mark.parametrize("bad_target", ["repo", "outside-db", "symlink", "model-overlap", "foreign-database"])
def test_factory_reset_rejects_unsafe_targets(managed, bad_target):
    root = Path(managed.env["DATA_DIR"])
    root.mkdir()
    marker = managed.root / "retained"
    marker.write_bytes(b"never-delete")
    env = {}
    if bad_target == "repo":
        env = {"DATA_DIR": str(managed.root), "DATABASE_URL": f"sqlite:///{managed.root}/test.sqlite3"}
    elif bad_target == "outside-db":
        env = {"DATABASE_URL": f"sqlite:///{managed.root}/outside.sqlite3"}
    elif bad_target == "symlink":
        (root / "link").symlink_to(marker)
    elif bad_target == "model-overlap":
        env = {"MODEL_DIR": str(root / "weights")}
    else:
        with sqlite3.connect(root / "test.sqlite3") as db:
            db.execute("CREATE TABLE unrelated (id INTEGER)")
    result = cli(managed, "factory-reset", "--confirm", "RESET_XBUM", env=env)
    assert result.returncode != 0
    assert marker.read_bytes() == b"never-delete"
    assert not list(Path(managed.env["DATA_DIR"]).resolve().parent.glob("xbum-reset-*"))


def test_factory_reset_refuses_a_standalone_service_lock(managed):
    populate(managed)
    assert managed.run("stop").returncode == 0
    root = Path(managed.env["DATA_DIR"])
    with open(root / "service.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        result = cli(managed, "factory-reset", "--confirm", "RESET_XBUM")
        assert result.returncode != 0 and "单独运行" in result.stderr
    assert json.loads(cli(managed, "users").stdout)["total"] == 1


def test_failed_reset_initialization_preserves_previous_database_and_media(managed):
    account, _ = populate(managed)
    assert managed.run("stop").returncode == 0
    root = Path(managed.env["DATA_DIR"])
    files = {
        str(p.relative_to(root)): p.read_bytes() for p in (root / account["household"]["id"]).rglob("*") if p.is_file()
    }
    (managed.root / "alembic.ini").write_text("[alembic]\nscript_location=missing-migrations\n")
    result = cli(managed, "factory-reset", "--confirm", "RESET_XBUM")
    assert result.returncode != 0
    assert json.loads(cli(managed, "users").stdout)["total"] == 1
    for key, data in files.items():
        assert (root / key).read_bytes() == data
    assert not list(Path(managed.env["DATA_DIR"]).resolve().parent.glob("xbum-reset-*"))


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_reset_installation_failure_does_not_discard_old_data(managed, rollback_fails):
    account, _ = populate(managed)
    assert managed.run("stop").returncode == 0
    script = f"""import sys
sys.path.insert(0, 'scripts')
from pathlib import Path
import manage
original = Path.rename
def fail_install(path, target):
    if path.name == 'fresh' or ({rollback_fails!r} and path.name == 'previous'):
        raise OSError('synthetic installation failure')
    return original(path, target)
Path.rename = fail_install
manage.factory_reset('RESET_XBUM')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=managed.root, env=managed.env, text=True, capture_output=True
    )
    assert result.returncode != 0
    data = Path(managed.env["DATA_DIR"])
    stages = list(Path(managed.env["DATA_DIR"]).resolve().parent.glob("xbum-reset-*"))
    if rollback_fails:
        assert "重置未完成" in result.stderr and len(stages) == 1
        data = stages[0] / "previous"
    else:
        assert not stages
    assert (data / account["household"]["id"]).is_dir()
    with sqlite3.connect(data / "test.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM photos").fetchone()[0] == 1
