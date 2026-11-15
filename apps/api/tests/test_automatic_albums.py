from datetime import date, datetime
from zoneinfo import ZoneInfo
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Event, EventPhoto, Household, Photo, PhotoAnalysis, PlaylistItem
from app.db.session import Base, SessionLocal, engine
from app.main import app
from app.services.calendar import day_distance
from app.services.recommendations import build_daily_playlist
from conftest import drain, fake_provider, setup, upload


def set_photo(db, pid, taken=None, event_type="daily", child=False, confidence=0.9):
    photo = db.get(Photo, pid)
    photo.taken_at, photo.capture_time_source = taken, "user" if taken else "upload_uncertain"
    analysis = db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == pid))
    analysis.analysis_json = {
        "candidate_event_type": event_type,
        "confidence": confidence,
        "scene_tags": ["儿童玩耍"] if child else ["静物"],
        "objects": [],
        "visible_activity": "小朋友在玩耍" if child else "没有儿童的风景",
        "caption": "测试合成图",
    }
    analysis.status, analysis.quality_score = "succeeded", 0.8


def album_ids(client, kind, suffix=""):
    response = client.get("/api/v1/albums/" + kind + (suffix + "&" if suffix else "?") + "scope=family")
    assert response.status_code == 200, response.text
    return [p["id"] for p in response.json()["items"]]


