import io
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

ROOT = tempfile.TemporaryDirectory(prefix="xbum-tests-")
os.environ["DATA_DIR"] = ROOT.name
os.environ["DATABASE_URL"] = f"sqlite:///{ROOT.name}/test.sqlite3"

from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.db.models import Job, now  # noqa: E402
from app.main import app  # noqa: E402
from app.workers.queue import claim  # noqa: E402
from app.workers.worker import run_job  # noqa: E402

PASSWORD = "test-only-strong-passphrase"


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def setup(client, username="owner", register=False):
    result = client.post(
        "/api/v1/auth/register" if register else "/api/v1/auth/setup",
        json={
            "username": username,
            "password": PASSWORD,
            "display_name": username,
            "household_name": username + "的家",
        },
    )
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf"]
    return client.get("/api/v1/auth/me").json()


@pytest.fixture
def owner(client):
    return setup(client)


def picture(seed=1, exif=False, size=(800, 600)):
    image = Image.new("RGB", size, ((seed * 57) % 255, (seed * 37) % 255, (seed * 91) % 255))
    draw = ImageDraw.Draw(image)
    for x in range(10, size[0], 25):
        draw.line((x, 0, (x + seed * 13) % size[0], size[1]), fill=(240, 240, 210), width=3)
    draw.rectangle((20, 30, 80 + seed % 80, 150), fill="red")
    data = io.BytesIO()
    if exif:
        metadata = Image.Exif()
        metadata[36867] = "2025:02:01 12:30:00"
        metadata[274] = 6
        image.save(data, "JPEG", exif=metadata)
    else:
        image.save(data, "PNG")
    return data.getvalue()


def upload(client, seed=1, shared=True, data=None, exif=False):
    result = client.post(
        "/api/v1/photos/upload",
        data={"shared": str(shared).lower()},
        files={
            "file": (
                "photo.jpg" if exif else "photo.png",
                data or picture(seed, exif),
                "image/jpeg" if exif else "image/png",
            )
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def fake_provider(kind, **kwargs):
    if kind in {"embedding", "text_embedding"}:
        return {"ok": True, "vector": [0.8, 0.6, 0, 0], "model": "test-only-siglip", "version": "fake-1"}
    if kind == "vlm":
        return {
            "ok": True,
            "version": "fake-vision-1",
            "analysis": {
                "scene_tags": ["海边", "合影"],
                "objects": ["沙滩"],
                "visible_activity": "几个人合影",
                "candidate_event_type": "travel",
                "caption": "海边合影（测试提供者）",
                "confidence": 0.7,
                "uncertainties": ["无法确认地点"],
            },
        }
    if kind == "faces":
        return {"ok": True, "faces": [{"bbox": [5, 5, 40, 40], "quality": 0.9, "vector": [0.8, 0.6, 0, 0]}]}
    if kind == "asr":
        return {"ok": True, "transcript": "测试口述记忆", "version": "fake-asr-1"}
    raise AssertionError(kind)


def drain(maximum=500, provider=fake_provider):
    from sqlalchemy import update

    with SessionLocal() as db:
        for _ in range(maximum):
            db.execute(
                update(Job).where(Job.state == "queued", Job.target_type == "household").values(next_run_at=now())
            )
            db.commit()
            job = claim(db)
            if not job:
                return
            run_job(db, job, provider=provider)
        raise AssertionError("queue did not drain")
