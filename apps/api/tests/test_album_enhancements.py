from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Event, EventPhoto, Photo, PhotoAnalysis
from app.db.session import SessionLocal
from app.main import app
from conftest import setup, upload
from test_auth_privacy import invite, pair


def stamp(db, photo_id, taken, score=0.8, child=True, travel=False):
    photo = db.get(Photo, photo_id)
    photo.taken_at = taken
    photo.capture_time_source = "user" if taken else "upload_uncertain"
    photo.duplicate_group = None
    analysis = db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == photo_id))
    analysis.quality_score = score
    analysis.analysis_json = {
        "confidence": 0.9, "candidate_event_type": "travel" if travel else "daily",
        "scene_tags": ["儿童"] if child else ["风景"], "objects": [],
        "visible_activity": "合成画面", "caption": "合成测试",
    }
    return photo


def test_album_film_uses_full_collection_across_pages_and_date_coverage(client, owner):
    ids = [upload(client, i, shared=False)["photo_id"] for i in range(43)]
    with SessionLocal() as db:
        for i, pid in enumerate(ids):
            day = date(2024, 1, 1) + timedelta(days=i)
            stamp(db, pid, day.isoformat() + "T03:00:00+00:00")
        db.commit()
    page = client.get("/api/v1/albums/growth?page=2&page_size=1").json()
    assert page["total"] == 43 and len(page["items"]) == 1
    film = client.get("/api/v1/albums/growth/playback?limit=200").json()
    assert film["mode"] == "photo_film"
    assert film["source_total"] == film["selected_count"] == 43
    assert {p["id"] for p in film["items"]} == set(ids)
    selected = client.get("/api/v1/albums/growth/playback?limit=5").json()["items"]
    assert selected[0]["id"] == ids[0] and selected[-1]["id"] == ids[-1]
    assert len(selected) == 5
    assert all("media_url" in p and "album_reason" in p for p in selected)


def test_album_film_scope_revocation_and_display_boundary(client, owner):
    public = upload(client, 1)["photo_id"]
    private = upload(client, 2, shared=False)["photo_id"]
    with SessionLocal() as db:
        for pid in [public, private]:
            stamp(db, pid, "2023-11-18T03:00:00+00:00", travel=True)
        db.commit()
    suffix = "/playback?day=2026-11-15"
    for kind in ["on_this_day", "growth", "travel"]:
        response = client.get(f"/api/v1/albums/{kind}" + suffix)
        assert response.status_code == 200, response.text
        assert {p["id"] for p in response.json()["items"]} == {public, private}
    with TestClient(app) as member:
        invite(client, owner, member)
        initial = member.get("/api/v1/albums/growth/playback").json()
        assert [p["id"] for p in initial["items"]] == [public]
        assert client.patch(f"/api/v1/photos/{public}", json={"visibility": "private"}).status_code == 200
        after = member.get("/api/v1/albums/growth/playback").json()
        assert after["source_total"] == 0 and after["items"] == []
        assert after["revision"] != initial["revision"]
        assert member.get(f"/api/v1/photos/{public}/media/960").status_code == 404
    display, _ = pair(client)
    try:
        assert display.get("/api/v1/albums/growth/playback").status_code == 401
    finally:
        display.close()
    with TestClient(app) as stranger:
        setup(stranger, "album-stranger", True)
        assert stranger.get("/api/v1/albums/growth/playback").json()["source_total"] == 0
    assert client.patch(f"/api/v1/photos/{private}", json={"is_hidden": True}).status_code == 200
    assert {p["id"] for p in client.get("/api/v1/albums/growth/playback").json()["items"]} == {public}


def test_recent_day_and_month_keep_complete_sets_and_explain_unknown_dates(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(4)]
    with SessionLocal() as db:
        for pid, taken in zip(ids, [
            "2026-11-01T03:00:00+00:00", "2026-11-15T03:00:00+00:00",
            "2026-09-01T03:00:00+00:00", None,
        ], strict=True):
            stamp(db, pid, taken)
        db.commit()
    result = client.get("/api/v1/albums/recent?day=2026-11-15").json()
    assert {p["id"] for p in result["items"]} == set(ids[:2])
    assert result["missing_date_count"] == 1
    assert any("上传时间" in reason for reason in result["explanation"])
    assert len(result["groups"]) == 2
    month = client.get("/api/v1/albums/recent?day=2026-09-15&period=month").json()
    assert [p["id"] for p in month["items"]] == [ids[2]]
    assert month["groups"][0]["id"] == "2026-09"


def test_person_group_and_milestone_filters_match_film_and_grid(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(3)]
    with SessionLocal() as db:
        for pid, taken in zip(ids, ["2024-01-01", "2024-02-01", "2024-02-02"], strict=True):
            stamp(db, pid, taken + "T03:00:00+00:00")
        db.commit()
    person = client.post("/api/v1/people", json={
        "name": "合成人物", "manually_confirmed": True, "is_family": True,
    }).json()
    for pid in ids[:2]:
        assert client.post(f"/api/v1/people/{person['id']}/photos/{pid}").status_code == 200
    milestone = client.post("/api/v1/milestones", json={
        "person_id": person["id"], "date": "2024-02-01", "title": "人工记录的纪念",
    }).json()
    filters = f"person_id={person['id']}&group=2024-02"
    grid = client.get("/api/v1/albums/growth?" + filters).json()
    film = client.get("/api/v1/albums/growth/playback?" + filters).json()
    assert [p["id"] for p in grid["items"]] == [ids[1]]
    assert [p["id"] for p in film["items"]] == [ids[1]]
    assert grid["groups"][0]["milestones"][0]["id"] == milestone["id"]
    assert grid["people"] == [{"id": person["id"], "name": "合成人物"}]
    unknown = client.get("/api/v1/albums/growth?person_id=not-in-family").json()
    assert unknown["total"] == 0 and unknown["highlights"] == []


