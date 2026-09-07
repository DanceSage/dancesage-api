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
    # An attempt at someone's video says which one — that is what files it
    # under the lesson on a group's wall.
    reply_to: Mapped[int | None] = mapped_column(ForeignKey("videos.id"), nullable=True,
                                                 default=None, index=True)
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
    # A share is an offer until the other person says yes. NULL here means
    # it is still on the table; they can see who and what, not the clip.
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True,
                                                            default=None)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True,
                                                           default=None)
    # Set when the share went through a group — either the owner sharing to
    # the class, or a member sharing back. That is what files it on the wall.
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True,
                                                 default=None, index=True)
    # Set when the grant came from a series share — the standing offer that
    # made it, so revoking the series finds all of them.
    series_grant_id: Mapped[int | None] = mapped_column(ForeignKey("series_grants.id"),
                                                        nullable=True, default=None, index=True)
    # When someone passes on a video they don't own: where the receiver's
    # attempts go — "sharer" (the person who gave it to them) or "owner" (who
    # made it). Chosen at share time. Irrelevant when the sharer is the owner.
    replies_to: Mapped[str] = mapped_column(String(8), default="sharer")

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    viewer: Mapped[User] = relationship(foreign_keys=[viewer_id])
    video: Mapped["Video | None"] = relationship(foreign_keys=[video_id])
    group: Mapped["Group | None"] = relationship(foreign_keys=[group_id])

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.accepted_at is not None

    @property
    def pending(self) -> bool:
        return self.revoked_at is None and self.accepted_at is None


class Group(Base):
    """People you share with together — a class, a team, a Tuesday crowd.

    A group is the owner's shorthand, nothing more: sharing a video with it
    writes one ordinary grant per member, so everything downstream — the
    inbox, revoking, declining — stays per person and per video. Leaving the
    group later does not take back what was already shared; that is a grant,
    and grants are revoked one by one, on purpose.
    """
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    members: Mapped[list["GroupMember"]] = relationship(back_populates="group",
                                                        cascade="all, delete-orphan")


class GroupMember(Base):
    __tablename__ = "group_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    group: Mapped[Group] = relationship(back_populates="members")
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class Series(Base):
    """A folder in My videos: a class, a course, a term. A video can sit in
    several. Sharing a series is a standing offer — every video in it now,
    and every one added later, one ordinary grant per person each."""
    __tablename__ = "series"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    items: Mapped[list["SeriesVideo"]] = relationship(back_populates="series",
                                                      cascade="all, delete-orphan",
                                                      order_by="SeriesVideo.added_at")
    grants: Mapped[list["SeriesGrant"]] = relationship(back_populates="series",
                                                       cascade="all, delete-orphan")


class SeriesVideo(Base):
    __tablename__ = "series_videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    series: Mapped[Series] = relationship(back_populates="items")
    video: Mapped[Video] = relationship(foreign_keys=[video_id])


class SeriesGrant(Base):
    """One person's standing access to a series: an offer until accepted, then
    the source of a video grant for each video, present and future."""
    __tablename__ = "series_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    viewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True,
                                                 default=None, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    series: Mapped[Series] = relationship(back_populates="grants")
    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    viewer: Mapped[User] = relationship(foreign_keys=[viewer_id])
    group: Mapped["Group | None"] = relationship(foreign_keys=[group_id])

    @property
    def pending(self) -> bool:
        return self.revoked_at is None and self.accepted_at is None

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.accepted_at is not None
