import io
import wave
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    AudioMemory,
    Event,
    EventPhoto,
    Household,
    Job,
    Photo,
    PhotoAnalysis,
    PlaylistItem,
)
from app.db.session import SessionLocal
from app.main import app
from app.services.calendar import festivals, is_quiet, parse_time
from app.services.events import cluster_events
from app.services.recommendations import build_daily_playlist
from app.workers.worker import run_job
from conftest import drain, fake_provider, setup, upload


def set_taken(pid, value, db):
    p = db.get(Photo, pid)
    p.taken_at = value
    p.capture_time_source = "user"
    return p


def test_event_time_clustering_lock_merge_split(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(4)]
    with SessionLocal() as db:
        for i, pid in enumerate(ids):
            set_taken(pid, f"202{4 + i // 2}-05-01T0{i % 2 + 1}:00:00+00:00", db)
        db.commit()
        cluster_events(db, db.get(Household, owner["household"]["id"]))
        db.commit()
    events = client.get("/api/v1/events").json()["items"]
    assert len(events) == 2
    first = events[0]
    assert (
        client.patch(
            "/api/v1/events/" + first["id"], json={"title": "人工旅行", "event_type": "travel", "confirmed": True}
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        cluster_events(db, db.get(Household, owner["household"]["id"]))
        db.commit()
        assert db.get(Event, first["id"]).title == "人工旅行"
    merged = client.post("/api/v1/events/merge", json={"event_ids": [e["id"] for e in events], "title": "旅行总集"})
    assert merged.status_code == 200, merged.text
    assert set(merged.json()["photo_ids"]) == set(ids)
    split = client.post(
        "/api/v1/events/" + merged.json()["id"] + "/split", json={"photo_ids": [ids[0]], "title": "第一天"}
    )
    assert split.status_code == 200 and split.json()["locked"]
    with SessionLocal() as db:
        cluster_events(db, db.get(Household, owner["household"]["id"]))
        db.commit()
    assert len(client.get("/api/v1/events").json()["items"]) == 2


def test_growth_birthday_and_people_merge_split(client, owner):
    pid = upload(client)["photo_id"]
    person = client.post(
        "/api/v1/people",
        json={
            "name": "孩子",
            "manually_confirmed": True,
            "is_family": True,
            "growth_enabled": True,
            "birthday": "2020-11-14",
        },
    ).json()
    assert client.post(f"/api/v1/people/{person['id']}/photos/{pid}").status_code == 200
    result = client.get("/api/v1/timeline?person_id=" + person["id"]).json()
    assert len(result["items"]) == 1
    milestone = client.post(
        "/api/v1/milestones", json={"person_id": person["id"], "date": "2025-09-01", "title": "家人确认的开学日"}
    )
    assert milestone.status_code == 200
    assert client.get("/api/v1/timeline?person_id=" + person["id"]).json()["milestones"][0]["confirmed"]
    with SessionLocal() as db:
        set_taken(pid, "2024-11-14T03:00:00+00:00", db)
        playlist = build_daily_playlist(db, db.get(Household, owner["household"]["id"]), date(2026, 11, 14))
        db.commit()
        item = db.scalar(select(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id))
        assert item.scene_type == "birthday"
    split = client.post(
        "/api/v1/people/split",
        json={
            "person_id": person["id"],
            "face_ids": client.get("/api/v1/people").json()["items"][0]["faces"]
            and [client.get("/api/v1/people").json()["items"][0]["faces"][0]["id"]],
        },
    )
    assert split.status_code == 200
    assert (
        client.post("/api/v1/people/merge", json={"person_ids": [person["id"], split.json()["id"]]}).status_code == 200
    )
    assert client.post("/api/v1/stories/generate", json={"person_id": person["id"]}).status_code == 200


def test_recommendation_deterministic_diverse_feedback_and_history(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(16)]
    with SessionLocal() as db:
        h = db.get(Household, owner["household"]["id"])
        for i, pid in enumerate(ids):
            set_taken(pid, f"2024-11-{14 if i == 0 else 1:02d}T03:00:00+00:00", db)
            a = db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == pid))
            a.quality_score = 0.8
        for group in range(4):
            event = Event(household_id=h.id, title="事件" + str(group), confirmed=True)
            db.add(event)
            db.flush()
            for i, pid in enumerate(ids[group * 4 : group * 4 + 4]):
                db.add(EventPhoto(event_id=event.id, photo_id=pid, position=i))
        db.commit()
        first = build_daily_playlist(db, h, date(2026, 11, 14))
        db.commit()
        left = [
            i.photo_id
            for i in db.scalars(
                select(PlaylistItem).where(PlaylistItem.playlist_id == first.id).order_by(PlaylistItem.position)
            )
        ]
        second = build_daily_playlist(db, h, date(2026, 11, 14))
        db.commit()
        right = [
            i.photo_id
            for i in db.scalars(
                select(PlaylistItem).where(PlaylistItem.playlist_id == second.id).order_by(PlaylistItem.position)
            )
        ]
        assert left == right and len(set(left)) == 16
        item = db.scalar(
            select(PlaylistItem).where(PlaylistItem.playlist_id == first.id, PlaylistItem.photo_id == ids[0])
        )
        assert item.scene_type == "on_this_day" and "2年前的今天" in item.reason_codes_json
    client.post("/api/v1/photos/" + ids[0] + "/feedback", json={"type": "snooze"})
    result = client.post("/api/v1/playlists/regenerate").json()
    assert ids[0] not in [i["photo_id"] for i in result["items"]]
    client.post("/api/v1/photos/" + ids[1] + "/feedback", json={"type": "hide"})
    assert ids[1] not in [i["photo_id"] for i in client.get("/api/v1/playlists/today").json()["items"]]
    # Historic snapshots remain recorded, but withdrawn photos are filtered at read time.
    historical = client.get("/api/v1/playlists/2026-11-14").json()
    assert ids[1] not in [i["photo_id"] for i in historical["items"]]


