from datetime import date, datetime
from enum import Enum
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def page_items(items, page, size):
    return {"items": items[(page - 1) * size : page * size], "total": len(items), "page": page, "page_size": size}


class Credentials(Strict):
    username: str = Field(min_length=2, max_length=100, pattern=r"^[\w.@+-]+$")
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(default="家人", min_length=1, max_length=100)
    household_name: str = Field(default="我们的家", min_length=1, max_length=100)


class Login(Strict):
    username: str = Field(max_length=100)
    password: str = Field(max_length=256)
    household_id: str | None = None


class InviteInput(Strict):
    role: Literal["member", "viewer"] = "member"
    hours: int = Field(default=24, ge=1, le=168)
    max_uses: int = Field(default=1, ge=1, le=50)


class RoleInput(Strict):
    role: Literal["owner", "member", "viewer"]


class HouseholdPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        if value:
            ZoneInfo(value)
        return value


class PhotoPatch(Strict):
    title: str | None = Field(default=None, max_length=300)
    caption_user: str | None = Field(default=None, max_length=4000)
    taken_at: datetime | None = None
    visibility: Literal["private", "family"] | None = None
    is_hidden: bool | None = None
    location_user: str | None = Field(default=None, max_length=200)

    @field_validator("taken_at")
    @classmethod
    def aware(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("拍摄时间须包含时区")
        return value


class BatchInput(Strict):
    photo_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["favorite", "hide", "delete", "restore", "analyze", "share"]


class FeedbackInput(Strict):
    type: Literal["favorite", "unfavorite", "like", "dislike", "snooze", "hide", "unhide"]


class EventType(str, Enum):
    travel = "travel"
    birthday = "birthday"
    festival = "festival"
    family_gathering = "family_gathering"
    outing = "outing"
    daily = "daily"
    milestone = "milestone"
    other = "other"


class EventPatch(Strict):
    title: str | None = Field(default=None, max_length=300)
    event_type: EventType | None = None
    location_label: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=4000)
    cover_photo_id: str | None = None
    confirmed: bool | None = None
    photo_ids: list[str] | None = Field(default=None, max_length=3000)
    participants_json: list[str] | None = None


class MergeEvents(Strict):
    event_ids: list[str] = Field(min_length=2, max_length=100)
    title: str = Field(default="家庭记忆", min_length=1, max_length=300)


class SplitEvent(Strict):
    photo_ids: list[str] = Field(min_length=1, max_length=3000)
    title: str = Field(min_length=1, max_length=300)


class PersonPatch(Strict):
    name: str | None = Field(default=None, max_length=100)
    relationship: str | None = Field(default=None, max_length=100)
    manually_confirmed: bool | None = None
    consent_flag: bool | None = None
    is_family: bool | None = None
    growth_enabled: bool | None = None
    growth_mode: Literal["auto", "include", "exclude"] | None = None
    birthday: date | None = None


class MergePeople(Strict):
    person_ids: list[str] = Field(min_length=2, max_length=100)


class SplitPeople(Strict):
    person_id: str
    face_ids: list[str] = Field(min_length=1, max_length=1000)


class MilestoneInput(Strict):
    person_id: str
    date: date
    title: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class SlideInput(Strict):
    photo_id: str
    caption: str = Field(default="", max_length=2000)
    duration_s: float = Field(default=6, ge=1, le=60)
    transition: Literal["fade", "cut"] = "fade"


class StoryPatch(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    theme: Literal["warm", "classic", "night"] | None = None
    background: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    ending: str | None = Field(default=None, max_length=2000)
    cover_photo_id: str | None = None
    slides: list[SlideInput] | None = Field(default=None, min_length=1, max_length=200)


class GenerateStory(Strict):
    event_id: str | None = None
    person_id: str | None = None


class SearchInput(Strict):
    query: str = Field(default="", max_length=500)
    start: date | None = None
    end: date | None = None
    person_id: str | None = None
    person_ids: list[str] = Field(default_factory=list, max_length=100)
    people_mode: Literal["all", "any"] = "all"
    terms: list[str] = Field(default_factory=list, max_length=30)
    exclude_terms: list[str] = Field(default_factory=list, max_length=30)
    reference_photo_id: str | None = None
    event_id: str | None = None
    location: str | None = Field(default=None, max_length=200)
    semantic: bool = True
    query_id: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=40, ge=1, le=100)


class TranscriptPatch(Strict):
    transcript: str = Field(max_length=16000)


class PairInput(Strict):
    code: str = Field(min_length=8, max_length=32)


class DeviceInput(Strict):
    name: str = Field(default="家庭相框", min_length=1, max_length=100)


class PlaybackInput(Strict):
    photo_id: str | None = None
    story_id: str | None = None
    action: Literal["play", "skip", "pause", "favorite", "hide", "dislike"] = "play"
    dwell_seconds: float = Field(default=0, ge=0, le=86400)


VISION_CONTRACT_VERSION = "2"


class AnalysisOutput(Strict):
    # No defaults: an incomplete answer must not become a successful analysis
    # filled with invented zeros/empty values by the application.
    scene_tags: list[str] = Field(max_length=30)
    objects: list[str] = Field(max_length=50)
    visible_activity: str = Field(max_length=1000)
    candidate_event_type: EventType
    caption: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    uncertainties: list[str] = Field(max_length=30)


class SettingsPatch(Strict):
    faces_enabled: bool | None = None
    face_consent: bool = False
    delete_biometrics: bool = False
    speech_enabled: bool | None = None
    embedding_enabled: bool | None = None
    vlm_enabled: bool | None = None
    ocr_enabled: bool | None = None
    background_start_hour: int | None = Field(default=None, ge=0, le=23)
    background_end_hour: int | None = Field(default=None, ge=0, le=23)
    min_free_memory_mb: int | None = Field(default=None, ge=0, le=65536)
    min_free_disk_mb: int | None = Field(default=None, ge=0, le=1048576)
    daily_photo_limit: int | None = Field(default=None, ge=10, le=30)
    display_interval_seconds: int | None = Field(default=None, ge=3, le=120)
    quiet_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    autoplay: bool | None = None
    idle_seconds: int | None = Field(default=None, ge=30, le=120)
    recent_days: int | None = Field(default=None, ge=1, le=90)
    quality_floor: float | None = Field(default=None, ge=0, le=1)
    event_gap_hours: float | None = Field(default=None, ge=1, le=24)
    travel_gap_hours: float | None = Field(default=None, ge=24, le=96)
    face_similarity: float | None = Field(default=None, ge=0.1, le=0.95)
    daily_generation_hour: int | None = Field(default=None, ge=0, le=23)
    default_visibility: Literal["private", "family"] | None = None
    scenes: dict[str, bool] | None = None
    weights: dict[str, float] | None = None
