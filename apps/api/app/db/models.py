from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import defaults
from app.db.session import Base


def uid():
    return uuid4().hex


def now():
    return datetime.now(UTC).isoformat()


def fk(table, **kwargs):
    return mapped_column(String(32), ForeignKey(f"{table}.id", ondelete="CASCADE"), **kwargs)


class Identity:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)


class Household(Identity, Base):
    __tablename__ = "households"
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(default="Asia/Shanghai")
    settings_json: Mapped[dict] = mapped_column(JSON, default=defaults)
    created_at: Mapped[str] = mapped_column(default=now)
    privacy_revision: Mapped[int] = mapped_column(default=1)


class User(Identity, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(default=now)


class Membership(Identity, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("household_id", "user_id"),
        CheckConstraint("role IN ('owner','member','viewer')"),
    )
    household_id: Mapped[str] = fk("households", index=True)
    user_id: Mapped[str] = fk("users")
    role: Mapped[str] = mapped_column(default="member")
    status: Mapped[str] = mapped_column(default="active")


class Invitation(Identity, Base):
    __tablename__ = "invitations"
    household_id: Mapped[str] = fk("households", index=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    role: Mapped[str] = mapped_column(default="member")
    expires_at: Mapped[str]
    max_uses: Mapped[int] = mapped_column(default=1)
    used_count: Mapped[int] = mapped_column(default=0)
    revoked: Mapped[bool] = mapped_column(default=False)


class DisplayDevice(Identity, Base):
    __tablename__ = "display_devices"
    household_id: Mapped[str] = fk("households", index=True)
    token_hash: Mapped[str | None] = mapped_column(unique=True)
    pair_hash: Mapped[str | None]
    expires_at: Mapped[str]
    name: Mapped[str]
    paired_at: Mapped[str | None]
    revoked_at: Mapped[str | None]


class Session(Identity, Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(unique=True)
    user_id: Mapped[str] = fk("users")
    household_id: Mapped[str | None] = fk("households", nullable=True)
    csrf: Mapped[str]
    expires_at: Mapped[str]


class LoginAttempt(Identity, Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(index=True)
    created_at: Mapped[str] = mapped_column(default=now, index=True)


class Photo(Identity, Base):
    __tablename__ = "photos"
    __table_args__ = (
        UniqueConstraint("household_id", "uploader_id", "sha256"),
        Index("ix_photos_household_taken", "household_id", "taken_at"),
        CheckConstraint("visibility IN ('private','family')"),
    )
    household_id: Mapped[str] = fk("households", index=True)
    uploader_id: Mapped[str] = fk("users")
    storage_key: Mapped[str]
    sha256: Mapped[str]
    phash: Mapped[str | None]
    duplicate_group: Mapped[str | None] = mapped_column(index=True)
    mime: Mapped[str]
    width: Mapped[int]
    height: Mapped[int]
    taken_at: Mapped[str | None]
    capture_time_source: Mapped[str] = mapped_column(default="upload_uncertain")
    gps_lat: Mapped[float | None]
    gps_lon: Mapped[float | None]
    visibility: Mapped[str] = mapped_column(default="private")
    is_hidden: Mapped[bool] = mapped_column(default=False)
    is_deleted: Mapped[bool] = mapped_column(default=False)
    title: Mapped[str] = mapped_column(default="")
    caption_user: Mapped[str] = mapped_column(default="")
    location_user: Mapped[str] = mapped_column(default="")
    locked_fields: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(default=now)


class PhotoDerivative(Identity, Base):
    __tablename__ = "photo_derivatives"
    __table_args__ = (UniqueConstraint("photo_id", "variant"),)
    photo_id: Mapped[str] = fk("photos", index=True)
    variant: Mapped[str]
    storage_key: Mapped[str]
    width: Mapped[int]
    height: Mapped[int]
    status: Mapped[str] = mapped_column(default="ready")


class PhotoAnalysis(Identity, Base):
    __tablename__ = "photo_analyses"
    photo_id: Mapped[str] = fk("photos", unique=True)
    model_name: Mapped[str] = mapped_column(default="")
    model_version: Mapped[str] = mapped_column(default="")
    pipeline_version: Mapped[str] = mapped_column(default="1")
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    caption_ai: Mapped[str] = mapped_column(default="")
    analysis_json: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_score: Mapped[float | None]
    quality_reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(default="pending")
    updated_at: Mapped[str] = mapped_column(default=now)


class Embedding(Identity, Base):
    __tablename__ = "embeddings"
    __table_args__ = (UniqueConstraint("photo_id", "model_version"),)
    photo_id: Mapped[str] = fk("photos", index=True)
    model_id: Mapped[str]
    model_version: Mapped[str]
    dimension: Mapped[int]
    data_blob: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[str] = mapped_column(default=now)


class Person(Identity, Base):
    __tablename__ = "people"
    household_id: Mapped[str] = fk("households", index=True)
    name: Mapped[str | None]
    relationship: Mapped[str | None]
    consent_flag: Mapped[bool] = mapped_column(default=False)
    manually_confirmed: Mapped[bool] = mapped_column(default=False)
    is_family: Mapped[bool] = mapped_column(default=False)
    growth_enabled: Mapped[bool] = mapped_column(default=False)
    growth_mode: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto")
    birthday: Mapped[str | None]


class Face(Identity, Base):
    __tablename__ = "faces"
    photo_id: Mapped[str] = fk("photos", index=True)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"), index=True)
    bounding_box_json: Mapped[list] = mapped_column(JSON)
    embedding_blob: Mapped[bytes | None] = mapped_column(LargeBinary)
    quality: Mapped[float]
    cluster_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="candidate")
    locked: Mapped[bool] = mapped_column(default=False)


class Event(Identity, Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_household_start", "household_id", "start_at"),)
    household_id: Mapped[str] = fk("households", index=True)
    title: Mapped[str]
    event_type: Mapped[str] = mapped_column(default="daily")
    start_at: Mapped[str | None]
    end_at: Mapped[str | None]
    location_label: Mapped[str] = mapped_column(default="")
    location_source: Mapped[str] = mapped_column(default="unknown")
    summary: Mapped[str] = mapped_column(default="")
    cover_photo_id: Mapped[str | None] = mapped_column(ForeignKey("photos.id", ondelete="SET NULL"))
    confirmed: Mapped[bool] = mapped_column(default=False)
    locked: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=1)
    confidence: Mapped[float] = mapped_column(default=0)
    segments_json: Mapped[list] = mapped_column(JSON, default=list)
    participants_json: Mapped[list] = mapped_column(JSON, default=list)


class EventPhoto(Identity, Base):
    __tablename__ = "event_photos"
    __table_args__ = (UniqueConstraint("event_id", "photo_id"),)
    event_id: Mapped[str] = fk("events", index=True)
    photo_id: Mapped[str] = fk("photos", index=True)
    position: Mapped[int]
    source: Mapped[str] = mapped_column(default="auto")


class Milestone(Identity, Base):
    __tablename__ = "milestones"
    household_id: Mapped[str] = fk("households", index=True)
    person_id: Mapped[str] = fk("people")
    date: Mapped[str]
    title: Mapped[str]
    note: Mapped[str] = mapped_column(default="")
    confirmed: Mapped[bool] = mapped_column(default=True)


class Story(Identity, Base):
    __tablename__ = "stories"
    household_id: Mapped[str] = fk("households", index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    title: Mapped[str]
    theme: Mapped[str] = mapped_column(default="warm")
    background: Mapped[str] = mapped_column(default="#251e1b")
    ending: Mapped[str] = mapped_column(default="让这些真实的瞬间，成为我们共同的记忆。")
    cover_photo_id: Mapped[str | None] = mapped_column(ForeignKey("photos.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(default="draft")
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[str] = fk("users")
    generated_by_ai: Mapped[bool] = mapped_column(default=False)
    mp4_key: Mapped[str | None]
    export_version: Mapped[int | None]
    created_at: Mapped[str] = mapped_column(default=now)


class StorySlide(Identity, Base):
    __tablename__ = "story_slides"
    story_id: Mapped[str] = fk("stories", index=True)
    photo_id: Mapped[str] = fk("photos", index=True)
    position: Mapped[int]
    duration_s: Mapped[float] = mapped_column(default=6.0)
    caption: Mapped[str] = mapped_column(default="")
    transition: Mapped[str] = mapped_column(default="fade")


class AudioMemory(Identity, Base):
    __tablename__ = "audio_memories"
    household_id: Mapped[str] = fk("households", index=True)
    photo_id: Mapped[str | None] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"))
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    uploader_id: Mapped[str] = fk("users")
    audio_key: Mapped[str]
    transcript: Mapped[str] = mapped_column(default="")
    transcript_confirmed: Mapped[bool] = mapped_column(default=False)
    query_only: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)


class Playlist(Identity, Base):
    __tablename__ = "playlists"
    household_id: Mapped[str] = fk("households", index=True)
    playlist_date: Mapped[str] = mapped_column(index=True)
    generator_version: Mapped[str] = mapped_column(default="weighted-mmr-1")
    config_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class PlaylistItem(Identity, Base):
    __tablename__ = "playlist_items"
    playlist_id: Mapped[str] = fk("playlists", index=True)
    photo_id: Mapped[str | None] = fk("photos", index=True)
    story_id: Mapped[str | None] = fk("stories")
    position: Mapped[int]
    score: Mapped[float]
    reason_codes_json: Mapped[list] = mapped_column(JSON)
    scene_type: Mapped[str]
    play_count: Mapped[int] = mapped_column(default=0)


class PlaybackEvent(Identity, Base):
    __tablename__ = "playback_events"
    household_id: Mapped[str] = fk("households", index=True)
    display_device_id: Mapped[str | None] = mapped_column(ForeignKey("display_devices.id", ondelete="SET NULL"))
    photo_id: Mapped[str | None] = fk("photos", index=True)
    story_id: Mapped[str | None] = fk("stories")
    played_at: Mapped[str] = mapped_column(default=now, index=True)
    dwell_seconds: Mapped[float] = mapped_column(default=0)
    action: Mapped[str] = mapped_column(default="play")


class Feedback(Identity, Base):
    __tablename__ = "feedback"
    household_id: Mapped[str] = fk("households", index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    photo_id: Mapped[str] = fk("photos", index=True)
    type: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)
    until_at: Mapped[str | None]


class Job(Identity, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_state_next", "state", "next_run_at"),)
    household_id: Mapped[str] = fk("households", index=True)
    target_type: Mapped[str]
    target_id: Mapped[str] = mapped_column(index=True)
    job_type: Mapped[str]
    state: Mapped[str] = mapped_column(default="queued")
    priority: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    lease_until: Mapped[str | None]
    lease_token: Mapped[str | None]
    next_run_at: Mapped[str] = mapped_column(default=now)
    error_code: Mapped[str | None]
    error_message_sanitized: Mapped[str | None]
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(default="")
    duration_ms: Mapped[int | None]
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)


class SearchQuery(Identity, Base):
    __tablename__ = "search_queries"
    household_id: Mapped[str] = fk("households", index=True)
    user_id: Mapped[str] = fk("users")
    text: Mapped[str]
    model_id: Mapped[str] = mapped_column(default="")
    model_version: Mapped[str] = mapped_column(default="")
    dimension: Mapped[int] = mapped_column(default=0)
    data_blob: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[str] = mapped_column(default=now)


class AuditLog(Identity, Base):
    __tablename__ = "audit_logs"
    household_id: Mapped[str | None] = mapped_column(ForeignKey("households.id", ondelete="SET NULL"), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str]
    object_type: Mapped[str]
    object_id: Mapped[str]
    time: Mapped[str] = mapped_column(default=now)
    metadata_sanitized: Mapped[dict] = mapped_column(JSON, default=dict)


SENSITIVE = {
    "password_hash",
    "token_hash",
    "pair_hash",
    "csrf",
    "data_blob",
    "embedding_blob",
    "storage_key",
    "audio_key",
}


def serialize(obj, exclude=()):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name not in SENSITIVE | set(exclude)}


# Register optional feature tables after the shared identity and core models.
from app.db import album_features, collection_features, story_features  # noqa: E402,F401
