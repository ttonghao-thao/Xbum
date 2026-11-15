from functools import partial

import httpx
import pytest
from sqlalchemy import delete, select

from app.ai import providers
from app.api.schemas import VISION_CONTRACT_VERSION
from app.db.models import Household, Job, Photo, PhotoAnalysis, now
from app.db.session import SessionLocal
from app.workers.queue import claim, reconcile_vision
from app.workers.worker import run_job
from conftest import drain, fake_provider, picture, upload


@pytest.mark.parametrize("mode", ["complete", "missing_fields", "truncated"])
def test_local_vision_requires_complete_bounded_output(monkeypatch, tmp_path, mode):
    image = tmp_path / "synthetic.png"
    image.write_bytes(picture())
    monkeypatch.setattr(providers.settings, "model_dir", tmp_path)
    complete = fake_provider("vlm")["analysis"]

    def handle(request):
        import json

        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": providers.settings.vlm_model, "digest": "test"}]})
        body = json.loads(request.content)
        assert set(body["format"]["required"]) == set(complete)
        assert body["format"]["properties"]["objects"]["maxItems"] == 6
        assert body["keep_alive"] == 0
        return httpx.Response(
            200,
            json={
                "response": json.dumps({} if mode == "missing_fields" else complete),
                "done": True,
                "done_reason": "length" if mode == "truncated" else "stop",
            },
        )

    original = httpx.Client
    monkeypatch.setattr(providers.httpx, "Client", partial(original, transport=httpx.MockTransport(handle)))
    if mode == "complete":
        assert providers.OllamaVision().analyze_photo(image)["analysis"] == complete
    else:
        from pydantic import ValidationError

        with pytest.raises((providers.InvalidModelOutput, ValidationError)):
            providers.OllamaVision().analyze_photo(image)


def test_old_missing_and_new_photos_reconcile_once_and_preserve_human_edits(client, owner):
    old, valid, missing = [upload(client, i, False, exif=True)["photo_id"] for i in range(3)]
    drain()
    client.patch("/api/v1/photos/" + old, json={"caption_user": "人工内容不可覆盖"})
    with SessionLocal() as db:
        h = db.get(Household, owner["household"]["id"])
        for pid in (old, missing):
            job = db.scalar(select(Job).where(Job.target_id == pid, Job.job_type == "vlm"))
            job.idempotency_key = f"legacy:{pid}:vlm:1"
        analysis = db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == old))
        analysis.pipeline_version, analysis.status, analysis.analysis_json = "1", "succeeded", {"confidence": 0}
        db.execute(delete(PhotoAnalysis).where(PhotoAnalysis.photo_id == missing))
        db.commit()
        assert reconcile_vision(db, h) == 2
        db.commit()
        assert reconcile_vision(db, h) == 0
        jobs = list(db.scalars(select(Job).where(Job.job_type == "vlm", Job.state == "queued")))
        assert {j.target_id for j in jobs} == {old, missing}
        assert all(f":vision-{VISION_CONTRACT_VERSION}" in j.idempotency_key for j in jobs)
    drain()
    with SessionLocal() as db:
        assert reconcile_vision(db, db.get(Household, owner["household"]["id"])) == 0
        for pid in (old, valid, missing):
            assert (
                db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == pid)).pipeline_version
                == VISION_CONTRACT_VERSION
            )
        assert db.get(Photo, old).caption_user == "人工内容不可覆盖"
    new = upload(client, 9, False)["photo_id"]
    with SessionLocal() as db:
        assert reconcile_vision(db, db.get(Household, owner["household"]["id"])) == 0
        assert len(list(db.scalars(select(Job).where(Job.target_id == new, Job.job_type == "vlm")))) == 1


def test_reconciliation_respects_pause_switch_and_bounded_failures(client, owner):
    pid = upload(client, shared=False)["photo_id"]
    with SessionLocal() as db:
        # Simulate a historic failed task, with no job for the new contract yet.
        db.execute(delete(Job).where(Job.target_id == pid))
        h = db.get(Household, owner["household"]["id"])
        h.settings_json = {**h.settings_json, "jobs_paused": True}
        db.commit()
        assert reconcile_vision(db, h) == 0
        h.settings_json = {**h.settings_json, "jobs_paused": False, "vlm_enabled": False}
        db.commit()
        assert reconcile_vision(db, h) == 0
        h.settings_json = {**h.settings_json, "vlm_enabled": True}
        db.commit()
        assert reconcile_vision(db, h) == 1
        db.commit()
        for _ in range(3):
            job = db.scalar(select(Job).where(Job.target_id == pid, Job.job_type == "vlm"))
            job.next_run_at = now()
            db.commit()
            job = claim(db)
            run_job(db, job, provider=lambda *a, **k: {"ok": False, "code": "invalid_model_json"})
        assert job.state == "failed" and job.attempts == 3
        assert reconcile_vision(db, h) == 0  # A periodic tick cannot reset attempts.
    result = client.get("/api/v1/albums/growth").json()
    assert result["failed_count"] == 1 and result["pending_count"] == 0