def test_on_this_day_is_a_complete_date_collection_not_a_playlist(client, owner, monkeypatch):
    ids = [upload(client, i)["photo_id"] for i in range(7)]
    with SessionLocal() as db:
        for i, pid in enumerate(ids):
            set_photo(
                db,
                pid,
                [
                    "2024-11-14T16:01:00+00:00",
                    "2023-11-15T03:00:00+00:00",
                    "2024-11-16T03:00:00+00:00",
                    "2026-11-15T03:00:00+00:00",
                    None,
                    "2022-10-15T03:00:00+00:00",
                    "2025-11-15T01:00:00+00:00",
                ][i],
            )
        db.get(Photo, ids[6]).is_hidden = True
        db.commit()
    day = "?day=2026-11-15"
    assert set(album_ids(client, "on_this_day", day)) == set(ids[:3])
    page = client.get("/api/v1/albums/on_this_day" + day + "&page_size=1&page=2").json()
    assert page["total"] == 3 and page["page"] == 2 and len(page["items"]) == 1
    assert page["match_window_days"] == 3 and "年前的今天" in page["items"][0]["album_reason"]
    monkeypatch.setattr(
        "app.services.albums.local_now", lambda _: datetime(2026, 11, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    summaries = client.get("/api/v1/albums").json()["items"]
    assert summaries[0]["total"] == 3 and summaries[0]["cover"]["id"] in ids[:3]
    # No-match is empty, never the whole photo library.
    assert album_ids(client, "on_this_day", "?day=2026-07-01") == []
    client.patch("/api/v1/photos/" + ids[0], json={"visibility": "private"})
    client.delete("/api/v1/photos/" + ids[1])
    assert album_ids(client, "on_this_day", day) == [ids[2]]


def test_anniversary_nearby_year_boundary_leap_and_unknown_date(client, owner):
    pid = upload(client)["photo_id"]
    assert album_ids(client, "on_this_day", "?day=2026-11-15") == []
    with SessionLocal() as db:
        set_photo(db, pid, "2024-12-31T03:00:00+00:00")
        db.commit()
    result = client.get("/api/v1/albums/on_this_day?day=2026-01-01").json()
    assert result["match_window_days"] == 3 and result["items"][0]["id"] == pid
    assert "这几天" in result["items"][0]["album_reason"]
    assert result["items"][0]["album_reason"] == "1年前的这几天"
    assert day_distance(date(2024, 12, 31), date(2025, 1, 1)) == 1
    assert day_distance(date(2024, 2, 29), date(2025, 2, 28)) == 0
    assert album_ids(client, "on_this_day", "?day=9999-01-01") == [pid]
    with SessionLocal() as db:
        set_photo(db, pid, "2025-12-31T03:00:00+00:00")
        db.commit()
    assert album_ids(client, "on_this_day", "?day=2026-01-01") == []


def test_automatic_content_growth_without_face_permission_or_person_configuration(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(5)]
    with SessionLocal() as db:
        for i, pid in enumerate(ids):
            set_photo(
                db,
                pid,
                "2024-03-01T03:00:00+00:00" if i else None,
                child=i in {0, 1, 3, 4},
                event_type="travel" if i == 3 else "daily",
                confidence=0.2 if i == 3 else 0.9,
            )
        db.get(Photo, ids[4]).visibility = "private"
        # An old auto event without reliable travel evidence must not bypass the
        # VLM confidence gate after upgrading an existing library.
        old_event = Event(household_id=owner["household"]["id"], title="旧候选", event_type="travel")
        db.add(old_event)
        db.flush()
        db.add(EventPhoto(event_id=old_event.id, photo_id=ids[3], position=0))
        db.commit()
    assert client.get("/api/v1/people").json()["items"] == []
    assert set(album_ids(client, "growth")) == set(ids[:2])
    data = client.get("/api/v1/albums/growth?scope=family").json()
    assert data["mode"] == "automatic" and data["items"][-1]["album_group"] == "unknown"
    assert "不代表同一人物" in data["items"][0]["album_reason"]
    assert album_ids(client, "travel") == []
    with SessionLocal() as db:
        playlist = build_daily_playlist(db, db.get(Household, owner["household"]["id"]), date(2026, 6, 1))
        db.commit()
        scenes = {i.scene_type for i in db.scalars(select(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id))}
        assert "growth" in scenes and "travel" not in scenes
    # Withdrawing a photograph removes it immediately, including summary covers.
    client.patch("/api/v1/photos/" + ids[1], json={"is_hidden": True})
    assert album_ids(client, "growth") == [ids[0]]


def test_growth_manual_include_exclude_and_reset_override_automatic_content(client, owner):
    child, other = [upload(client, i)["photo_id"] for i in range(2)]
    with SessionLocal() as db:
        set_photo(db, child, "2024-04-01T03:00:00+00:00", child=True)
        set_photo(db, other, "2025-04-01T03:00:00+00:00")
        db.commit()
    person = client.post(
        "/api/v1/people", json={"name": "人工名字", "is_family": True, "manually_confirmed": True}
    ).json()
    assert person["growth_mode"] == "auto" and not person["growth_enabled"]
    assert client.post(f"/api/v1/people/{person['id']}/photos/{other}").status_code == 200
    assert album_ids(client, "growth") == [child]
    assert client.patch("/api/v1/people/" + person["id"], json={"growth_enabled": True}).status_code == 200
    assert album_ids(client, "growth") == [other]
    assert client.get("/api/v1/albums/growth").json()["mode"] == "manual"
    client.post(f"/api/v1/people/{person['id']}/photos/{child}")
    client.patch("/api/v1/people/" + person["id"], json={"growth_mode": "exclude"})
    assert album_ids(client, "growth") == []
    client.patch("/api/v1/people/" + person["id"], json={"growth_mode": "auto"})
    assert set(album_ids(client, "growth")) == {child, other}  # confirmed person's cross-month records
    conflict = client.patch("/api/v1/people/" + person["id"], json={"growth_mode": "auto", "growth_enabled": True})
    assert conflict.status_code == 422
    assert client.patch("/api/v1/people/" + person["id"], json={"growth_mode": None}).status_code == 422


def test_manual_empty_growth_selection_does_not_fall_back_to_unrelated_children(client, owner):
    pid = upload(client)["photo_id"]
    with SessionLocal() as db:
        set_photo(db, pid, child=True)
        db.commit()
    client.post(
        "/api/v1/people",
        json={"name": "未关联照片的家人", "is_family": True, "manually_confirmed": True, "growth_mode": "include"},
    )
    assert album_ids(client, "growth") == []


def test_async_analysis_builds_travel_and_growth_without_confirmation_and_respects_corrections(client, owner):
    pid = upload(client, exif=True)["photo_id"]

    def content_provider(kind, **kwargs):
        result = fake_provider(kind, **kwargs)
        if kind == "vlm":
            result["analysis"]["scene_tags"] += ["儿童玩耍"]
        return result

    drain(provider=content_provider)
    assert album_ids(client, "travel") == [pid]
    assert album_ids(client, "growth") == [pid]  # album membership is non-exclusive
    event = client.get("/api/v1/events").json()["items"][0]
    assert not event["confirmed"]
    with SessionLocal() as db:
        h = db.get(Household, owner["household"]["id"])
        playlist = build_daily_playlist(db, h, date(2026, 6, 1))
        db.commit()
        item = db.scalar(select(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id))
        assert item.scene_type == "travel" and "候选" in item.reason_codes_json[0]
    client.patch("/api/v1/events/" + event["id"], json={"title": "人工修正为日常", "event_type": "daily"})
    assert album_ids(client, "travel") == []
    client.post("/api/v1/photos/" + pid + "/analyze")
    drain(provider=content_provider)
    assert album_ids(client, "travel") == []
    with SessionLocal() as db:
        assert db.get(Event, event["id"]).title == "人工修正为日常"
    client.patch("/api/v1/events/" + event["id"], json={"event_type": "outing"})
    assert album_ids(client, "travel") == [pid]


def test_album_household_boundary_privacy_and_invalid_kind(client, owner):
    pid = upload(client)["photo_id"]
    with SessionLocal() as db:
        set_photo(db, pid, child=True, event_type="travel")
        db.commit()
    assert album_ids(client, "travel") == [pid]
    with TestClient(app) as stranger:
        setup(stranger, "another-home", True)
        for kind in ("on_this_day", "growth", "travel"):
            assert album_ids(stranger, kind) == []
        assert all(item["cover"] is None for item in stranger.get("/api/v1/albums").json()["items"])
        assert stranger.get("/api/v1/albums").json()["private_photo_count"] == 0
    client.patch("/api/v1/photos/" + pid, json={"visibility": "private"})
    assert album_ids(client, "travel") == album_ids(client, "growth") == []
    assert client.get("/api/v1/albums").json()["private_photo_count"] == 1
    assert client.get("/api/v1/albums/nonexistent").status_code == 422


def test_upgrade_preserves_existing_person_choices_faces_and_milestones(client, owner, tmp_path):
    pid = upload(client)["photo_id"]
    person = client.post(
        "/api/v1/people",
        json={
            "name": "迁移测试",
            "is_family": True,
            "manually_confirmed": True,
            "growth_enabled": True,
        },
    ).json()
    other = client.post("/api/v1/people", json={"name": "原来未启用的人物"}).json()
    client.post(f"/api/v1/people/{person['id']}/photos/{pid}")
    milestone = client.post(
        "/api/v1/milestones",
        json={
            "person_id": person["id"],
            "title": "人工时刻",
            "date": "2025-01-01",
        },
    ).json()
    # Copy synthetic rows into an offline database shaped like the old revision.
    path = tmp_path / "album.sqlite3"
    with engine.connect() as source, sqlite3.connect(path) as destination:
        source.connection.driver_connection.backup(destination)
        # Build the actual initial schema, not today's schema with only one old
        # column removed. New feature tables did not exist at f601a7f91551.
        initial_tables = {
            "households", "users", "memberships", "invitations", "display_devices", "sessions",
            "login_attempts", "photos", "photo_derivatives", "photo_analyses", "embeddings", "people",
            "faces", "events", "event_photos", "milestones", "stories", "story_slides", "audio_memories",
            "playlists", "playlist_items", "playback_events", "feedback", "jobs", "search_queries", "audit_logs",
        }
        for table in reversed(Base.metadata.sorted_tables):
            if table.name not in initial_tables:
                destination.execute(f'DROP TABLE "{table.name}"')
        destination.execute("ALTER TABLE people DROP COLUMN growth_mode")
        destination.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        destination.execute("INSERT INTO alembic_version VALUES ('f601a7f91551')")
        destination.commit()
    env = {**os.environ, "DATA_DIR": str(tmp_path), "DATABASE_URL": f"sqlite:///{path}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[3],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(path) as db:
        assert dict(db.execute("SELECT id, growth_mode FROM people")) == {person["id"]: "include", other["id"]: "auto"}
        assert db.execute("SELECT photo_id FROM faces WHERE person_id = ?", (person["id"],)).fetchone() == (pid,)
        assert db.execute("SELECT person_id FROM milestones WHERE id = ?", (milestone["id"],)).fetchone() == (
            person["id"],
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
