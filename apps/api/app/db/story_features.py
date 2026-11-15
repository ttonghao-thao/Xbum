"""Persistent recommendation choices and grounded story source records."""
from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Identity, fk, now
from app.db.session import Base


class RecommendationRule(Identity, Base):
    __tablename__ = "recommendation_rules"
    __table_args__ = (
        CheckConstraint("scope IN ('personal','household')"),
        CheckConstraint("mode IN ('less','never')"),
        CheckConstraint("target_kind IN ('person','date','location','theme')"),
    )
    household_id: Mapped[str] = fk("households", index=True)
    creator_id: Mapped[str] = fk("users", index=True)
    scope: Mapped[str]
    mode: Mapped[str]
    target_kind: Mapped[str]
    target_value: Mapped[str] = mapped_column(String(300), default="")
    start_date: Mapped[str | None]
    end_date: Mapped[str | None]
    created_at: Mapped[str] = mapped_column(default=now)


class StoryPlan(Identity, Base):
    __tablename__ = "story_plans"
    __table_args__ = (UniqueConstraint("story_id"),)
    household_id: Mapped[str] = fk("households", index=True)
    story_id: Mapped[str] = fk("stories", index=True)
    length: Mapped[str] = mapped_column(default="medium")
    expected_version: Mapped[int] = mapped_column(default=1)
    source_photo_ids: Mapped[list] = mapped_column(JSON, default=list)
    chapters_json: Mapped[list] = mapped_column(JSON, default=list)
    sources_json: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(default="queued")
    generator: Mapped[str] = mapped_column(default="grounded-selection-1")


class StoryNarration(Identity, Base):
    __tablename__ = "story_narrations"
    __table_args__ = (UniqueConstraint("story_id", "audio_memory_id"), UniqueConstraint("story_id", "photo_id"))
    household_id: Mapped[str] = fk("households", index=True)
    story_id: Mapped[str] = fk("stories", index=True)
    audio_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("audio_memories.id", ondelete="SET NULL"), index=True
    )
    photo_id: Mapped[str] = fk("photos", index=True)
    source_digest: Mapped[str]
