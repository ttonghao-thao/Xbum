import io
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.schemas import AnalysisOutput
from app.db.models import (
    Embedding,
    EventPhoto,
    Face,
    Job,
    Photo,
    PhotoAnalysis,
    PhotoDerivative,
    StorySlide,
)
from app.db.session import SessionLocal
from app.services.storage import LocalStorage, storage
from app.workers.queue import claim, enqueue, fail, recover
from app.workers.worker import run_job
from conftest import drain, fake_provider, picture, upload


def test_upload_100_and_sha_dedup(client, owner):
    ids = [upload(client, i)["photo_id"] for i in range(100)]
    assert len(set(ids)) == 100
    result = upload(client, 0)
    assert result["status"] == "duplicate" and result["photo_id"] == ids[0]
    assert client.get("/api/v1/photos?page_size=100").json()["total"] == 100
    from fastapi.testclient import TestClient
    from app.main import app
    from test_auth_privacy import invite

    with TestClient(app) as other:
        invite(client, owner, other)
        shared = other.get("/api/v1/photos?page_size=100").json()
        assert shared["total"] == 100 and len(shared["items"]) == 100
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(PhotoDerivative)) == 200
        assert db.scalar(select(func.count()).select_from(Job)) == 600


def test_near_duplicate_candidates_preserve_originals(client, owner):
    original = picture(10)
    left = upload(client, data=original)["photo_id"]
    image = Image.open(io.BytesIO(original))
    output = io.BytesIO()
    image.save(output, "PNG", compress_level=1)
    right = upload(client, data=output.getvalue())["photo_id"]
    assert left != right
    with SessionLocal() as db:
        a, b = db.get(Photo, left), db.get(Photo, right)
        assert a.duplicate_group == b.duplicate_group
        assert storage.read_media(a.storage_key).is_file() and storage.read_media(b.storage_key).is_file()


@pytest.mark.parametrize("data,mime", [(b"not a picture", "image/png"), (picture(), "text/html")])
def test_upload_rejects_invalid_content(client, owner, data, mime):
    result = client.post("/api/v1/photos/upload", files={"file": ("../escape.png", data, mime)})
    assert result.status_code == 415
    assert "request_id" in result.json()


def test_pixel_limit_and_size(client, owner, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 20)
    result = client.post("/api/v1/photos/upload", files={"file": ("large.png", picture(), "image/png")})
    assert result.status_code == 415
    result = client.post("/api/v1/photos/upload", content=b"x", headers={"Content-Length": str(40 * 1024 * 1024)})
    assert result.status_code == 413


def test_exif_orientation_unknown_time_and_manual_lock(client, owner):
    known = upload(client, 5, exif=True)["photo_id"]
    unknown = upload(client, 6)["photo_id"]
    a = client.get("/api/v1/photos/" + known).json()
    b = client.get("/api/v1/photos/" + unknown).json()
    assert (a["width"], a["height"]) == (600, 800)
    assert a["taken_at"].startswith("2025-02-01T04:30:00")
    assert a["capture_time_source"] == "exif_assumed_timezone"
    assert b["taken_at"] is None and b["gps_lat"] is None and b["capture_time_source"] == "upload_uncertain"
    assert (
        client.patch(
            "/api/v1/photos/" + known, json={"taken_at": "2023-01-02T03:00:00+08:00", "caption_user": "人工记忆"}
        ).status_code
        == 200
    )
    drain()
    a = client.get("/api/v1/photos/" + known).json()
    assert a["taken_at"].startswith("2023-01-01T19:00:00") and a["caption_user"] == "人工记忆"
    assert "taken_at" in a["locked_fields"]
    assert a["analysis"]["caption_ai"].endswith("（测试提供者）")


def test_all_independent_steps_and_faces_off(client, owner):
    pid = upload(client, exif=True)["photo_id"]
    drain()
    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.target_id == pid)))
        assert len(jobs) == 6
        assert next(j for j in jobs if j.job_type == "faces").state == "skipped"
        assert all(j.state in {"succeeded", "skipped"} for j in jobs)
        assert db.scalar(select(func.count()).select_from(Face)) == 0
        e = db.scalar(select(Embedding).where(Embedding.photo_id == pid))
        assert e.model_version == "fake-1" and e.dimension == 4
        assert np.linalg.norm(np.frombuffer(e.data_blob, dtype=np.float32)) == pytest.approx(1)
        assert db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == pid)).quality_score is not None


