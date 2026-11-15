import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models import Photo
from app.db.session import SessionLocal
from app.main import app
from conftest import upload
from test_auth_privacy import invite


def save_collection(client, photo_ids, title="合成共享素材集合"):
    response = client.post("/api/v1/collections", json={"title": title, "mode": "static", "photo_ids": photo_ids})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_collection_film_respects_only_its_owner_personal_rules_and_keeps_full_grid(client, owner):
    ids = [upload(client, seed)["photo_id"] for seed in [1, 2]]
    with SessionLocal() as db:
        for pid, year in zip(ids, [2024, 2025], strict=True):
            p = db.get(Photo, pid)
            p.taken_at, p.capture_time_source, p.duplicate_group = f"{year}-01-01T03:00:00+00:00", "user", None
        db.commit()
    cid = save_collection(client, ids)
    initial = client.get(f"/api/v1/collections/{cid}/playback").json()
    assert initial["source_total"] == initial["total"] == initial["selected_count"] == 2
    with TestClient(app) as member:
        invite(client, owner, member)
        their_cid = save_collection(member, ids)
        rule = client.post("/api/v1/recommendation-rules", json={
            "scope": "personal", "mode": "never", "target_kind": "date",
            "start_date": "2024-01-01", "end_date": "2025-12-31",
        })
        assert rule.status_code == 200, rule.text
        film = client.get(f"/api/v1/collections/{cid}/playback").json()
        assert film["source_total"] == film["total"] == 2
        assert film["selected_count"] == 0 and film["items"] == [] and film["explanation"]
        assert film["revision"] != initial["revision"]
        grid = client.get(f"/api/v1/collections/{cid}").json()
        assert grid["total"] == 2 and {p["id"] for p in grid["items"]} == set(ids)
        their_film = member.get(f"/api/v1/collections/{their_cid}/playback").json()
        assert their_film["selected_count"] == 2
        assert member.get(f"/api/v1/collections/{cid}/playback").status_code == 404
        assert client.patch(f"/api/v1/photos/{ids[0]}", json={"visibility": "private"}).status_code == 200
        their_film = member.get(f"/api/v1/collections/{their_cid}/playback").json()
        assert their_film["source_total"] == 1
        assert [p["id"] for p in their_film["items"]] == [ids[1]]
        assert client.delete("/api/v1/recommendation-rules/" + rule.json()["id"]).status_code == 200
        restored = client.get(f"/api/v1/collections/{cid}/playback").json()
        assert restored["source_total"] == restored["selected_count"] == 1


def test_collection_film_selection_is_independent_of_grid_pagination(client, owner):
    ids = [upload(client, seed)["photo_id"] for seed in range(27)]
    with SessionLocal() as db:
        for index, pid in enumerate(ids):
            photo = db.get(Photo, pid)
            photo.taken_at, photo.capture_time_source = f"2024-01-{index + 1:02d}T03:00:00+00:00", "user"
            photo.duplicate_group = None
        db.commit()
    cid = save_collection(client, ids)
    grid = client.get(f"/api/v1/collections/{cid}?page=2&page_size=1").json()
    assert grid["total"] == 27 and len(grid["items"]) == 1
    film = client.get(f"/api/v1/collections/{cid}/playback").json()
    assert film["source_total"] == film["total"] == 27
    assert film["selected_count"] == len(film["items"]) == 24
    assert film["items"][0]["id"] == ids[0] and film["items"][-1]["id"] == ids[-1]


def test_feature_migration_has_both_story_narration_unique_constraints(tmp_path):
    path = tmp_path / "album.sqlite3"
    env = {**os.environ, "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{path}"}
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                            cwd=Path(__file__).resolve().parents[3], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(path) as db:
        unique = set()
        for index in db.execute("PRAGMA index_list('story_narrations')").fetchall():
            if index[2]:
                unique.add(tuple(row[2] for row in db.execute(f'PRAGMA index_info("{index[1]}")').fetchall()))
        assert ("story_id", "audio_memory_id") in unique
        assert ("story_id", "photo_id") in unique
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
