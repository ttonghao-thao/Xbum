from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Job, Photo, PhotoAnalysis
from app.db.session import SessionLocal
from app.main import app
from conftest import drain, upload
from test_auth_privacy import invite, pair


def test_private_photos_organize_for_uploader_without_family_or_display_leak(client, owner):
    pid = upload(client, shared=False, exif=True)["photo_id"]
    drain()
    with SessionLocal() as db:
        photo = db.get(Photo, pid)
        photo.taken_at = "2023-11-18T03:00:00+00:00"
        a = db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == pid))
        a.analysis_json = {**a.analysis_json, "scene_tags": ["儿童", "户外游玩"], "candidate_event_type": "outing"}
        db.commit()
    assert client.get("/api/v1/albums").json()["scope"] == "visible"
    for kind in ("on_this_day?day=2026-11-15", "growth", "travel"):
        result = client.get("/api/v1/albums/" + kind).json()
        assert result["items"][0]["id"] == pid
        family = client.get("/api/v1/albums/" + kind + ("&" if "?" in kind else "?") + "scope=family").json()
        assert family["total"] == 0
    assert client.get("/api/v1/photos/" + pid).json()["visibility"] == "private"
    assert client.post("/api/v1/search", json={"query": "", "semantic": False}).json()["total"] == 0
    display, _ = pair(client)
    assert display.get("/api/v1/display/feed").json()["items"] == []
    assert display.get("/api/v1/albums").status_code == 401
    with TestClient(app) as other:
        invite(client, owner, other)
        for kind in ("on_this_day", "growth", "travel"):
            assert other.get("/api/v1/albums/" + kind).json()["total"] == 0
        assert other.get(f"/api/v1/photos/{pid}/media/320").status_code == 404


def test_batch_share_is_explicit_atomic_and_refreshes_events_without_reanalysis(client, owner):
    ids = [upload(client, i, False, exif=True)["photo_id"] for i in range(3)]
    drain()
    with SessionLocal() as db:
        initial_vlm = len(list(db.scalars(select(Job).where(Job.job_type == "vlm"))))
    with TestClient(app) as other:
        invite(client, owner, other)
        assert other.post("/api/v1/photos/batch", json={"action": "share", "photo_ids": ids}).status_code == 404
        response = client.post("/api/v1/photos/batch", json={"action": "share", "photo_ids": ids[:2] + ids[:1]})
        assert response.status_code == 200 and response.json()["count"] == 2
        assert {p["id"] for p in other.get("/api/v1/photos").json()["items"]} == set(ids[:2])
        assert other.get("/api/v1/photos/" + ids[2]).status_code == 404
        theirs = upload(other, 9, shared=True)["photo_id"]
        # An owner may edit shared family content, but cannot grant sharing on
        # behalf of another uploader. Validate the whole selection before writes.
        denied = client.post("/api/v1/photos/batch", json={"action": "share", "photo_ids": [ids[2], theirs]})
        assert denied.status_code == 422
        assert client.get("/api/v1/photos/" + ids[2]).json()["visibility"] == "private"
    drain()
    assert client.get("/api/v1/events").json()["total"] > 0
    with SessionLocal() as db:
        # Only the second member's new upload added a VLM job; sharing reused old results.
        assert len(list(db.scalars(select(Job).where(Job.job_type == "vlm")))) == initial_vlm + 1
        assert "visibility" in db.get(Photo, ids[0]).locked_fields
    fresh = upload(client, 19, shared=False)["photo_id"]
    assert client.get("/api/v1/photos/" + fresh).json()["visibility"] == "private"
