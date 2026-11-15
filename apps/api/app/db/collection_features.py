"""Local text recognition and user-owned smart collections."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Identity, fk, now
from app.db.session import Base


class PhotoText(Identity, Base):
    __tablename__ = "photo_texts"
    photo_id: Mapped[str] = fk("photos", unique=True)
    text: Mapped[str] = mapped_column(default="")
    regions_json: Mapped[list] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(default="")
    corrected_text: Mapped[str | None]
    confirmed: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[str] = mapped_column(default=now)


class SmartCollection(Identity, Base):
    __tablename__ = "smart_collections"
    household_id: Mapped[str] = fk("households", index=True)
    created_by: Mapped[str] = fk("users", index=True)
    title: Mapped[str] = mapped_column(String(300))
    mode: Mapped[str] = mapped_column(default="dynamic")
    query_json: Mapped[dict] = mapped_column(JSON, default=dict)
    photo_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    include_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    exclude_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    cover_photo_id: Mapped[str | None] = mapped_column(ForeignKey("photos.id", ondelete="SET NULL"))
    pinned: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)
