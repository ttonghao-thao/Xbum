import io
from datetime import date
import pytest

from PIL import Image
from PIL.TiffImagePlugin import IFDRational
from sqlalchemy import select

from app.db.models import Event, EventPhoto, Household, Photo, PhotoAnalysis
from app.db.session import SessionLocal
from app.services.recommendations import build_daily_playlist, playlist_payload
from conftest import upload
from test_auth_privacy import pair


def test_stored_playlist_and_display_media_recheck_quality_floor(client, owner):
    pid = upload(client)["photo_id"]
    display, _ = pair(client)
    try:
        assert display.get("/api/v1/display/feed").json()["items"]
        with SessionLocal() as db:
            db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == pid)).quality_score = 0.01
            db.commit()
        assert display.get("/api/v1/display/feed").json()["items"] == []
        assert display.get(f"/api/v1/photos/{pid}/media/960?display=1").status_code == 404
        assert client.get(f"/api/v1/photos/{pid}/media/960").status_code == 200
        assert client.get("/api/v1/photos").json()["items"]
    finally:
        display.close()


def test_disabling_every_scene_produces_no_playlist_items(client, owner):
    upload(client)
    config = client.get("/api/v1/settings").json()["settings"]
    assert (
        client.patch("/api/v1/settings", json={"scenes": {name: False for name in config["scenes"]}}).status_code == 200
    )
    assert client.post("/api/v1/playlists/regenerate").json()["items"] == []


def test_festival_card_does_not_confuse_different_festivals(client, owner):
    pid = upload(client)["photo_id"]
    with SessionLocal() as db:
        h = db.get(Household, owner["household"]["id"])
        p = db.get(Photo, pid)
        p.taken_at = "2024-09-17T04:00:00+00:00"
        event = Event(household_id=h.id, title="中秋团圆", event_type="festival", confirmed=True)
        db.add(event)
        db.flush()
        db.add(EventPhoto(event_id=event.id, photo_id=pid, position=0))
        db.flush()
        playlist = build_daily_playlist(db, h, date(2025, 1, 29))
        db.commit()
        assert playlist_payload(db, playlist, h)["items"][0]["scene_type"] != "festival"


def test_gps_and_explicit_exif_timezone(client, owner):
    image = Image.new("RGB", (800, 600), "orange")
    exif = Image.Exif()
    exif[34665] = {36867: "2025:01:01 12:00:00", 36881: "+09:00"}
    exif[34853] = {
        1: "N",
        2: (IFDRational(30), IFDRational(30), IFDRational(0)),
        3: "E",
        4: (IFDRational(120), IFDRational(15), IFDRational(0)),
    }
    content = io.BytesIO()
    image.save(content, "JPEG", exif=exif)
    pid = upload(client, data=content.getvalue(), exif=True)["photo_id"]
    photo = client.get("/api/v1/photos/" + pid).json()
    assert photo["capture_time_source"] == "exif"
    assert photo["taken_at"] == "2025-01-01T03:00:00+00:00"
    assert photo["gps_lat"] == 30.5 and photo["gps_lon"] == 120.25


def test_export_snapshot_invalidated_after_sharing_changes(client, owner):
    from conftest import drain

    pid = upload(client)["photo_id"]
    jid = client.post("/api/v1/system/export").json()["job_id"]
    drain()
    response = client.get("/api/v1/system/export/" + jid)
    assert response.status_code == 200 and response.content[:2] == b"PK"
    client.patch("/api/v1/photos/" + pid, json={"visibility": "private"})
    assert client.get("/api/v1/system/export/" + jid).status_code == 409


@pytest.mark.parametrize(
    "format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp"), ("HEIF", "image/heic")]
)
def test_all_installed_image_decoders(client, owner, format, mime):
    image = Image.new("RGB", (640, 480), (150, 120, 90))
    output = io.BytesIO()
    image.save(output, format=format)
    response = client.post(
        "/api/v1/photos/upload", data={"shared": "false"}, files={"file": ("synthetic", output.getvalue(), mime)}
    )
    assert response.status_code == 200, response.text
    photo = client.get("/api/v1/photos/" + response.json()["photo_id"]).json()
    assert (photo["width"], photo["height"]) == (640, 480)


def test_chunked_body_stopped_before_unbounded_multipart(client, owner):
    chunks = (b"x" * (1024 * 1024) for _ in range(33))
    response = client.post(
        "/api/v1/photos/upload", content=chunks, headers={"Content-Type": "application/octet-stream"}
    )
    assert response.status_code == 413 and response.json()["code"] == "http_413"


def test_metadata_rerun_rebuilds_derivative(client, owner):
    from app.db.models import PhotoDerivative
    from app.services.storage import storage
    from conftest import drain

    pid = upload(client)["photo_id"]
    with SessionLocal() as db:
        key = db.scalar(
            select(PhotoDerivative.storage_key).where(PhotoDerivative.photo_id == pid, PhotoDerivative.variant == "960")
        )
    storage.delete_media(key)
    drain()
    assert storage.read_media(key).is_file()


def test_openapi_documentation_requires_no_cdn(client):
    response = client.get("/api/docs")
    assert response.status_code == 200 and "cdn.jsdelivr" not in response.text
    assert "/api/v1/photos/upload" in response.text


def test_positive_negative_and_collection_feedback_change_scores(client, owner):
    pid = upload(client)["photo_id"]

    def score():
        items = client.post("/api/v1/playlists/regenerate").json()["items"]
        return next(i["score"] for i in items if i["photo_id"] == pid)

    baseline = score()
    client.post(f"/api/v1/photos/{pid}/feedback", json={"type": "favorite"})
    assert score() > baseline
    client.post(f"/api/v1/photos/{pid}/feedback", json={"type": "unfavorite"})
    assert score() == baseline
    client.post(f"/api/v1/photos/{pid}/feedback", json={"type": "like"})
    assert score() > baseline
    client.post(f"/api/v1/photos/{pid}/feedback", json={"type": "dislike"})
    assert score() < baseline
    client.post(f"/api/v1/photos/{pid}/feedback", json={"type": "like"})
    assert score() > baseline
