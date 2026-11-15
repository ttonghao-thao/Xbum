"""Family-only retrieval with explicit, inspectable hard conditions."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
from fastapi import HTTPException
from sqlalchemy import select

from app.db.collection_features import PhotoText
from app.db.models import AudioMemory, Embedding, Event, EventPhoto, Face, Job, Person, PhotoAnalysis, SearchQuery
from app.services.calendar import normalize_search_text, parse_time
from app.services.photos import family_candidates, photo_payload
from app.workers.queue import enqueue

# Vocabulary expansion describes matching, never an inferred location or identity.
CONCEPTS = {
    "海边": ["海边", "海邊", "海水", "沙滩", "海滩", "beach", "sea", "ocean"],
    "合照": ["合照", "合影", "group photo", "group portrait"],
    "截图": ["截图", "截屏", "screenshot"],
    "猫": ["猫", "貓", "cat", "kitten"],
    "狗": ["狗", "dog", "puppy"],
    "公园": ["公园", "公園", "park"],
    "生日": ["生日", "birthday"],
    "美食": ["美食", "食物", "用餐", "food", "meal"],
    "雪": ["雪", "snow"],
}
RELATIONSHIPS = ["妈妈", "爸爸", "爷爷", "奶奶", "外公", "外婆", "姐姐", "妹妹", "哥哥", "弟弟", "母亲", "父亲"]


def _conditions(db, household, params):
    start, end, label = parse_time(params.query, household.timezone)
    start, end = params.start or start, params.end or end
    if start and end and start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    original = normalize_search_text(params.query)
    query = original
    people = list(
        db.scalars(select(Person).where(Person.household_id == household.id, Person.manually_confirmed.is_(True)))
    )
    by_id = {p.id: p for p in people}
    requested = set(params.person_ids + ([params.person_id] if params.person_id else []))
    if requested - set(by_id):
        raise HTTPException(422, "人物条件须为当前家庭已确认的人物")
    unknown = []
    if "上次" in original:
        unknown.append("“上次”尚未对应到确定事件，请指定日期或事件")
    exclude = list(dict.fromkeys(t.strip() for t in params.exclude_terms if t.strip()))
    for match in re.finditer(r"(?:排除|不要|不包含|不含|没有)([^，,。；;\s的]+)", original):
        excluded = match.group(1)
        for filler in ["照片", "图片"]:
            excluded = excluded.replace(filler, "")
        exclude.extend(t for t in re.split(r"和|与|及", excluded) if t)
        query = query.replace(match.group(0), " ")
    excluded_people = set()
    for person in people:
        for value in [person.name, person.relationship]:
            if value and value in exclude:
                excluded_people.add(person.id)
            if value and value in query:
                requested.add(person.id)
                query = query.replace(value, " ")
    if "全家" in original:
        requested |= {p.id for p in people if p.is_family}
        if not requested:
            unknown.append("未标注家庭人物，无法确认全家")
    for relation in RELATIONSHIPS:
        if relation in original and not any(relation == p.relationship or relation == p.name for p in people):
            unknown.append(f"尚未确认人物关系：{relation}")
            query = query.replace(relation, " ")
    mode = params.people_mode
    if "全家" in original:
        mode = "all"
    if excluded_people & requested:
        raise HTTPException(422, "同一个人物不能同时要求出现和排除")
    query = re.sub(
        r"20\d{2}年|(?<!\d)(?:1[0-2]|0?[1-9])月|去年|前年|今年|上个月|这个月|本月|春节|元宵|端午|中秋|春天|春季|夏天|夏季|秋天|秋季|冬天|冬季|昨天|今天",
        " ",
        query,
    )
    hard_terms = list(dict.fromkeys(t.strip() for t in params.terms if t.strip()))
    for concept in CONCEPTS:
        aliases = [concept] + (["合影"] if concept == "合照" else [])
        if any(alias in query for alias in aliases):
            # "A 和 B 的合照" is supported by confirmed co-occurrence, not scene vocabulary alone.
            if not (concept == "合照" and len(requested) >= 2 and mode == "all"):
                hard_terms.append(concept)
            for alias in aliases:
                query = query.replace(alias, " ")
    for word in sorted(
        [
            "帮我找出",
            "帮我找",
            "找出",
            "整理成相册",
            "整理",
            "保存",
            "相册",
            "照片",
            "图片",
            "全家",
            "同时出现",
            "同框",
            "一起",
            "以及",
            "的",
            "在",
            "和",
            "与",
            "去",
            "把",
            "将",
        ],
        key=len,
        reverse=True,
    ):
        query = query.replace(word, " ")
    free_terms = [s for s in re.split(r"[\s，,。；;]+", query) if s]
    return {
        "start": start,
        "end": end,
        "time_label": label,
        "person_ids": sorted(requested),
        "excluded_person_ids": sorted(excluded_people),
        "people_mode": mode,
        "people": [{"id": by_id[pid].id, "name": by_id[pid].name or "已确认人物"} for pid in sorted(requested)],
        "terms": list(dict.fromkeys(hard_terms)),
        "exclude_terms": list(dict.fromkeys(exclude)),
        "free_terms": free_terms,
        "unknown": unknown,
    }


def _matches(term, text):
    alternatives = CONCEPTS.get(term, [term])
    return any(normalize_search_text(t).casefold() in text for t in alternatives)


def search(db, actor, household, params, *, paginate=True, create_query=True):
    conditions = _conditions(db, household, params)
    photos = family_candidates(db, household.id)
    photo_ids = [p.id for p in photos]
    allowed = set(photo_ids)
    by_person = {pid: set() for pid in conditions["person_ids"] + conditions["excluded_person_ids"]}
    for face in db.scalars(select(Face).where(Face.photo_id.in_(photo_ids), Face.person_id.in_(by_person))):
        # When biometrics are disabled only explicit human corrections/tags are identity evidence.
        if household.settings_json["faces_enabled"] or face.locked or face.status == "manual_tag":
            by_person[face.person_id].add(face.photo_id)
    people_photos = None
    if conditions["person_ids"]:
        sets = [by_person[pid] for pid in conditions["person_ids"]]
        people_photos = set.intersection(*sets) if conditions["people_mode"] == "all" else set.union(*sets)
    blocked_people_photos = set().union(*(by_person[pid] for pid in conditions["excluded_person_ids"]))
    events = list(db.scalars(select(Event).where(Event.household_id == household.id)))
    event_map = {e.id: e for e in events}
    if params.event_id and params.event_id not in event_map:
        raise HTTPException(404, "事件不存在或没有权限")
    links = {}
    for link in db.scalars(select(EventPhoto).where(EventPhoto.photo_id.in_(photo_ids))):
        if link.event_id in event_map:
            links.setdefault(link.photo_id, []).append(link.event_id)
    safe_events = set(event_map)
    for link in db.scalars(select(EventPhoto).where(EventPhoto.event_id.in_(event_map))):
        if link.photo_id not in allowed:
            safe_events.discard(link.event_id)
    analysis = {a.photo_id: a for a in db.scalars(select(PhotoAnalysis).where(PhotoAnalysis.photo_id.in_(photo_ids)))}
    texts = {r.photo_id: r for r in db.scalars(select(PhotoText).where(PhotoText.photo_id.in_(photo_ids)))}
    memories = {}
    event_memories = {}
    for memory in db.scalars(
        select(AudioMemory).where(
            AudioMemory.household_id == household.id,
            AudioMemory.transcript_confirmed.is_(True),
            AudioMemory.query_only.is_(False),
        )
    ):
        if memory.photo_id and memory.photo_id not in allowed:
            continue
        if memory.photo_id:
            memories.setdefault(memory.photo_id, []).append(memory.transcript)
        elif memory.event_id:
            # All sources of an event annotation must still be shared.
            event_ids = set(db.scalars(select(EventPhoto.photo_id).where(EventPhoto.event_id == memory.event_id)))
            if event_ids and event_ids <= allowed:
                event_memories.setdefault(memory.event_id, []).append(memory.transcript)
    query_record, job, qvec = None, None, None
    embeddings = {}
    reference = None
    if params.reference_photo_id:
        if params.reference_photo_id not in allowed:
            raise HTTPException(404, "参考照片不存在或不在家庭共享范围")
        reference = db.scalar(
            select(Embedding)
            .where(Embedding.photo_id == params.reference_photo_id)
            .order_by(Embedding.created_at.desc())
        )
        if reference is None:
            raise HTTPException(409, "参考照片尚未完成图像分析")
    elif params.query_id:
        query_record = db.get(SearchQuery, params.query_id)
        if (
            not query_record
            or query_record.household_id != actor.household_id
            or query_record.user_id != actor.user_id
            or query_record.text != params.query
        ):
            raise HTTPException(404, "搜索请求不存在")
        job = db.scalar(select(Job).where(Job.target_id == query_record.id, Job.job_type == "text_embedding"))
    elif create_query and params.semantic and params.query.strip() and household.settings_json["embedding_enabled"]:
        query_record = SearchQuery(household_id=household.id, user_id=actor.user_id, text=params.query)
        db.add(query_record)
        db.flush()
        job = enqueue(db, household.id, "search", query_record.id, "text_embedding", priority=20)
    encoding = reference or query_record
    if encoding and encoding.data_blob:
        qvec = np.frombuffer(encoding.data_blob, dtype=np.float32)
        embeddings = {
            e.photo_id: e
            for e in db.scalars(
                select(Embedding).where(
                    Embedding.photo_id.in_(photo_ids),
                    Embedding.model_id == encoding.model_id,
                    Embedding.model_version == encoding.model_version,
                    Embedding.dimension == encoding.dimension,
                )
            )
        }
    results = []
    for photo in photos:
        if conditions["unknown"]:
            continue
        if photo.id in blocked_people_photos:
            continue
        reasons = []
        taken = (
            datetime.fromisoformat(photo.taken_at).astimezone(ZoneInfo(household.timezone)).date()
            if photo.taken_at
            else None
        )
        start, end = conditions["start"], conditions["end"]
        if (start or end) and (taken is None or (start and taken < start) or (end and taken > end)):
            continue
        if start or end:
            reasons.append(f"拍摄时间：{taken.isoformat()} · {conditions['time_label'] or '指定范围'}")
        if people_photos is not None:
            if photo.id not in people_photos:
                continue
            reasons.append("人工确认人物全部同框" if conditions["people_mode"] == "all" else "人工确认人物任一出现")
        if params.event_id and params.event_id not in links.get(photo.id, []):
            continue
        event_text = []
        for eid in links.get(photo.id, []):
            if eid not in safe_events:
                continue
            e = event_map[eid]
            event_text.append(f"{e.title} {e.location_label if e.location_source != 'unknown' else ''} {e.summary}")
        human = f"{photo.title} {photo.caption_user} {photo.location_user} " + " ".join(event_text)
        if params.location and params.location not in human:
            continue
        a = analysis.get(photo.id)
        ai = (" ".join(a.tags_json) + " " + a.caption_ai) if a else ""
        text_record = texts.get(photo.id)
        recognized = (text_record.corrected_text if text_record.confirmed else text_record.text) if text_record else ""
        oral = " ".join(
            memories.get(photo.id, []) + [t for eid in links.get(photo.id, []) for t in event_memories.get(eid, [])]
        )
        text = normalize_search_text(" ".join([human, ai, recognized or "", oral])).casefold()
        if any(_matches(term, text) for term in conditions["exclude_terms"]):
            continue
        if not all(_matches(term, text) for term in conditions["terms"]):
            continue
        similarity = None
        if qvec is not None and photo.id in embeddings:
            similarity = float(qvec @ np.frombuffer(embeddings[photo.id].data_blob, dtype=np.float32))
        if reference and (photo.id == params.reference_photo_id or similarity is None or similarity < 0.12):
            continue
        free = conditions["free_terms"]
        lexical = bool(free or conditions["terms"]) and all(_matches(term, text) for term in free)
        if free and not lexical and (similarity is None or similarity < 0.12):
            continue
        if lexical or conditions["terms"]:
            query_terms = [*free, *conditions["terms"]]
            if any(_matches(t, normalize_search_text(human).casefold()) for t in query_terms):
                reasons.append("人工说明/事件文字匹配")
            if any(_matches(t, normalize_search_text(ai).casefold()) for t in query_terms):
                reasons.append("AI 推测标签匹配")
            if any(_matches(t, normalize_search_text(recognized or "").casefold()) for t in query_terms):
                reasons.append("图片文字匹配（人工校对）" if text_record.confirmed else "图片文字匹配（本地识别）")
            if any(_matches(t, normalize_search_text(oral).casefold()) for t in query_terms):
                reasons.append("家人确认的口述记忆")
        if similarity is not None and similarity >= 0.12:
            reasons.append("已有图像向量相似" if reference else "SigLIP2 图文向量相似度（候选）")
        results.append(
            {
                **photo_payload(db, photo),
                "match_reasons": reasons or ["结构化条件"],
                "similarity": similarity,
                "score": (0.5 if lexical else 0) + (similarity or 0),
                "text_regions": text_record.regions_json if text_record and recognized else [],
            }
        )
    results.sort(key=lambda row: (-row["score"], row["id"]))
    if create_query:
        db.commit()
    offset = (params.page - 1) * params.page_size
    return {
        "items": results[offset : offset + params.page_size] if paginate else results,
        "total": len(results),
        "page": params.page,
        "page_size": params.page_size,
        "query_id": query_record.id if query_record else None,
        "job_id": job.id if job else None,
        "semantic_state": job.state if job else ("ready" if reference else "disabled"),
        "time_basis": conditions["time_label"],
        "unknown_conditions": conditions["unknown"],
        "parsed_conditions": {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in conditions.items()},
    }