def test_no_invented_on_this_day_and_duplicate_control(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(3)]
    with SessionLocal() as db:
        h = db.get(Household, owner["household"]["id"])
        for pid in ids:
            db.get(Photo, pid).duplicate_group = "burst"
        playlist = build_daily_playlist(db, h, date(2026, 11, 14))
        db.commit()
        items = list(db.scalars(select(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id)))
        assert len(items) == 1 and items[0].scene_type != "on_this_day"


def test_search_historical_time_structured_filters_and_privacy(client, owner):
    pid = upload(client)["photo_id"]
    unknown = upload(client, 2)["photo_id"]
    private = upload(client, 3, False)["photo_id"]
    for p in [pid, unknown, private]:
        client.patch("/api/v1/photos/" + p, json={"caption_user": "海边全家合影", "location_user": "人工登记海边"})
    with SessionLocal() as db:
        set_taken(pid, "2025-01-30T04:00:00+00:00", db)
        db.commit()
    result = client.post("/api/v1/search", json={"query": "去年春节的合影", "semantic": False}).json()
    assert [p["id"] for p in result["items"]] == [pid]
    result = client.post(
        "/api/v1/search", json={"query": "海边合照", "semantic": False, "location": "人工登记海边"}
    ).json()
    assert {p["id"] for p in result["items"]} == {pid, unknown}
    assert all(p["match_reasons"] for p in result["items"])
    assert client.post("/api/v1/search", json={"query": "火星不存在", "semantic": False}).json()["items"] == []
    client.patch("/api/v1/photos/" + pid, json={"is_hidden": True})
    assert pid not in [
        p["id"] for p in client.post("/api/v1/search", json={"query": "海边", "semantic": False}).json()["items"]
    ]


