"""Recommendation choices and grounded asynchronous story composition."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from app.api.schemas import Strict
from app.core.security import audit, current, household, media_actor, scoped, writer
from app.db.models import AudioMemory, Person, Photo, serialize
from app.db.session import get_db
from app.db.story_features import RecommendationRule, StoryNarration, StoryPlan
from app.services.stories import (
    create_composition,
    narration_digest,
    narration_source,
    story_access,
    story_payload,
    invalidate_story_export,
    memory_suggestions,
)
from app.workers.queue import enqueue, enqueue_coalesced

router = APIRouter()


class RuleInput(Strict):
    scope: Literal["personal", "household"] = "personal"
    mode: Literal["less", "never"] = "less"
    target_kind: Literal["person", "date", "location", "theme"]
    target_value: str = Field(default="", max_length=300)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def complete_target(self):
        if self.target_kind == "date":
            if not self.start_date or not self.end_date or self.start_date > self.end_date:
                raise ValueError("请选择有效的日期范围")
        elif not self.target_value:
            raise ValueError("请选择人物或填写地点/主题")
        return self


@router.get("/recommendation-rules")
def rules(actor=Depends(current), db: DB = Depends(get_db)):
    rows = db.scalars(select(RecommendationRule).where(
        RecommendationRule.household_id == actor.household_id,
        or_(RecommendationRule.scope == "household", RecommendationRule.creator_id == actor.user_id),
    ).order_by(RecommendationRule.created_at.desc(), RecommendationRule.id))
    return {"items": [{**serialize(row), "can_delete": row.creator_id == actor.user_id or (
        row.scope == "household" and actor.role == "owner"
    )} for row in rows]}


@router.post("/recommendation-rules")
def add_rule(payload: RuleInput, actor=Depends(writer), db: DB = Depends(get_db)):
    if payload.scope == "household" and actor.role != "owner":
        raise HTTPException(403, "家庭相框推荐规则由管理员设置")
    if payload.target_kind == "person":
        person = scoped(db, Person, payload.target_value, actor)
        if not person.manually_confirmed:
            raise HTTPException(422, "请先人工确认人物身份")
    values = payload.model_dump(mode="json")
    rule = RecommendationRule(household_id=actor.household_id, creator_id=actor.user_id, **values)
    db.add(rule)
    db.flush()
    audit(db, actor, "recommendation_rule_created", "recommendation_rules", rule.id)
    if payload.scope == "household":
        enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return {**serialize(rule), "can_delete": True}


@router.delete("/recommendation-rules/{rid}")
def remove_rule(rid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    rule = scoped(db, RecommendationRule, rid, actor)
    if rule.creator_id != actor.user_id and not (rule.scope == "household" and actor.role == "owner"):
        raise HTTPException(404, "规则不存在或没有权限")
    if rule.scope == "household" and actor.role != "owner":
        raise HTTPException(403, "家庭相框推荐规则由管理员设置")
    is_household = rule.scope == "household"
    db.delete(rule)
    audit(db, actor, "recommendation_rule_deleted", "recommendation_rules", rid)
    if is_household:
        enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return {"ok": True}


@router.get("/memory-suggestions")
def suggestions(actor=Depends(current), db: DB = Depends(get_db)):
    return {"items": memory_suggestions(db, actor, household(db, actor))}


class ComposeInput(Strict):
    event_id: str | None = None
    person_id: str | None = None
    photo_ids: list[str] = Field(default_factory=list, max_length=3000)
    title: str | None = Field(default=None, max_length=300)
    length: Literal["short", "medium", "long"] = "medium"


@router.post("/stories/compose")
def compose(payload: ComposeInput, actor=Depends(writer), db: DB = Depends(get_db)):
    story, job = create_composition(db, actor, **payload.model_dump())
    db.commit()
    return {"story": story_payload(db, story), "job_id": job.id, "status": job.state}


@router.post("/stories/{sid}/compose")
def recompose(sid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    story, _ = story_access(db, sid, actor, edit=True)
    plan = db.scalar(select(StoryPlan).where(StoryPlan.story_id == sid))
    if not plan or story.version != plan.expected_version:
        raise HTTPException(409, "故事已有人工编辑，请保留当前版本或从素材另建故事")
    job = enqueue(db, actor.household_id, "story", sid, "story_compose", key=f"story:{sid}:compose:{story.version}")
    db.commit()
    return {"job_id": job.id, "status": job.state}


class NarrationInput(Strict):
    audio_memory_id: str
    photo_id: str


@router.get("/stories/{sid}/narrations")
def narrations(sid: str, actor=Depends(current), db: DB = Depends(get_db)):
    story, slides = story_access(db, sid, actor, edit=True)
    from app.core.security import eligible

    photo_ids = {s.photo_id for s in slides if (photo := db.get(Photo, s.photo_id))
                 and photo.household_id == actor.household_id and eligible(photo)}
    choices = []
    for memory in db.scalars(select(AudioMemory).where(
        AudioMemory.household_id == actor.household_id,
        AudioMemory.transcript_confirmed.is_(True), AudioMemory.query_only.is_(False)
    )):
        targets = {memory.photo_id} & photo_ids if memory.photo_id else photo_ids if memory.event_id == story.event_id else set()
        valid_targets = []
        for pid in targets:
            candidate = StoryNarration(household_id=actor.household_id, story_id=sid,
                                       audio_memory_id=memory.id, photo_id=pid, source_digest=narration_digest(memory))
            try:
                narration_source(db, story, candidate, photo_ids)
                valid_targets.append(pid)
            except (HTTPException, ValueError):
                continue
        if valid_targets:
            choices.append({"id": memory.id, "transcript": memory.transcript, "photo_ids": sorted(valid_targets)})
    bindings = []
    for binding in db.scalars(select(StoryNarration).where(StoryNarration.story_id == sid)):
        try:
            memory = narration_source(db, story, binding, photo_ids)
            valid, transcript = True, memory.transcript
        except (HTTPException, ValueError):
            valid, transcript = False, "来源已删除、变更或撤回，请移除后重新选择"
        bindings.append({**serialize(binding), "valid": valid, "transcript": transcript})
    return {"items": bindings, "choices": choices}


@router.post("/stories/{sid}/narrations")
def attach_narration(sid: str, payload: NarrationInput, actor=Depends(writer), db: DB = Depends(get_db)):
    story, slides = story_access(db, sid, actor, edit=True)
    memory = scoped(db, AudioMemory, payload.audio_memory_id, actor)
    binding = StoryNarration(household_id=actor.household_id, story_id=sid, audio_memory_id=memory.id,
                             photo_id=payload.photo_id, source_digest=narration_digest(memory))
    narration_source(db, story, binding, {s.photo_id for s in slides})
    existing = db.scalar(select(StoryNarration).where(
        StoryNarration.story_id == sid,
        or_(StoryNarration.photo_id == payload.photo_id, StoryNarration.audio_memory_id == memory.id)
    ))
    if existing:
        raise HTTPException(409, "该照片或录音已有旁白关联，请先移除")
    db.add(binding)
    invalidate_story_export(story)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该照片或录音已有旁白关联，请刷新后重试") from None
    return serialize(binding)


@router.delete("/stories/{sid}/narrations/{nid}")
def detach_narration(sid: str, nid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    story, _ = story_access(db, sid, actor, edit=True)
    binding = scoped(db, StoryNarration, nid, actor)
    if binding.story_id != sid:
        raise HTTPException(404, "旁白不存在")
    db.delete(binding)
    invalidate_story_export(story)
    db.commit()
    return {"ok": True}


@router.get("/stories/{sid}/narrations/{nid}/audio")
def narration_audio(sid: str, nid: str, actor=Depends(media_actor), db: DB = Depends(get_db)):
    story, slides = story_access(db, sid, actor)
    binding = scoped(db, StoryNarration, nid, actor)
    if binding.story_id != sid:
        raise HTTPException(404, "旁白不存在")
    memory = narration_source(db, story, binding, {s.photo_id for s in slides})
    # Reuse the established audio serving/format handling after story-specific authorization.
    from app.api.memories import get_audio

    return get_audio(memory.id, actor, db)