def test_queue_idempotence_retry_pause_restart(client, owner):
    pid = upload(client)["photo_id"]
    with SessionLocal() as db:
        a = enqueue(db, owner["household"]["id"], "photo", pid, "metadata")
        b = enqueue(db, owner["household"]["id"], "photo", pid, "metadata")
        assert a.id == b.id
        db.commit()
        job = claim(db)
        assert job.state == "running" and job.attempts == 1
        jid = job.id
        fail(job, "model_missing")
        db.commit()
        assert job.state == "retry"
        job.state = "running"
        job.lease_until = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        db.commit()
        recover(db)
        assert db.get(Job, jid).state == "queued"
    assert client.post("/api/v1/jobs/pause").status_code == 200
    with SessionLocal() as db:
        assert claim(db) is None
    assert client.post("/api/v1/jobs/resume").status_code == 200
    with SessionLocal() as db:
        assert claim(db) is not None


def test_provider_failure_does_not_block_quality_or_photos(client, owner):
    pid = upload(client)["photo_id"]

    def fail_ai(kind, **kwargs):
        return {"ok": False, "code": "model_missing"}

    drain(provider=fail_ai)
    assert client.get("/api/v1/photos/" + pid + "/media/960").status_code == 200
    with SessionLocal() as db:
        jobs = list(db.scalars(select(Job).where(Job.target_id == pid)))
        assert next(j for j in jobs if j.job_type == "quality").state == "succeeded"
        assert next(j for j in jobs if j.job_type == "vlm").state == "retry"


def test_strict_model_json():
    with pytest.raises(ValidationError):
        AnalysisOutput.model_validate({"confidence": 1.2})
    with pytest.raises(ValidationError):
        AnalysisOutput.model_validate({"name": "invented"})
    with pytest.raises(ValidationError):
        AnalysisOutput.model_validate_json("not json")
    with pytest.raises(ValidationError):
        AnalysisOutput.model_validate({})
    complete = fake_provider("vlm")["analysis"]
    assert AnalysisOutput.model_validate(complete).caption
    for required in complete:
        with pytest.raises(ValidationError):
            AnalysisOutput.model_validate({k: v for k, v in complete.items() if k != required})


def test_face_consent_revalidation_and_erasure(client, owner):
    pid = upload(client)["photo_id"]
    assert client.patch("/api/v1/settings", json={"faces_enabled": True}).status_code == 422
    assert client.patch("/api/v1/settings", json={"faces_enabled": True, "face_consent": True}).status_code == 200
    drain()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Face)) == 1
    person = client.get("/api/v1/people").json()["items"][0]
    assert person["name"] is None and not person["manually_confirmed"]
    face_id = person["faces"][0]["id"]
    crop = client.get("/api/v1/faces/" + face_id + "/media")
    assert crop.status_code == 200 and crop.headers["content-type"].startswith("image/jpeg")
    assert (
        client.patch("/api/v1/people/" + person["id"], json={"name": "家人甲", "manually_confirmed": True}).status_code
        == 200
    )
    client.post("/api/v1/photos/" + pid + "/analyze?step=faces")
    drain()
    assert client.get("/api/v1/people").json()["items"][0]["faces"][0]["id"] == face_id
    client.patch("/api/v1/settings", json={"faces_enabled": False, "delete_biometrics": True})
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Face)) == 0


def test_photo_purge_cascades_every_derived_record(client, owner):
    pid = upload(client, exif=True)["photo_id"]
    drain()
    event = client.get("/api/v1/events").json()["items"][0]
    client.post("/api/v1/stories/generate", json={"event_id": event["id"]}).json()["id"]
    with SessionLocal() as db:
        path = storage.read_media(db.get(Photo, pid).storage_key)
    assert client.delete("/api/v1/photos/" + pid + "?permanent=true&confirm=" + pid).status_code == 409
    client.delete("/api/v1/photos/" + pid)
    assert client.post("/api/v1/photos/" + pid + "/restore").status_code == 200
    client.delete("/api/v1/photos/" + pid)
    assert client.delete("/api/v1/photos/" + pid + "?permanent=true&confirm=" + pid).status_code == 200
    assert not path.exists()
    with SessionLocal() as db:
        for cls in [PhotoDerivative, PhotoAnalysis, Embedding, Face, EventPhoto, StorySlide]:
            assert db.scalar(select(func.count()).select_from(cls).where(cls.photo_id == pid)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.target_id == pid)) == 0


def test_storage_path_and_symlink_escape(tmp_path):
    local = LocalStorage(tmp_path / "root")
    for key in ["../outside", "/etc/passwd"]:
        with pytest.raises(ValueError):
            local.read_media(key)
    (tmp_path / "root" / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        local.read_media("link/file")


def test_photo_deleted_during_inference_cannot_be_resurrected(client, owner):
    pid = upload(client)["photo_id"]

    def delete_target(kind, **kwargs):
        client.delete("/api/v1/photos/" + pid)
        return fake_provider(kind, **kwargs)

    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.target_id == pid, Job.job_type == "embedding"))
        job.state = "running"
        job.lease_token = "test"
        db.commit()
        run_job(db, job, provider=delete_target)
        assert job.state == "cancelled"
        assert db.scalar(select(func.count()).select_from(Embedding)) == 0
