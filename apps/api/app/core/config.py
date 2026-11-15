from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    web_port: int = 5173
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///./data/album.sqlite3"
    model_dir: Path = Path("models")
    default_household_timezone: str = "Asia/Shanghai"
    ollama_base_url: str = "http://127.0.0.1:11434"
    vlm_model: str = "qwen3-vl:2b-instruct"
    siglip_model: str = "google/siglip2-base-patch16-224"
    max_upload_mb: int = 30
    daily_photo_limit: int = Field(default=20, ge=10, le=30)
    display_interval_seconds: int = Field(default=10, ge=3, le=120)
    display_quiet_start: str = Field(default="22:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    display_quiet_end: str = Field(default="07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    vlm_concurrency: int = Field(default=1, ge=1, le=1)
    embed_concurrency: int = Field(default=1, ge=1, le=1)
    asr_concurrency: int = Field(default=1, ge=1, le=1)
    session_cookie_secure: bool = False
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    whisper_bin: str = "whisper-cli"
    whisper_model: Path = Path("models/ggml-small.bin")
    model_timeout: int = 300
    session_hours: int = 72

    @field_validator("ollama_base_url")
    @classmethod
    def local_only(cls, value):
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Ollama must use a loopback HTTP endpoint")
        return value.rstrip("/")


settings = Settings()


def defaults():
    return {
        "faces_enabled": False,
        "speech_enabled": False,
        "embedding_enabled": True,
        "vlm_enabled": True,
        "jobs_paused": False,
        "daily_photo_limit": settings.daily_photo_limit,
        "display_interval_seconds": settings.display_interval_seconds,
        "quiet_start": settings.display_quiet_start,
        "quiet_end": settings.display_quiet_end,
        "autoplay": True,
        "idle_seconds": 60,
        "default_visibility": "private",
        "quality_floor": 0.12,
        "recent_days": 7,
        "event_gap_hours": 8,
        "travel_gap_hours": 48,
        "face_similarity": 0.45,
        "daily_generation_hour": 4,
        "config_version": 1,
        "scenes": {s: True for s in ["daily", "on_this_day", "travel", "birthday", "festival", "growth", "new_share"]},
        "weights": {
            "event_relevance": 0.25,
            "quality": 0.20,
            "calendar_relevance": 0.20,
            "user_preference": 0.15,
            "freshness": 0.10,
            "underexposure": 0.10,
        },
    }
