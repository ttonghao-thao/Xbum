import re
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select

from app.db.models import Embedding, Event, EventPhoto, Face, Job, Person, Photo, PhotoAnalysis, SearchQuery
from app.services.calendar import normalize_search_text, parse_time
from app.services.photos import family_candidates, photo_payload
from app.workers.queue import enqueue


def search(db, actor, household, params):
    inferred_start, inferred_end, label = parse_time(params.query, household.timezone)
    start, end = params.start or inferred_start, params.end or inferred_end
    query = re.sub(
        r"20\d{2}年|去年|前年|今年|上个月|这个月|本月|春节|元宵|端午|中秋", "", normalize_search_text(params.query)
    )
    for word in ["的照片", "照片", "全家", "去年", "去", "的", "在", "和"]:
        query = query.replace(word, " ")
    tokens = [t for t in re.split(r"\s+", query) if t]
    expanded = []
    for token in tokens:
        expanded += [token]
        if "海边" in token:
            expanded += ["海", "沙滩", "海水", "beach", "sea"]
        if "合照" in token or "合影" in token:
            expanded += ["合照", "合影", "group"]
    people = list(
        db.scalars(select(Person).where(Person.household_id == household.id, Person.manually_confirmed.is_(True)))
    )
    named = [p.id for p in people if p.name and p.name in params.query]
    if params.person_id:
        named.append(params.person_id)
    if "全家" in params.query:
        named += [p.id for p in people if p.is_family]
    person_photos = None
    if named:
        person_photos = set(db.scalars(select(Face.photo_id).where(Face.person_id.in_(named))))
    events = list(db.scalars(select(Event).where(Event.household_id == household.id)))
    event_labels = {
        e.id: f"{e.title} {e.location_label if e.location_source != 'unknown' else ''} {e.summary}" for e in events
    }
    links = {}
    event_ids = {e.id for e in events if e.id == params.event_id}
    for link in db.scalars(select(EventPhoto).where(EventPhoto.event_id.in_([e.id for e in events]))):
        links.setdefault(link.photo_id, []).append(link.event_id)
    analysis = {
        a.photo_id: a for a in db.scalars(select(PhotoAnalysis).join(Photo).where(Photo.household_id == household.id))
    }
    query_record = None
    job = None
    if params.query_id:
        query_record = db.get(SearchQuery, params.query_id)
        if (
            not query_record
            or query_record.household_id != actor.household_id
            or query_record.user_id != actor.user_id
            or query_record.text != params.query
        ):
            from fastapi import HTTPException

            raise HTTPException(404, "搜索请求不存在")
        job = db.scalar(select(Job).where(Job.target_id == query_record.id, Job.job_type == "text_embedding"))
    elif params.semantic and params.query.strip() and household.settings_json["embedding_enabled"]:
        query_record = SearchQuery(household_id=household.id, user_id=actor.user_id, text=params.query)
        db.add(query_record)
        db.flush()
        job = enqueue(db, household.id, "search", query_record.id, "text_embedding", priority=20)
    embeddings = {}
    if query_record and query_record.data_blob:
        embeddings = {
            e.photo_id: e
            for e in db.scalars(
                select(Embedding)
                .join(Photo)
                .where(
                    Photo.household_id == household.id,
                    Photo.visibility == "family",
                    Photo.is_hidden.is_(False),
                    Photo.is_deleted.is_(False),
                    Embedding.model_id == query_record.model_id,
                    Embedding.model_version == query_record.model_version,
                    Embedding.dimension == query_record.dimension,
                )
            )
        }
    qvec = np.frombuffer(query_record.data_blob, dtype=np.float32) if query_record and query_record.data_blob else None
    results = []
    for photo in family_candidates(db, household.id):
        reasons = []
        # Unknown capture times never match historical time phrases by upload date.
        taken = (
            datetime.fromisoformat(photo.taken_at).astimezone(ZoneInfo(household.timezone)).date()
            if photo.taken_at
            else None
        )
        if (start or end) and (taken is None or (start and taken < start) or (end and taken > end)):
            continue
        if start or end:
            reasons.append(f"拍摄时间：{taken.isoformat()} · {label or '指定范围'}")
        if person_photos is not None and photo.id not in person_photos:
            continue
        if person_photos is not None:
            reasons.append("人工确认的人物")
        if params.event_id and not any(eid in event_ids for eid in links.get(photo.id, [])):
            continue
        a = analysis.get(photo.id)
        human = f"{photo.title} {photo.caption_user} {photo.location_user} " + " ".join(
            event_labels[eid] for eid in links.get(photo.id, [])
        )
        ai = (" ".join(a.tags_json) + " " + a.caption_ai) if a else ""
        text = normalize_search_text(human + " " + ai).lower()
        if params.location and params.location not in human:
            continue
        lexical = any(t.lower() in text for t in expanded)
        if lexical:
            reasons.append(
                "人工说明/事件文字匹配" if any(t.lower() in human.lower() for t in expanded) else "AI 推测标签匹配"
            )
        similarity = None
        if qvec is not None and photo.id in embeddings:
            similarity = float(qvec @ np.frombuffer(embeddings[photo.id].data_blob, dtype=np.float32))
        if expanded and not lexical and (similarity is None or similarity < 0.12):
            continue
        if similarity is not None and similarity >= 0.12:
            reasons.append("SigLIP2 图文向量相似度（候选）")
        if not reasons and expanded:
            continue
        results.append(
            {
                **photo_payload(db, photo),
                "match_reasons": reasons or ["结构化条件"],
                "similarity": similarity,
                "score": (0.5 if lexical else 0) + (similarity or 0),
            }
        )
    results.sort(key=lambda row: (-row["score"], row["id"]))
    db.commit()
    offset = (params.page - 1) * params.page_size
    return {
        "items": results[offset : offset + params.page_size],
        "total": len(results),
        "page": params.page,
        "query_id": query_record.id if query_record else None,
        "job_id": job.id if job else None,
        "semantic_state": job.state if job else "disabled",
        "time_basis": label,
        "unknown_conditions": ["未标注家庭人物，无法确认全家"] if "全家" in params.query and not named else [],
    }
