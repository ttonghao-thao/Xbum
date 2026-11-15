import io
import json
import subprocess
import wave

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import Actor
from app.db.models import AudioMemory, EventPhoto, Household, Photo, Story, StorySlide
from app.db.session import SessionLocal
from app.db.story_features import StoryNarration
from app.main import app
from app.services.recommendations import build_daily_playlist, playlist_payload, recommendation_effects
from app.services.storage import storage
from app.services.stories import compose_story, export_mp4
from conftest import PASSWORD, drain, setup, upload


def invite_member(client, owner, member):
    code = client.post(f"/api/v1/households/{owner['household']['id']}/invites", json={}).json()["code"]
    response = member.post(f"/api/v1/invites/{code}/join", json={"username": "member", "password": PASSWORD})
    assert response.status_code == 200, response.text
    member.headers["X-CSRF-Token"] = response.json()["csrf"]


def rule(client, **values):
    response = client.post("/api/v1/recommendation-rules", json={
        "target_kind": "date", "start_date": "2025-02-01", "end_date": "2025-02-01",
        "mode": "never", **values,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_recommendation_scope_and_old_playlist_revalidation(client, owner):
    pid = upload(client, exif=True)["photo_id"]
    drain()
    personal = rule(client)
    with SessionLocal() as db:
        h = db.get(Household, owner["household"]["id"])
        p = db.get(Photo, pid)
        actor = Actor(owner["user"]["id"], h.id, "owner")
        assert recommendation_effects(db, h.id, [p], actor=actor)[pid]["blocked"]
        assert not recommendation_effects(db, h.id, [p], household_scope=True)[pid]["blocked"]
        playlist = build_daily_playlist(db, h)
        db.commit()
        assert [row["photo_id"] for row in playlist_payload(db, playlist, h)["items"]] == [pid]
        playlist_id = playlist.id
    with TestClient(app) as member:
        invite_member(client, owner, member)
        assert member.get("/api/v1/recommendation-rules").json()["items"] == []
        assert member.delete("/api/v1/recommendation-rules/" + personal["id"]).status_code == 404
        assert member.post("/api/v1/recommendation-rules", json={
            "target_kind": "theme", "target_value": "海边", "scope": "household",
        }).status_code == 403
    global_rule = rule(client, scope="household")
    with SessionLocal() as db:
        from app.db.models import Playlist
        h = db.get(Household, owner["household"]["id"])
        assert playlist_payload(db, db.get(Playlist, playlist_id), h)["items"] == []
        assert playlist_payload(db, build_daily_playlist(db, h), h)["items"] == []
    assert client.get("/api/v1/photos/" + pid).status_code == 200
    assert client.post("/api/v1/search", json={"query": "", "semantic": False}).json()["total"] == 1
    assert client.delete("/api/v1/recommendation-rules/" + global_rule["id"]).status_code == 200


def test_invalid_rules_and_less_mode(client, owner):
    pid = upload(client, exif=True)["photo_id"]
    response = client.post("/api/v1/recommendation-rules", json={
        "target_kind": "date", "start_date": "2025-02-02", "end_date": "2025-02-01",
    })
    assert response.status_code == 422
    person = client.post("/api/v1/people", json={"name": "候选"}).json()
    assert client.post("/api/v1/recommendation-rules", json={
        "target_kind": "person", "target_value": person["id"],
    }).status_code == 422
    rule(client, mode="less")
    with SessionLocal() as db:
        actor = Actor(owner["user"]["id"], owner["household"]["id"], "owner")
        value = recommendation_effects(db, actor.household_id, [db.get(Photo, pid)], actor=actor)[pid]
        assert value["multiplier"] == 0.25 and not value["blocked"]


def test_queued_story_selection_covers_source_and_preserves_manual_edits(client, owner):
    ids = []
    for i in range(16):
        pid = upload(client, i + 1, exif=True)["photo_id"]
        client.patch("/api/v1/photos/" + pid, json={"taken_at": f"2025-01-{i+1:02d}T12:00:00+08:00"})
        ids.append(pid)
    response = client.post("/api/v1/stories/compose", json={"photo_ids": ids, "length": "short", "title": "真实日期故事"})
    assert response.status_code == 200, response.text
    sid = response.json()["story"]["id"]
    with SessionLocal() as db:
        assert compose_story(db, db.get(Story, sid))
        db.commit()
    result = client.get("/api/v1/stories/" + sid).json()
    assert len(result["slides"]) <= 12
    assert {ids[0], ids[-1]}.issubset({s["photo_id"] for s in result["slides"]})
    assert result["generated_by_ai"] is False
    assert result["composition"]["state"] == "ready"
    assert all(s["caption_source"] == "capture_time" for s in result["composition"]["sources"])
    client.patch("/api/v1/stories/" + sid, json={"title": "人工故事标题"})
    with SessionLocal() as db:
        assert compose_story(db, db.get(Story, sid)) is False
        db.commit()
    assert client.get("/api/v1/stories/" + sid).json()["title"] == "人工故事标题"
    assert client.post(f"/api/v1/stories/{sid}/compose").status_code == 409


def test_story_composition_rejects_private_and_cross_household_sources(client, owner):
    private = upload(client, shared=False)["photo_id"]
    assert client.post("/api/v1/stories/compose", json={"photo_ids": [private]}).status_code == 422
    with TestClient(app) as other:
        setup(other, "separate", True)
        assert other.post("/api/v1/stories/compose", json={"photo_ids": [private]}).status_code == 404


def make_narrated_story(client, owner):
    pid = upload(client, exif=True)["photo_id"]
    drain()
    with SessionLocal() as db:
        event_id = db.scalar(select(EventPhoto.event_id).where(EventPhoto.photo_id == pid))
    story = client.post("/api/v1/stories/generate", json={"event_id": event_id}).json()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 8000)
    key = f"{owner['household']['id']}/audio/narration-synthetic.wav"
    storage.put_original(key, buffer.getvalue())
    with SessionLocal() as db:
        memory = AudioMemory(household_id=owner["household"]["id"], photo_id=pid,
                             uploader_id=owner["user"]["id"], audio_key=key,
                             transcript="这是人工确认的合成测试旁白", transcript_confirmed=False)
        db.add(memory)
        db.commit()
        memory_id = memory.id
    return story["id"], pid, memory_id


def test_narration_confirmation_and_deleted_source_invalidates_story(client, owner):
    sid, pid, mid = make_narrated_story(client, owner)
    path = f"/api/v1/stories/{sid}/narrations"
    assert client.post(path, json={"audio_memory_id": mid, "photo_id": pid}).status_code == 404
    client.patch("/api/v1/memories/audio/" + mid, json={"transcript": "这是已确认的旁白"})
    response = client.post(path, json={"audio_memory_id": mid, "photo_id": pid})
    assert response.status_code == 200, response.text
    binding_id = response.json()["id"]
    assert client.get("/api/v1/stories/" + sid).json()["slides"][0]["narration_url"].endswith(binding_id + "/audio")
    assert client.delete("/api/v1/memories/audio/" + mid).status_code == 200
    assert client.get("/api/v1/stories/" + sid).status_code == 404
    assert client.get(f"/api/v1/stories/{sid}/export").status_code == 404
    assert client.get(path).json()["items"][0]["valid"] is False
    assert client.delete(path + "/" + binding_id).status_code == 200
    assert client.get("/api/v1/stories/" + sid).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(StoryNarration).where(StoryNarration.story_id == sid)) is None