def test_semantic_text_embedding_is_async_and_model_matched(client, owner):
    upload(client)
    drain()
    result = client.post("/api/v1/search", json={"query": "海边"}).json()
    assert result["job_id"] and result["semantic_state"] == "queued"
    drain()
    result = client.post("/api/v1/search", json={"query": "海边", "query_id": result["query_id"]}).json()
    assert result["semantic_state"] == "succeeded" and len(result["items"]) == 1


def test_story_edit_export_real_ffmpeg_and_authorization(client, owner):
    pid = upload(client, exif=True)["photo_id"]
    drain()
    event = client.get("/api/v1/events").json()["items"][0]
    response = client.post("/api/v1/stories/generate", json={"event_id": event["id"]})
    assert response.status_code == 200, response.text
    story = response.json()
    assert not story["generated_by_ai"]
    updated = client.patch(
        "/api/v1/stories/" + story["id"],
        json={
            "title": "我们的真实记忆",
            "slides": [{"photo_id": pid, "caption": "人工字幕测试", "duration_s": 1, "transition": "fade"}],
        },
    )
    assert updated.status_code == 200, updated.text
    job = client.post("/api/v1/stories/" + story["id"] + "/export").json()["job_id"]
    drain()
    assert client.get("/api/v1/jobs/" + job).json()["state"] == "succeeded"
    result = client.get("/api/v1/stories/" + story["id"] + "/export")
    assert result.status_code == 200 and result.content[4:8] == b"ftyp"
    client.patch("/api/v1/photos/" + pid, json={"visibility": "private"})
    assert client.get("/api/v1/stories/" + story["id"] + "/export").status_code == 404
    assert client.get("/api/v1/stories/" + story["id"]).status_code == 404


def recording():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 16000)
    return output.getvalue()


def test_audio_memory_async_correction_and_delete(client, owner):
    pid = upload(client)["photo_id"]
    assert (
        client.post(
            "/api/v1/memories/audio", data={"photo_id": pid}, files={"file": ("test.wav", recording(), "audio/wav")}
        ).status_code
        == 409
    )
    client.patch("/api/v1/settings", json={"speech_enabled": True})
    r = client.post(
        "/api/v1/memories/audio", data={"photo_id": pid}, files={"file": ("test.wav", recording(), "audio/wav")}
    )
    assert r.status_code == 200, r.text
    mid = r.json()["memory_id"]
    drain()
    assert client.get("/api/v1/memories/audio/" + mid).json()["transcript"] == "测试口述记忆"
    assert client.patch("/api/v1/memories/audio/" + mid, json={"transcript": "人工校对"}).status_code == 200
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.target_id == mid))
        job.state = "running"
        job.lease_token = "second"
        db.commit()
        run_job(db, job, provider=fake_provider)
        assert db.get(AudioMemory, mid).transcript == "人工校对"
    with TestClient(app) as other:
        setup(other, "outside", True)
        assert other.get("/api/v1/memories/audio/" + mid).status_code == 404
        assert other.get("/api/v1/memories/audio/" + mid + "/media").status_code == 404
    assert client.delete("/api/v1/memories/audio/" + mid).status_code == 200
    assert client.get("/api/v1/memories/audio/" + mid).status_code == 404


def test_calendar_timezone_and_lunar():
    start, end, label = parse_time("去年春节全家合影", "Asia/Shanghai", date(2026, 11, 14))
    assert start == date(2025, 1, 26) and end == date(2025, 2, 5) and label == "春节"
    assert "春节" in festivals(date(2025, 1, 29))
    assert parse_time("去年春節的全家合影", "Asia/Shanghai", date(2026, 11, 14))[:2] == (start, end)
    start, end, _ = parse_time("上个月", "Asia/Shanghai", date(2026, 1, 2))
    assert start == date(2025, 12, 1) and end == date(2025, 12, 31)
    assert not is_quiet({"quiet_start": "00:00", "quiet_end": "00:00"}, "Asia/Shanghai")
