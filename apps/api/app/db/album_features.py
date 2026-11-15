"""Per-member album presentation choices, independent of photo sharing."""

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Identity, fk
from app.db.session import Base


class AlbumPin(Identity, Base):
    __tablename__ = "album_pins"
    __table_args__ = (UniqueConstraint("household_id", "user_id", "kind"),)
    household_id: Mapped[str] = fk("households", index=True)
    user_id: Mapped[str] = fk("users", index=True)
    kind: Mapped[str]


class AlbumPhotoChoice(Identity, Base):
    __tablename__ = "album_photo_choices"
    __table_args__ = (UniqueConstraint("household_id", "user_id", "photo_id"),)
    household_id: Mapped[str] = fk("households", index=True)
    user_id: Mapped[str] = fk("users", index=True)
    photo_id: Mapped[str] = fk("photos", index=True)
    preferred: Mapped[bool] = mapped_column(default=True)