def test_narration_mp4_contains_audio_and_revoked_photo_is_denied(client, owner):
    sid, pid, mid = make_narrated_story(client, owner)
    client.patch("/api/v1/memories/audio/" + mid, json={"transcript": "确认的原声"})
    assert client.post(f"/api/v1/stories/{sid}/narrations", json={"audio_memory_id": mid, "photo_id": pid}).status_code == 200
    with SessionLocal() as db:
        story = db.get(Story, sid)
        slide = db.scalar(select(StorySlide).where(StorySlide.story_id == sid))
        slide.duration_s = 1
        db.commit()
        key = export_mp4(db, story)
        from app.ai.providers import ffmpeg_binary
        from pathlib import Path
        probe = subprocess.run([str(Path(ffmpeg_binary()).with_name("ffprobe")), "-v", "error",
                                "-show_entries", "stream=codec_type", "-of", "json", str(storage.read_media(key))],
                               capture_output=True, check=True, timeout=10)
        assert "audio" in {s["codec_type"] for s in json.loads(probe.stdout)["streams"]}
        story.mp4_key, story.export_version = key, story.version
        db.commit()
    assert client.get(f"/api/v1/stories/{sid}/export").status_code == 200
    client.patch("/api/v1/photos/" + pid, json={"visibility": "private"})
    assert client.get(f"/api/v1/stories/{sid}/export").status_code == 404


def test_memory_suggestions_use_family_only_and_personal_rules(client, owner):
    public = upload(client, 1, exif=True)["photo_id"]
    private = upload(client, 2, shared=False, exif=True)["photo_id"]
    drain()
    suggestions = client.get("/api/v1/memory-suggestions").json()["items"]
    photo_ids = {pid for row in suggestions for pid in row["photo_ids"]}
    assert public in photo_ids and private not in photo_ids
    rule(client)
    assert client.get("/api/v1/memory-suggestions").json()["items"] == []


def test_composition_metadata_filters_removed_slides(client, owner):
    ids = [upload(client, seed, exif=True)["photo_id"] for seed in [1, 2]]
    result = client.post("/api/v1/stories/compose", json={"photo_ids": ids}).json()
    sid = result["story"]["id"]
    with SessionLocal() as db:
        compose_story(db, db.get(Story, sid))
        db.commit()
    assert client.patch("/api/v1/stories/" + sid, json={"slides": [{"photo_id": ids[0], "caption": "人工说明"}]}).status_code == 200
    client.patch("/api/v1/photos/" + ids[1], json={"visibility": "private"})
    result = client.get("/api/v1/stories/" + sid).json()
    assert result["composition"]["state"] == "edited"
    assert {s["photo_id"] for s in result["composition"]["sources"]} == {ids[0]}
    assert all(ids[1] not in chapter["photo_ids"] for chapter in result["composition"]["chapters"])


def test_narration_audio_display_scope_and_duplicate_binding(client, owner):
    sid, pid, mid = make_narrated_story(client, owner)
    client.patch("/api/v1/memories/audio/" + mid, json={"transcript": "确认的原声"})
    path = f"/api/v1/stories/{sid}/narrations"
    binding = client.post(path, json={"audio_memory_id": mid, "photo_id": pid}).json()
    assert client.post(path, json={"audio_memory_id": mid, "photo_id": pid}).status_code == 409
    audio_url = path + "/" + binding["id"] + "/audio"
    assert client.get(audio_url).status_code == 200
    device = client.post("/api/v1/display/devices", json={"name": "合成原声屏"}).json()
    with TestClient(app) as display:
        assert display.post("/api/v1/display/pair", json={"code": device["code"]}).status_code == 200
        assert display.get(audio_url + "?display=1").status_code == 200
        assert display.get("/api/v1/memories/audio/" + mid + "/media").status_code == 401
        client.patch("/api/v1/photos/" + pid, json={"visibility": "private"})
        assert display.get(audio_url + "?display=1").status_code == 404