def test_pin_is_personal_and_duplicate_representative_survives_selection(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(2)]
    with SessionLocal() as db:
        for pid, score in zip(ids, [0.95, 0.2], strict=True):
            stamp(db, pid, "2024-01-01T03:00:00+00:00", score).duplicate_group = ids[0]
        db.commit()
    group = client.get("/api/v1/duplicates").json()["items"][0]
    assert group["representative_id"] == ids[0]
    assert client.put(f"/api/v1/duplicates/{ids[1]}", json={"action": "representative"}).status_code == 200
    assert client.get("/api/v1/duplicates").json()["items"][0]["representative_id"] == ids[1]
    film = client.get("/api/v1/albums/growth/playback").json()
    assert film["source_total"] == 2 and [p["id"] for p in film["items"]] == [ids[1]]
    assert client.put("/api/v1/album-pins/growth", json={"pinned": True}).status_code == 200
    assert client.get("/api/v1/albums/growth").json()["pinned"]
    with TestClient(app) as member:
        invite(client, owner, member)
        assert member.get("/api/v1/album-pins").json()["items"] == []
        assert not member.get("/api/v1/albums/growth").json()["pinned"]
        assert member.put(f"/api/v1/duplicates/{ids[1]}", json={"action": "representative"}).status_code == 403
    assert client.put(f"/api/v1/duplicates/{ids[1]}", json={"action": "detach"}).status_code == 200
    assert client.get("/api/v1/duplicates").json()["total"] == 0
    assert client.get("/api/v1/photos").json()["total"] == 2


def test_travel_groups_offer_date_chapters_without_inventing_location(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(2)]
    with SessionLocal() as db:
        event = Event(household_id=owner["household"]["id"], title="人工确认旅行", event_type="travel", locked=True)
        db.add(event)
        db.flush()
        for i, pid in enumerate(ids):
            stamp(db, pid, f"2024-01-0{i + 1}T03:00:00+00:00", travel=True)
            db.add(EventPhoto(event_id=event.id, photo_id=pid, position=i))
        event_id = event.id
        db.commit()
    result = client.get("/api/v1/albums/travel").json()
    assert result["groups"][0]["id"] == event_id
    assert result["groups"][0]["dates"] == {"2024-01-01": 1, "2024-01-02": 1}
    assert result["groups"][0]["title"] == "人工确认旅行"
    assert all("人工" in photo["album_reason"] for photo in result["items"])


def test_personal_never_changes_film_and_cover_but_preserves_complete_album(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(2)]
    with SessionLocal() as db:
        for i, pid in enumerate(ids):
            stamp(db, pid, f"2024-01-0{i + 1}T03:00:00+00:00")
        db.commit()
    rule = client.post("/api/v1/recommendation-rules", json={
        "scope": "personal", "mode": "never", "target_kind": "date",
        "start_date": "2024-01-01", "end_date": "2024-01-02",
    })
    assert rule.status_code == 200, rule.text
    assert client.get("/api/v1/albums/growth").json()["total"] == 2
    film = client.get("/api/v1/albums/growth/playback").json()
    assert film["source_total"] == 2 and film["items"] == []
    assert any("推荐规则" in reason for reason in film["explanation"])
    card = next(p for p in client.get("/api/v1/albums").json()["items"] if p["kind"] == "growth")
    assert card["total"] == 2 and card["cover"] is None
    with TestClient(app) as member:
        invite(client, owner, member)
        assert member.get("/api/v1/albums/growth/playback").json()["selected_count"] == 2
    assert client.delete("/api/v1/recommendation-rules/" + rule.json()["id"]).status_code == 200
    assert client.get("/api/v1/albums/growth/playback").json()["selected_count"] == 2


def test_explicit_representative_can_keep_a_low_quality_nonduplicate_photo(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(2)]
    with SessionLocal() as db:
        for pid, score in zip(ids, [0.95, 0.01], strict=True):
            stamp(db, pid, "2024-01-01T03:00:00+00:00", score)
        db.commit()
    assert client.put(f"/api/v1/album-photos/{ids[1]}/preferred", json={"preferred": True}).status_code == 200
    film = client.get("/api/v1/albums/growth/playback?limit=1").json()
    assert film["items"][0]["id"] == ids[1]
    selected = client.get("/api/v1/albums/growth").json()
    assert next(photo for photo in selected["items"] if photo["id"] == ids[1])["preferred"]
    assert next(photo for photo in selected["highlights"] if photo["id"] == ids[1])["preferred"]
    assert client.put(f"/api/v1/album-photos/{ids[1]}/preferred", json={"preferred": False}).status_code == 200
    selected = client.get("/api/v1/albums/growth").json()
    assert not next(photo for photo in selected["highlights"] if photo["id"] == ids[1])["preferred"]
    assert client.get("/api/v1/albums/growth/playback?limit=1").json()["items"][0]["id"] == ids[0]
