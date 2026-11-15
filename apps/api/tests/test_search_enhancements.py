from datetime import date

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.schemas import SearchInput
from app.ai.ocr import OCR_VERSION, reconcile_ocr
from app.core.security import Actor
from app.db.collection_features import PhotoText, SmartCollection
from app.db.models import AudioMemory, Embedding, Household, Job, Photo, SearchQuery
from app.db.session import SessionLocal
from app.main import app
from app.services.calendar import parse_time
from app.services.search import search
from conftest import setup, upload


def picture_info(db, pid, *, caption="海边合影", taken="2025-07-05T04:00:00+00:00"):
    p = db.get(Photo, pid)
    p.caption_user, p.taken_at, p.capture_time_source = caption, taken, "user"
    return p


def person(client, name, relationship=None):
    response = client.post(
        "/api/v1/people",
        json={"name": name, "relationship": relationship, "manually_confirmed": True, "is_family": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_season_and_explicit_month_dates():
    assert parse_time("去年夏天", "Asia/Shanghai", date(2026, 11, 15))[:2] == (date(2025, 6, 1), date(2025, 8, 31))
    assert parse_time("2024年2月", "Asia/Shanghai", date(2026, 11, 15))[:2] == (date(2024, 2, 1), date(2024, 2, 29))
    assert parse_time("去年冬天", "Asia/Shanghai", date(2026, 11, 15))[:2] == (date(2025, 12, 1), date(2026, 2, 28))


def test_people_intersection_time_semantic_cannot_relax_conditions(client, owner):
    a, b = person(client, "小明"), person(client, "阿兰", "妈妈")
    ids = [upload(client, i)["photo_id"] for i in range(1, 5)]
    for pid in ids:
        assert client.post(f"/api/v1/people/{a}/photos/{pid}").status_code == 200
    for pid in ids[1:]:
        assert client.post(f"/api/v1/people/{b}/photos/{pid}").status_code == 200
    with SessionLocal() as db:
        for pid in ids:
            picture_info(db, pid)
        picture_info(db, ids[2], caption="室内合影")
        picture_info(db, ids[3], taken="2023-07-05T04:00:00+00:00")
        db.commit()
    response = client.post("/api/v1/search", json={"query": "去年夏天小明和妈妈在海边的合照", "semantic": False})
    assert response.status_code == 200, response.text
    assert [p["id"] for p in response.json()["items"]] == [ids[1]]
    assert set(response.json()["parsed_conditions"]["person_ids"]) == {a, b}
    result = client.post(
        "/api/v1/search", json={"query": "海边", "person_ids": [a, b], "people_mode": "any", "semantic": False}
    ).json()
    assert {p["id"] for p in result["items"]} == {ids[0], ids[1], ids[3]}
    with SessionLocal() as db:
        q = SearchQuery(
            household_id=owner["household"]["id"],
            user_id=owner["user"]["id"],
            text="海边",
            model_id="test",
            model_version="1",
            dimension=2,
            data_blob=np.array([1, 0], dtype=np.float32).tobytes(),
        )
        db.add(q)
        for pid in ids:
            db.add(
                Embedding(
                    photo_id=pid,
                    model_id="test",
                    model_version="1",
                    dimension=2,
                    data_blob=np.array([1, 0], dtype=np.float32).tobytes(),
                )
            )
        db.commit()
        result = search(
            db,
            Actor(owner["user"]["id"], owner["household"]["id"], "owner"),
            db.get(Household, owner["household"]["id"]),
            SearchInput(
                query="海边",
                query_id=q.id,
                person_ids=[a, b],
                terms=["海边"],
                start=date(2025, 6, 1),
                end=date(2025, 8, 31),
            ),
        )
        assert [p["id"] for p in result["items"]] == [ids[1]]


def test_unknown_family_relationship_negation_and_confirmed_audio(client, owner):
    pid = upload(client)["photo_id"]
    with SessionLocal() as db:
        picture_info(db, pid, caption="海边截图")
        db.add(
            AudioMemory(
                household_id=owner["household"]["id"],
                photo_id=pid,
                uploader_id=owner["user"]["id"],
                audio_key="not-read-by-search",
                transcript="老院子的合欢树",
                transcript_confirmed=True,
            )
        )
        db.commit()
    assert client.post("/api/v1/search", json={"query": "海边 排除截图", "semantic": False}).json()["items"] == []
    result = client.post("/api/v1/search", json={"query": "全家", "semantic": False}).json()
    assert result["items"] == [] and result["unknown_conditions"]
    assert client.post("/api/v1/search", json={"query": "妈妈", "semantic": False}).json()["items"] == []
    result = client.post("/api/v1/search", json={"query": "合欢树", "semantic": False}).json()
    assert result["items"][0]["id"] == pid
    assert "家人确认的口述记忆" in result["items"][0]["match_reasons"]
    client.patch(f"/api/v1/photos/{pid}", json={"visibility": "private"})
    assert client.post("/api/v1/search", json={"query": "合欢树", "semantic": False}).json()["items"] == []


def test_reference_search_permission_and_model_matching(client, owner):
    pids = [upload(client, i)["photo_id"] for i in range(1, 4)]
    with SessionLocal() as db:
        for index, pid in enumerate(pids):
            db.add(
                Embedding(
                    photo_id=pid,
                    model_id="test",
                    model_version="1" if index < 2 else "2",
                    dimension=2,
                    data_blob=np.array([1, 0], dtype=np.float32).tobytes(),
                )
            )
        db.commit()
    result = client.post("/api/v1/search", json={"reference_photo_id": pids[0], "semantic": False}).json()
    assert [p["id"] for p in result["items"]] == [pids[1]]
    client.patch(f"/api/v1/photos/{pids[0]}", json={"visibility": "private"})
    assert client.post("/api/v1/search", json={"reference_photo_id": pids[0]}).status_code == 404
    with TestClient(app) as other:
        setup(other, "other", True)
        assert other.post("/api/v1/search", json={"reference_photo_id": pids[1]}).status_code == 404


def test_ocr_index_correction_survives_reconciliation_and_withdrawal(client, owner):
    pid = upload(client)["photo_id"]
    private = upload(client, 2, shared=False)["photo_id"]
    with SessionLocal() as db:
        db.add(
            PhotoText(
                photo_id=pid,
                text="北京站",
                regions_json=[{"text": "北京站", "bbox": [0.1, 0.2, 0.5, 0.1]}],
                model_version=OCR_VERSION,
            )
        )
        db.commit()
        h = db.get(Household, owner["household"]["id"])
        assert reconcile_ocr(db, h) == 0
    response = client.post("/api/v1/search", json={"query": "北京站", "semantic": False})
    assert response.json()["items"][0]["text_regions"]
    assert client.patch(f"/api/v1/photos/{pid}/text", json={"text": "上海站"}).status_code == 200
    assert client.post("/api/v1/search", json={"query": "北京站", "semantic": False}).json()["total"] == 0
    assert client.post("/api/v1/search", json={"query": "上海站", "semantic": False}).json()["total"] == 1
    assert client.post(f"/api/v1/photos/{private}/ocr").status_code == 422
    client.patch(f"/api/v1/photos/{pid}", json={"visibility": "private"})
    assert client.post("/api/v1/search", json={"query": "上海站", "semantic": False}).json()["total"] == 0


def test_saved_dynamic_collection_overrides_privacy_and_deletion(client, owner):
    a, b = [upload(client, i)["photo_id"] for i in (1, 2)]
    with SessionLocal() as db:
        picture_info(db, a, caption="海边")
        picture_info(db, b, caption="室内")
        db.commit()
    result = client.post(
        "/api/v1/collections", json={"title": "海边回顾", "mode": "dynamic", "query": {"query": "海边"}}
    )
    assert result.status_code == 200, result.text
    cid = result.json()["id"]
    assert result.json()["total"] == 1
    client.patch(f"/api/v1/collections/{cid}", json={"include_ids": [b], "exclude_ids": [a]})
    items = client.get(f"/api/v1/collections/{cid}").json()["items"]
    assert [p["id"] for p in items] == [b]
    with SessionLocal() as db:
        picture_info(db, a, caption="海边旅行")
        db.commit()
    assert [p["id"] for p in client.get(f"/api/v1/collections/{cid}/playback").json()["items"]] == [b]
    with TestClient(app) as other:
        setup(other, "other", True)
        assert other.get(f"/api/v1/collections/{cid}").status_code == 404
    client.patch(f"/api/v1/photos/{b}", json={"visibility": "private"})
    assert client.get(f"/api/v1/collections/{cid}/playback").json()["items"] == []
    assert client.get(f"/api/v1/collections/{cid}").json()["include_ids_json"] == []
    assert client.delete(f"/api/v1/collections/{cid}").status_code == 200
    assert client.get(f"/api/v1/photos/{a}").status_code == 200
    with SessionLocal() as db:
        assert db.get(SmartCollection, cid) is None
        assert not list(db.scalars(select(Job).where(Job.job_type == "text_embedding")))


def test_excluded_person_and_unresolved_time_are_hard_conditions(client, owner):
    a = person(client, "小明")
    p1, p2 = [upload(client, i)["photo_id"] for i in (1, 2)]
    client.post(f"/api/v1/people/{a}/photos/{p1}")
    with SessionLocal() as db:
        picture_info(db, p1)
        picture_info(db, p2)
        db.commit()
    result = client.post("/api/v1/search", json={"query": "海边 不含小明的照片", "semantic": False}).json()
    assert [p["id"] for p in result["items"]] == [p2]
    assert result["parsed_conditions"]["excluded_person_ids"] == [a]
    result = client.post("/api/v1/search", json={"query": "上次去海边", "semantic": False}).json()
    assert result["items"] == [] and result["unknown_conditions"]


def test_ocr_missing_versions_are_enqueued_once_and_preserve_human_text(client, owner):
    pid = upload(client)["photo_id"]
    with SessionLocal() as db:
        row = PhotoText(photo_id=pid, text="旧识别", corrected_text="人工保留", confirmed=True, model_version="old")
        db.add(row)
        db.commit()
        h = db.get(Household, owner["household"]["id"])
        assert reconcile_ocr(db, h) == 1
        db.commit()
        assert reconcile_ocr(db, h) == 0
        assert row.corrected_text == "人工保留"
        assert len(list(db.scalars(select(Job).where(Job.job_type == "ocr")))) == 1


def test_collection_retains_invisible_overrides_when_editing_visible_photos(client, owner):
    a, b = [upload(client, i)["photo_id"] for i in (1, 2)]
    result = client.post("/api/v1/collections", json={"title": "人工选择", "mode": "dynamic", "query": {"query": ""}})
    cid = result.json()["id"]
    client.patch(f"/api/v1/collections/{cid}", json={"exclude_ids": [a]})
    client.patch(f"/api/v1/photos/{a}", json={"visibility": "private"})
    assert client.get(f"/api/v1/collections/{cid}").json()["exclude_ids_json"] == []
    client.patch(f"/api/v1/collections/{cid}", json={"exclude_ids": [b]})
    client.patch(f"/api/v1/photos/{a}", json={"visibility": "family"})
    detail = client.get(f"/api/v1/collections/{cid}").json()
    assert set(detail["exclude_ids_json"]) == {a, b}
    assert detail["items"] == []


def test_saved_rule_with_merged_person_becomes_empty_not_every_photo(client, owner):
    a = person(client, "孩子")
    pid = upload(client)["photo_id"]
    client.post(f"/api/v1/people/{a}/photos/{pid}")
    result = client.post(
        "/api/v1/collections", json={"title": "孩子的照片", "mode": "dynamic", "query": {"person_ids": [a]}}
    )
    assert result.status_code == 200
    cid = result.json()["id"]
    with SessionLocal() as db:
        from app.db.models import Person

        db.delete(db.get(Person, a))
        db.commit()
    detail = client.get(f"/api/v1/collections/{cid}").json()
    assert detail["items"] == [] and detail["rule_warning"]
    assert client.get("/api/v1/collections").status_code == 200
