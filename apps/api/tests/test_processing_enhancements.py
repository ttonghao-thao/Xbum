from sqlalchemy import select

from app.db.collection_features import PhotoText
from app.db.models import Photo
from app.db.session import SessionLocal
from app.workers.queue import claim, enqueue
from app.workers.worker import run_job
from conftest import upload


def test_ocr_worker_keeps_human_text_and_rechecks_visibility(client, owner):
    pid = upload(client)["photo_id"]
    with SessionLocal() as db:
        db.add(PhotoText(photo_id=pid, corrected_text="人工确认", confirmed=True))
        job = enqueue(db, owner["household"]["id"], "photo", pid, "ocr", key="ocr-test-1")
        db.commit()
        run_job(db, job, provider=lambda kind, **_: {"ok": True, "text": "机器候选", "regions": [], "version": "test-ocr"})
        row = db.scalar(select(PhotoText).where(PhotoText.photo_id == pid))
        assert row.text == "机器候选" and row.corrected_text == "人工确认" and row.confirmed

        def revoke(kind, **_):
            with SessionLocal() as other:
                other.get(Photo, pid).visibility = "private"
                other.commit()
            return {"ok": True, "text": "不应写入", "regions": [], "version": "new-test-ocr"}

        job2 = enqueue(db, owner["household"]["id"], "photo", pid, "ocr", key="ocr-test-2")
        db.commit()
        run_job(db, job2, provider=revoke)
        db.expire_all()
        row = db.scalar(select(PhotoText).where(PhotoText.photo_id == pid))
        assert row.text == "机器候选" and row.corrected_text == "人工确认"
        assert job2.state == "skipped"


def test_resource_limits_defer_background_but_allow_user_query(client, owner, monkeypatch):
    from app.services import system

    monkeypatch.setattr(system, "processing_status", lambda _: {"can_process_background": False})
    with SessionLocal() as db:
        background = enqueue(db, owner["household"]["id"], "photo", "background", "vlm", priority=100)
        query = enqueue(db, owner["household"]["id"], "search", "interactive", "text_embedding", priority=20)
        db.commit()
        claimed = claim(db)
        assert claimed.id == query.id
        assert db.get(type(background), background.id).state == "queued"
