"""Users, their videos, and who they let in."""
import datetime as dt
from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable, and NULL rather than "" before one is chosen. The column is unique,
    # and SQL lets many rows be NULL but only one be the empty string — so a second
    # person signing up would collide with the first on a handle neither has yet.
    handle: Mapped[str | None] = mapped_column(String(40), unique=True, index=True,
                                               nullable=True, default=None)
    display_name: Mapped[str] = mapped_column(String(80))
    bio: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(String(60), index=True, default="")
    styles: Mapped[str] = mapped_column(String(160), default="")
    levels: Mapped[str] = mapped_column(String(120), default="")
    takes_students: Mapped[int] = mapped_column(Integer, default=0)   # opt in to the directory
    auth_uid: Mapped[str | None] = mapped_column(String(128), unique=True, index=True,
                                                 nullable=True, default=None)
    email: Mapped[str] = mapped_column(String(160), default="")
    # Empty means no photo — the page falls back to initials rather than a stock face.
    avatar_key: Mapped[str] = mapped_column(String(200), default="")        # often a private relay
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    videos: Mapped[list["Video"]] = relationship(back_populates="user",
                                                 cascade="all, delete-orphan")

    @property
    def style_list(self):
        return [s.strip() for s in self.styles.split(",") if s.strip()]


class Video(Base):
    __tablename__ = "videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default="")
    style: Mapped[str] = mapped_column(String(40), default="")
    level: Mapped[str] = mapped_column(String(40), default="")
    visibility: Mapped[str] = mapped_column(String(12), default="private")
    pose_key: Mapped[str] = mapped_column(String(200))            # 3D track, skeleton view
    pose2d_key: Mapped[str] = mapped_column(String(200), default="")  # 2D track, overlays video
    video_key: Mapped[str] = mapped_column(String(200), default="")   # empty = skeleton only
    dancers: Mapped[int] = mapped_column(Integer, default=1)
    frames: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=30.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    user: Mapped[User] = relationship(back_populates="videos")

    @property
    def has_video(self):
        return bool(self.video_key)

    @property
    def seconds(self):
        return round(self.frames / self.fps, 1) if self.fps else 0


class Grant(Base):
    """One person letting another see their shared videos.

    A grant is a record, not a file — which is the whole reason this table exists.
    Revoking is setting a timestamp, and the next request is refused; there is no
    copy on anyone's device to chase. That is what makes access something you can
    take back rather than something you gave away.

    The grant is person to person, not per video: mark a video *shared* and everyone
    you have granted can see it. One decision to manage instead of one per clip.
    """
    __tablename__ = "grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    viewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # NULL means every shared video this person has; set means only that one.
    # Both matter: a teacher with one class wants the first, a teacher running
    # a beginner and an advanced group wants the second.
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"),
                                                 nullable=True, default=None,
                                                 index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True,
                                                           default=None)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    viewer: Mapped[User] = relationship(foreign_keys=[viewer_id])
    video: Mapped["Video | None"] = relationship(foreign_keys=[video_id])

    @property
    def active(self) -> bool:
        return self.revoked_at is None
