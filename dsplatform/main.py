"""Dance Sage platform — profiles and the city directory.

Phone records and uploads. The web only reads. No browser upload path exists by
design, and no video enters without a pose track: the skeleton is the platform rule.
"""
import asyncio, gzip, json, os, random, pathlib
import jwt, time
import datetime as dt
from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import (HTMLResponse, JSONResponse, FileResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from .db import get_db, Base, engine, SessionLocal
from .models import User, Video, Grant, Group, GroupMember, Series, SeriesVideo, SeriesGrant
from .storage import get_storage, LocalStorage
from .auth import (verify_provider_token, issue_session, current_user,
                    optional_user, COOKIE, SECRET)

HERE = pathlib.Path(__file__).parent
app = FastAPI(title="Dance Sage")

BACKUP_HOURS = float(os.environ.get("BACKUP_EVERY_HOURS", "12"))


@app.on_event("startup")
def _migrate():
    """Schema changes create_all cannot make.

    create_all only creates tables that are missing; it never alters one that
    exists. A model that says nullable over a table that says NOT NULL looks
    correct in the code and fails at the first insert.
    """
    from .migrate_handle_null import migrate
    url = os.environ.get("DATABASE_URL", "sqlite:///./dancesage.db")
    try:
        migrate(url.replace("sqlite:////", "/").replace("sqlite:///", ""))
    except Exception as e:
        print(f"migration skipped: {e}", flush=True)
    _migrate_grant_offers()


def _migrate_grant_offers():
    """Shares became offers. A grants table from before has no accepted_at;
    add it, and mark what was already shared as accepted — those people had
    it, and an upgrade must not take it away. Only a missing column triggers
    the backfill, so a restart never accepts anyone's pending offers."""
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(grants)"))]
            if cols and "accepted_at" not in cols:
                conn.execute(text("ALTER TABLE grants ADD COLUMN accepted_at DATETIME"))
                conn.execute(text("UPDATE grants SET accepted_at = created_at"))
                print("grants: added accepted_at, existing shares accepted", flush=True)
            if cols and "group_id" not in cols:
                conn.execute(text("ALTER TABLE grants ADD COLUMN group_id INTEGER"))
                print("grants: added group_id", flush=True)
            if cols and "series_grant_id" not in cols:
                conn.execute(text("ALTER TABLE grants ADD COLUMN series_grant_id INTEGER"))
                print("grants: added series_grant_id", flush=True)
            vcols = [row[1] for row in conn.execute(text("PRAGMA table_info(videos)"))]
            if vcols and "reply_to" not in vcols:
                conn.execute(text("ALTER TABLE videos ADD COLUMN reply_to INTEGER"))
                print("videos: added reply_to", flush=True)
    except Exception as e:
        print(f"grant offers migration skipped: {e}", flush=True)


@app.on_event("startup")
async def _schedule_backups():
    """Back the database up to R2, on a loop, for as long as the server runs.

    The database is the only thing here that cannot be rebuilt: R2 holds the pose
    tracks and the video, but nothing there records who owns them. Lose the volume
    without this and you keep a bucket of anonymous files.

    In the app rather than a separate scheduled machine because there is one
    machine, and a backup that depends on a second thing running is a backup with
    two ways to silently stop.
    """
    if os.environ.get("STORAGE_BACKEND", "local").lower() != "r2":
        return                      # nowhere durable to put them

    async def loop():
        while True:
            try:
                await asyncio.to_thread(_take_backup)
            except Exception as e:
                # A failed backup must never take the server down with it.
                print(f"backup failed: {e}", flush=True)
            await asyncio.sleep(BACKUP_HOURS * 3600)

    asyncio.create_task(loop())


def _take_backup():
    from .backup import take
    take()
@app.middleware("http")
async def _short_cache_for_static(request: Request, call_next):
    """Keep /static fresh.

    These files are edited in place under the same names, so a long cache means
    a deploy lands and nobody sees it — the renderer stayed stale for hours
    after being fixed. Content that changes name when it changes can be cached
    hard; this cannot.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
    return response


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
def _nav_user(request: Request) -> dict:
    """Puts `me` in front of every template so the shared header can decide
    between "Sign in" and "Your videos".

    Done here rather than threading a user through nine route signatures — the
    nav is chrome, and chrome should not change what each page has to accept.
    """
    tok = request.cookies.get(COOKIE, "")
    if not tok:
        return {"me": None}
    try:
        uid = jwt.decode(tok, SECRET, algorithms=["HS256"]).get("uid")
    except jwt.PyJWTError:
        return {"me": None}
    db = SessionLocal()
    try:
        me = db.get(User, uid)
        offers = 0
        if me:
            offers = len(db.execute(select(Grant).where(
                Grant.viewer_id == me.id, Grant.revoked_at.is_(None),
                Grant.accepted_at.is_(None))).scalars().all())
        return {"me": me, "offer_count": offers}
    finally:
        db.close()


templates = Jinja2Templates(directory=str(HERE / "templates"),
                            context_processors=[_nav_user])
Base.metadata.create_all(engine)


@app.get("/health")
def health():
    """Cheap and dependency-free — it must not touch R2 or the database, or a
    slow bucket would look like a dead server and get the machine restarted."""
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    cities = db.execute(
        select(User.city).where(User.takes_students == 1).distinct()
    ).scalars().all()
    return templates.TemplateResponse(request, "home.html",
                                      {"cities": [c for c in cities if c]})


@app.get("/city/{city}", response_class=HTMLResponse)
def city(city: str, request: Request, style: str = "", level: str = "",
         db: Session = Depends(get_db)):
    q = select(User).where(User.takes_students == 1, User.city.ilike(city))
    teachers = list(db.execute(q).scalars().all())
    if style:
        teachers = [t for t in teachers if style.lower() in t.styles.lower()]
    if level:
        teachers = [t for t in teachers if level.lower() in t.levels.lower()]
    # Rotate rather than rank. Sorting by popularity is the function that makes
    # good unknown teachers invisible, which is the reason this exists.
    random.shuffle(teachers)
    cards = []
    for t in teachers:
        vids = sorted(t.videos, key=lambda v: v.created_at, reverse=True)
        if vids:
            cards.append({"teacher": t, "video": vids[0], "count": len(vids)})
    styles = sorted({s for t in teachers for s in t.style_list})
    return templates.TemplateResponse(request, "city.html", {
        "city": city.title(), "cards": cards,
        "styles": styles, "style": style, "level": level,
    })


@app.get("/@{handle}", response_class=HTMLResponse)
def profile(handle: str, request: Request, me: User | None = Depends(optional_user),
            db: Session = Depends(get_db)):
    """One page, three versions of itself depending on who is reading it."""
    u = db.execute(select(User).where(User.handle == handle)).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "No such profile")
    vids = sorted([v for v in u.videos if _may_view(v, me, db)],
                  key=lambda v: v.created_at, reverse=True)
    shared = bool(me) and me.id != u.id and _has_grant(db, u.id, me.id)
    # Public videos other dancers shared with this person appear here too —
    # with the sharer's name on them. Private ones never do; those are the
    # owner's, and only the two of them can see them.
    passed_on = []
    for g in db.execute(select(Grant).where(Grant.viewer_id == u.id, Grant.revoked_at.is_(None),
                                            Grant.accepted_at.is_not(None))).scalars().all():
        v = g.video
        if v is None or v.visibility != "public" or v.user_id == u.id:
            continue
        passed_on.append({"video": v, "by": g.owner})
    passed_on.sort(key=lambda x: x["video"].created_at, reverse=True)
    return templates.TemplateResponse(request, "profile.html",
                                      {"u": u, "videos": vids, "passed_on": passed_on,
                                       "is_owner": bool(me) and me.id == u.id,
                                       "has_access": shared})


@app.get("/v/{video_id}", response_class=HTMLResponse)
def video(video_id: int, request: Request, me: User | None = Depends(optional_user),
          db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not _may_view(v, me, db):
        raise HTTPException(404, "No such video")
    more = [x for x in v.user.videos
            if x.id != v.id and _may_view(x, me, db)][:6]
    # Your own: the full menu. Someone else's public one: share it on. A private
    # one shared with you: nothing — it is theirs to share, not yours.
    can_share = bool(me) and (v.user_id == me.id or v.visibility == "public")
    return templates.TemplateResponse(request, "video.html",
                                      {"v": v, "u": v.user, "more": more,
                                       "can_share": can_share})


@app.get("/pose/{key:path}.json")
def pose(key: str, request: Request, u: User | None = Depends(optional_user),
         db: Session = Depends(get_db)):
    """In the cloud this becomes a short-lived signed URL checked against grants.

    Served still gzipped — every client decompresses transparently, so the tenfold
    size win applies on the wire and not just on disk.
    """
    if not _may_view(_owner_of(db, pose=key), u, db):
        raise HTTPException(404, "No pose track")
    try:
        blob, gzipped = get_storage().pose_blob(key)
    except FileNotFoundError:
        raise HTTPException(404, "No pose track")
    headers = {"Cache-Control": "private, max-age=3600"}
    if gzipped:
        # Every real client accepts gzip, but announcing an encoding the caller
        # did not ask for hands them bytes they cannot read.
        if "gzip" in request.headers.get("accept-encoding", "").lower():
            headers["Content-Encoding"] = "gzip"
        else:
            blob = gzip.decompress(blob)
    return Response(blob, media_type="application/json", headers=headers)


# ── who may see what ───────────────────────────────────────────────────────

def _owner_of(db: Session, *, pose: str = "", video: str = "") -> Video | None:
    """The post a stored object belongs to. Unknown objects have no owner."""
    if pose:
        return db.execute(select(Video).where(
            (Video.pose_key == pose) | (Video.pose2d_key == pose))).scalars().first()
    return db.execute(select(Video).where(Video.video_key == video)).scalars().first()


def _has_grant(db: Session, owner_id: int, viewer_id: int,
               video_id: int | None = None) -> bool:
    """An active grant for this exact video. Revoked grants are not grants.

    Access is always per clip. There is no way to hand someone everything at
    once — sharing one video is a decision about that video, and a viewer sees
    it and nothing else.
    """
    q = select(Grant).where(Grant.owner_id == owner_id,
                            Grant.viewer_id == viewer_id,
                            Grant.revoked_at.is_(None),
                            Grant.accepted_at.is_not(None),
                            Grant.video_id == video_id)
    return db.execute(q).scalars().first() is not None


def _may_view(v: Video | None, u: User | None, db: Session | None = None) -> bool:
    """Public is public, private is the owner's alone, shared needs a grant.

    An unknown key is refused rather than served. Guessing a filename must not be
    a way in, which is the whole reason this function exists.
    """
    if v is None:
        return False
    if v.visibility == "public":
        return True
    if u is None:
        return False
    if v.user_id == u.id:
        return True
    # Private for everyone else — except the people it was shared with.
    return db is not None and _has_grant(db, v.user_id, u.id, v.id)


PLAYBACK_TTL = 3600


def _playback_token(key: str, expires: int) -> str:
    return jwt.encode({"k": key, "exp": expires}, SECRET, algorithm="HS256")


def _playback_ok(key: str, token: str) -> bool:
    """A short-lived ticket for one object. AVPlayer cannot send an Authorization
    header, so playback is authorised by a signed URL instead — the same shape the
    cloud uses, so nothing changes when storage moves."""
    if not token:
        return False
    try:
        return jwt.decode(token, SECRET, algorithms=["HS256"]).get("k") == key
    except jwt.PyJWTError:
        return False


@app.get("/avatar/{handle}.jpg")
def avatar(handle: str, db: Session = Depends(get_db)):
    """A profile photo, or 404 so the page falls back to initials."""
    u = db.execute(select(User).where(User.handle == handle)).scalar_one_or_none()
    if not u or not u.avatar_key:
        raise HTTPException(404, "No avatar")
    try:
        data = get_storage().avatar_bytes(u.avatar_key)
    except FileNotFoundError:
        raise HTTPException(404, "No avatar")
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/video/{key}.mov")
def video_file(key: str, t: str = "", u: User | None = Depends(optional_user),
               db: Session = Depends(get_db)):
    """Served from disk locally; in the cloud the client is sent straight to R2.

    Redirecting rather than proxying is the whole point of R2 — the bytes go from
    Cloudflare to the viewer without passing through here, which is what keeps
    egress free and this process idle during playback.
    """
    if not _playback_ok(key, t) and not _may_view(_owner_of(db, video=key), u, db):
        raise HTTPException(404, "No video")
    st = get_storage()
    if isinstance(st, LocalStorage):
        p = st.video_path(key)
        if not p.exists():
            raise HTTPException(404, "No video")
        return FileResponse(p, media_type="video/quicktime")
    return RedirectResponse(st.video_url(key), status_code=307)


@app.post("/v1/videos")
async def upload(
    title: str = Form(...),
    note: str = Form(""),
    style: str = Form("Bachata"),
    level: str = Form("All levels"),
    fps: float = Form(30.0),
    pose3d: str = Form(...),          # JSON: {"j": [[[x,y,z]…]…]}
    pose2d: str = Form(""),           # JSON: {"j": [[[x,y]…]…]} — overlays the video
    times: str = Form(""),            # JSON: seconds per frame, as actually captured
    visibility: str = Form("private"),  # private by default; going public is a choice
    reply_to: int | None = Form(None),  # the video this is an attempt at, if any
    video: UploadFile | None = File(None),
    u: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The app posts here. No pose track, no video — that is the platform rule,
    enforced by pose3d being required rather than by a policy document."""
    try:
        p3 = json.loads(pose3d)
    except json.JSONDecodeError:
        raise HTTPException(400, "pose3d is not valid JSON")
    if not p3.get("j"):
        raise HTTPException(400, "pose3d has no joints")
    if visibility not in ("private", "granted", "public"):
        raise HTTPException(400, "visibility must be private, granted or public")

    st = get_storage()
    n = len(db.execute(select(Video).where(Video.user_id == u.id)).scalars().all()) + 1
    stem = f"{u.handle}/upload-{n}"
    frames = len(p3["j"][0])
    # When the pose frames were actually captured. Detection is throttled and
    # irregular, so assuming an even spacing makes the skeleton drift away from
    # the video it is drawn on — by seconds, over a long clip.
    stamps = []
    if times:
        try:
            stamps = [float(t) for t in json.loads(times)]
        except (json.JSONDecodeError, TypeError, ValueError):
            stamps = []
    if len(stamps) != frames:
        stamps = []

    st.put_pose(f"{stem}-3d", {"fps": fps, "frames": frames, "dancers": len(p3["j"]),
                               "height": p3.get("height", 1.6),
                               "centre": p3.get("centre", [0, 0, 0]),
                               "t": stamps, "j": p3["j"]})
    pose2d_key = ""
    if pose2d:
        p2 = json.loads(pose2d)
        st.put_pose(f"{stem}-2d", {"fps": fps, "frames": len(p2["j"][0]), "j": p2["j"],
                                   "t": stamps, "vis": p2.get("vis", [])})
        pose2d_key = f"{stem}-2d"
    video_key = ""
    if video is not None:
        st.put_video(stem.replace("/", "-"), await video.read())
        video_key = stem.replace("/", "-")

    # Only a video the poster can see may be answered; otherwise the id is noise.
    if reply_to is not None and not _may_view(db.get(Video, reply_to), u, db):
        reply_to = None
    v = Video(user_id=u.id, title=title, note=note, style=style, level=level,
              pose_key=f"{stem}-3d", pose2d_key=pose2d_key, video_key=video_key,
              dancers=len(p3["j"]), frames=frames, fps=int(fps),
              visibility=visibility, reply_to=reply_to)
    db.add(v); db.commit(); db.refresh(v)
    return {"id": v.id, "url": f"/v/{v.id}", "profile": f"/@{u.handle}",
            "visibility": v.visibility}


# ── auth ───────────────────────────────────────────────────────────────────

@app.post("/v1/auth/signin")
def sign_in(payload: dict, db: Session = Depends(get_db)):
    """Exchange Apple's identity token for a session. Called once per device."""
    token = payload.get("idToken") or payload.get("identityToken") or ""
    if not token:
        raise HTTPException(400, "idToken required")
    claims = verify_provider_token(token)
    sub = claims["sub"]
    u = db.execute(select(User).where(User.auth_uid == sub)).scalar_one_or_none()
    created = False
    if not u:
        # Apple only sends the name on the very first sign-in, so take it if offered.
        u = User(auth_uid=sub, email=claims.get("email", "") or "", handle=None,
                 display_name=payload.get("displayName") or claims.get("name") or "Dancer")
        db.add(u); db.commit(); db.refresh(u)
        created = True
    tok = issue_session(u)
    resp = JSONResponse({"token": tok, "created": created,
                         "needs_handle": not u.handle,
                         "me": {"handle": u.handle, "display_name": u.display_name}})
    # the app uses the Bearer token; a browser gets a cookie so server-rendered
    # pages know who is asking without any JavaScript
    resp.set_cookie(COOKIE, tok, httponly=True, secure=True, samesite="lax",
                    max_age=180 * 86400, path="/")
    # A readable companion carrying no credential. The pages on Pages are static
    # files with their nav baked in at build time, so they cannot know you are
    # signed in — this lets a few lines of script on them say "Your videos"
    # instead of inviting you to sign in again.
    # Carries the handle, not a credential — enough for a static page to show your
    # face in the nav and link to your profile, and useless to anyone who steals it.
    resp.set_cookie("ds_in", u.handle or "1", httponly=False, secure=True,
                    samesite="lax", max_age=180 * 86400, path="/")
    resp.delete_cookie("ds_out", path="/")
    return resp


@app.get("/v1/me")
def me(u: User = Depends(current_user)):
    return {"handle": u.handle, "display_name": u.display_name, "bio": u.bio,
            "city": u.city, "styles": u.styles, "levels": u.levels,
            "takes_students": bool(u.takes_students),
            "avatar": f"/avatar/{u.handle}.jpg" if u.avatar_key else "",
            "videos": [{"id": v.id, "title": v.title, "note": v.note,
                        "style": v.style, "level": v.level,
                        "visibility": v.visibility,
                        "frames": v.frames, "has_video": v.has_video,
                        "pose_key": v.pose_key, "pose2d_key": v.pose2d_key,
                        "video_key": v.video_key, "fps": int(v.fps or 30),
                        "created_at": v.created_at.isoformat()}
                       # Newest first — the same order the web page shows.
                       for v in sorted(u.videos, key=lambda v: v.created_at, reverse=True)]}


@app.patch("/v1/me")
def update_me(payload: dict, u: User = Depends(current_user),
              db: Session = Depends(get_db)):
    if "handle" in payload:
        h = payload["handle"].strip().lower()
        if not h.isalnum() or not 3 <= len(h) <= 30:
            raise HTTPException(400, "Handle must be 3-30 letters or numbers")
        if h in RESERVED:
            # Handles live at the site root, so one of these would shadow a page.
            raise HTTPException(400, "That handle is reserved")
        taken = db.execute(select(User).where(User.handle == h,
                                              User.id != u.id)).scalar_one_or_none()
        if taken:
            raise HTTPException(409, "That handle is taken")
        u.handle = h
    for field in ("display_name", "bio", "city", "styles", "levels"):
        if field in payload:
            setattr(u, field, payload[field])
    if "takes_students" in payload:
        u.takes_students = 1 if payload["takes_students"] else 0
    db.commit()
    resp = JSONResponse({"ok": True, "handle": u.handle})
    if u.handle:
        # The nav reads this; a handle chosen after signing in must reach it.
        resp.set_cookie("ds_in", u.handle, httponly=False, secure=True,
                        samesite="lax", max_age=180 * 86400, path="/")
    return resp


# ── the web session ────────────────────────────────────────────────────────

@app.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request, u: User | None = Depends(optional_user)):
    # Already signed in? The form would only be confusing — the nav is showing
    # your face while the page asks who you are. Go where you were headed.
    if u:
        return RedirectResponse("/me", status_code=303)
    return templates.TemplateResponse(request, "signin.html", {})


@app.get("/signout")
def signout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    resp.delete_cookie("ds_in", path="/")
    return resp


@app.get("/me", response_class=HTMLResponse)
def my_page(request: Request, u: User | None = Depends(optional_user),
            db: Session = Depends(get_db)):
    """Everything you have posted, whatever its visibility. Only you see this."""
    if not u:
        return RedirectResponse("/signin", status_code=303)
    if not u.handle:
        return templates.TemplateResponse(request, "handle.html", {"u": u})
    vids = sorted(u.videos, key=lambda v: v.created_at, reverse=True)
    shared_ids = {g.video_id for g in db.execute(select(Grant).where(
        Grant.owner_id == u.id, Grant.revoked_at.is_(None))).scalars().all()}
    groups = list_groups(u, db)
    series = [_series_card(s, for_owner=True) for s in
              db.execute(select(Series).where(Series.owner_id == u.id).order_by(Series.created_at)).scalars().all()]
    in_series = {v["id"] for s in series for v in s["videos"]}
    return templates.TemplateResponse(request, "me.html",
                                      {"u": u, "videos": vids, "shared_ids": shared_ids,
                                       "series": series, "in_series": in_series,
                                       "shared": _shared_with(u, db),
                                       "offers": _shared_with(u, db, pending=True),
                                       "series_offers": _series_offers(u, db, pending=True),
                                       "series_in": _series_offers(u, db, pending=False),
                                       "own_groups": groups["groups"], "member_of": groups["member_of"]})


# ── browsing ───────────────────────────────────────────────────────────────

def _card(v: Video) -> dict:
    """One video as the app draws it. Credit travels with the clip, always."""
    return {"id": v.id, "title": v.title, "style": v.style, "level": v.level,
            "visibility": v.visibility, "frames": v.frames, "fps": int(v.fps or 30),
            "has_video": v.has_video, "dancers": v.dancers,
            "pose_key": v.pose_key, "pose2d_key": v.pose2d_key,
            "video_key": v.video_key, "note": v.note, "reply_to": v.reply_to,
            "by": {"handle": v.user.handle, "display_name": v.user.display_name,
                   "avatar": f"/avatar/{v.user.handle}.jpg" if v.user.avatar_key else ""}}


def _visible_videos(db: Session, me: User | None) -> list[Video]:
    vids = db.execute(select(Video).where(Video.visibility == "public")).scalars().all()
    if me:
        rest = db.execute(select(Video).where(Video.visibility != "public")).scalars().all()
        vids += [v for v in rest if _may_view(v, me, db)]
    return vids


def _shared_with(me: User, db: Session, *, pending: bool = False) -> list[dict]:
    """What other people have let you see, grouped by who — or, with
    `pending`, what they have offered and you have not answered. The same
    lists the app's inbox and the web's page draw from."""
    grants = db.execute(select(Grant).where(Grant.viewer_id == me.id,
                                            Grant.revoked_at.is_(None))).scalars().all()
    by_owner: dict[int, dict] = {}
    for g in grants:
        if g.pending != pending:
            continue
        v = g.video
        if v is None:
            continue
        # A share of someone else's public video is fine while it is public.
        if v.user_id != g.owner_id and v.visibility != "public":
            continue
        entry = by_owner.setdefault(g.owner_id, {
            "handle": g.owner.handle,
            "display_name": g.owner.display_name,
            "avatar": f"/avatar/{g.owner.handle}.jpg" if g.owner.avatar_key else "",
            "videos": []})
        # The grant id rides with the card: accepting, declining and stopping
        # all name it.
        sg = db.get(SeriesGrant, g.series_grant_id) if g.series_grant_id else None
        entry["videos"].append(dict(_card(v), grant_id=g.id,
                                    group=({"id": g.group_id, "name": g.group.name}
                                           if g.group_id and g.group else None),
                                    series=({"id": sg.series_id, "name": sg.series.name}
                                            if sg else None)))
    out = list(by_owner.values())
    for entry in out:
        entry["videos"].sort(key=lambda c: c["id"], reverse=True)
    return out


def _series_card(s: Series, *, for_owner: bool = False) -> dict:
    card = {"id": s.id, "name": s.name, "video_count": len(s.items),
            "videos": [_card(i.video) for i in s.items],
            "owner": {"handle": s.owner.handle, "display_name": s.owner.display_name}}
    if for_owner:
        card["shared_with"] = [{"grant_id": g.id, "handle": g.viewer.handle,
                                "display_name": g.viewer.display_name,
                                "accepted": g.accepted_at is not None,
                                "group": g.group.name if g.group else None}
                               for g in s.grants if g.revoked_at is None]
    return card


def _series_offers(me: User, db: Session, *, pending: bool) -> list[dict]:
    rows = db.execute(select(SeriesGrant).where(SeriesGrant.viewer_id == me.id,
                                                SeriesGrant.revoked_at.is_(None))).scalars().all()
    return [dict(_series_card(g.series), series_grant_id=g.id,
                 group=({"id": g.group_id, "name": g.group.name} if g.group else None))
            for g in rows if g.pending == pending]


@app.get("/v1/shared")
def shared_with_me(me: User = Depends(current_user), db: Session = Depends(get_db)):
    """`from` is what you accepted; `offers` is waiting on you — and the same
    two for series, which are accepted once."""
    return {"from": _shared_with(me, db), "offers": _shared_with(me, db, pending=True),
            "series": _series_offers(me, db, pending=False),
            "series_offers": _series_offers(me, db, pending=True)}


def _materialise(db: Session, sg: SeriesGrant) -> None:
    """An accepted series grant becomes one accepted video grant per video in
    the series — the live part: called again whenever a video is added."""
    for item in sg.series.items:
        v = item.video
        if v.user_id != sg.owner_id:
            continue
        g = _grant(db, sg.owner, sg.viewer, v, sg.group, accepted=True)
        g.series_grant_id = sg.id


@app.post("/v1/shared/series/{series_grant_id}/accept")
def accept_series(series_grant_id: int, u: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    sg = db.get(SeriesGrant, series_grant_id)
    if not sg or sg.viewer_id != u.id or sg.revoked_at is not None:
        raise HTTPException(404, "No such offer")
    if sg.accepted_at is None:
        sg.accepted_at = dt.datetime.utcnow()
        _materialise(db, sg)
        db.commit()
    return {"ok": True, "accepted": series_grant_id}


def _end_series_grant(sg: SeriesGrant, db: Session) -> None:
    sg.revoked_at = dt.datetime.utcnow()
    for g in db.execute(select(Grant).where(Grant.series_grant_id == sg.id,
                                            Grant.revoked_at.is_(None))).scalars().all():
        g.revoked_at = sg.revoked_at


@app.delete("/v1/shared/series/{series_grant_id}")
def decline_series(series_grant_id: int, u: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    """Decline the offer, or stop the series — every video it brought goes too."""
    sg = db.get(SeriesGrant, series_grant_id)
    if not sg or sg.viewer_id != u.id:
        raise HTTPException(404, "No such share")
    _end_series_grant(sg, db)
    db.commit()
    return {"ok": True}


@app.post("/v1/shared/{grant_id}/accept")
def accept_share(grant_id: int, u: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    g = db.get(Grant, grant_id)
    if not g or g.viewer_id != u.id or g.revoked_at is not None:
        raise HTTPException(404, "No such offer")
    if g.accepted_at is None:
        g.accepted_at = dt.datetime.utcnow()
        db.commit()
    return {"ok": True, "accepted": grant_id}


# ── deletion ───────────────────────────────────────────────────────────────

def _erase_video(st, db: Session, v: Video) -> None:
    """Remove a post and everything it points at.

    Storage first, then the row. The other order can strand objects nothing
    refers to any more, and an orphan in a bucket is a file nobody will ever
    find to delete.
    """
    for key in (v.pose_key, v.pose2d_key):
        if key:
            try:
                st.delete(pose=key)
            except Exception:
                pass          # already gone, or a bad key; the row still goes
    if v.video_key:
        try:
            st.delete(video=v.video_key)
        except Exception:
            pass
    db.execute(delete(Grant).where(Grant.video_id == v.id))
    db.delete(v)


@app.delete("/v1/videos/{video_id}")
def delete_video(video_id: int, u: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Delete one post. Promised in the privacy policy, so it has to be real."""
    v = db.get(Video, video_id)
    if not v or v.user_id != u.id:
        raise HTTPException(404, "No such video")
    _erase_video(get_storage(), db, v)
    db.commit()
    return {"ok": True, "deleted": video_id}


@app.delete("/v1/me")
def delete_account(u: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete the account and everything in it.

    Apple requires this of any app that creates accounts, and the privacy policy
    promises it. It removes the profile, every post, the media behind them, and
    every grant in both directions.

    What it cannot remove is the Firebase identity — the server never holds a
    credential for it. The app deletes that itself, with a token it gets by asking
    for the password again, so the two halves go together.
    """
    st = get_storage()
    n = len(u.videos)
    for v in list(u.videos):
        _erase_video(st, db, v)
    if u.avatar_key:
        try:
            st.delete(avatar=u.avatar_key)
        except Exception:
            pass
    # Grants both ways: what they gave out, and what was given to them.
    db.execute(delete(Grant).where((Grant.owner_id == u.id) | (Grant.viewer_id == u.id)))
    # Series they own, and series shared to them.
    for s in db.execute(select(Series).where(Series.owner_id == u.id)).scalars().all():
        db.delete(s)
    db.execute(delete(SeriesGrant).where(SeriesGrant.viewer_id == u.id))
    # Groups they own go; their seat in other people's groups goes too.
    for g in db.execute(select(Group).where(Group.owner_id == u.id)).scalars().all():
        db.delete(g)
    db.execute(delete(GroupMember).where(GroupMember.user_id == u.id))
    handle = u.handle
    db.delete(u)
    db.commit()
    return {"ok": True, "handle": handle, "videos_deleted": n}


# ── who you have let in ────────────────────────────────────────────────────

@app.get("/v1/grants")
def list_grants(u: User = Depends(current_user), db: Session = Depends(get_db)):
    """Everyone who can see your shared videos."""
    rows = db.execute(select(Grant).where(Grant.owner_id == u.id,
                                          Grant.revoked_at.is_(None))).scalars().all()
    series_rows = db.execute(select(SeriesGrant).where(SeriesGrant.owner_id == u.id,
                                                      SeriesGrant.revoked_at.is_(None))).scalars().all()
    return {"grants": [{"id": g.id, "handle": g.viewer.handle,
                        "display_name": g.viewer.display_name,
                        "avatar": f"/avatar/{g.viewer.handle}.jpg" if g.viewer.avatar_key else "",
                        "since": g.created_at.isoformat(),
                        "video_id": g.video_id,
                        "accepted": g.accepted_at is not None,
                        "series": g.series_grant_id is not None,
                        "scope": g.video.title if g.video else "(video deleted)"}
                       for g in rows],
            "series_grants": [{"id": sg.id, "handle": sg.viewer.handle,
                               "display_name": sg.viewer.display_name,
                               "series_id": sg.series_id, "series": sg.series.name,
                               "accepted": sg.accepted_at is not None,
                               "group": sg.group.name if sg.group else None}
                              for sg in series_rows]}


def _grant(db: Session, owner: User, viewer: User, v: Video,
           group: Group | None = None, accepted: bool = False) -> Grant:
    """One person, one video. Re-granting someone you revoked reuses the row
    rather than piling up history. `accepted` skips the offer — a reply to a
    class is an answer, not a question."""
    existing = db.execute(select(Grant).where(
        Grant.owner_id == owner.id, Grant.viewer_id == viewer.id,
        Grant.video_id == v.id)).scalars().first()
    g = existing
    if g:
        if g.revoked_at is not None:
            g.revoked_at = None
            g.accepted_at = None      # ended once; ask again
    else:
        g = Grant(owner_id=owner.id, viewer_id=viewer.id, video_id=v.id)
        db.add(g)
    if group is not None:
        g.group_id = group.id
    if accepted and g.accepted_at is None:
        g.accepted_at = dt.datetime.utcnow()
    return g


@app.post("/v1/grants")
def add_grant(payload: dict, u: User = Depends(current_user),
              db: Session = Depends(get_db)):
    """Let one person — or everyone in one of your groups — see a video.
    Idempotent: granting twice is fine."""
    video_id = payload.get("video_id")
    series_id = payload.get("series_id")
    if video_id is None and series_id is None:
        raise HTTPException(400, "video_id or series_id required — access is granted per video or per series")
    v = None
    series = None
    if series_id is not None:
        series = db.get(Series, int(series_id))
        if not series or series.owner_id != u.id:
            raise HTTPException(404, "No such series")
    else:
        v = db.get(Video, int(video_id))
        if not v or (v.user_id != u.id and v.visibility != "public"):
            raise HTTPException(404, "No such video")

    viewers: list[User] = []
    group: Group | None = None
    handle = (payload.get("handle") or "").strip().lstrip("@").lower()
    group_id = payload.get("group_id")
    if handle:
        viewer = db.execute(select(User).where(User.handle == handle)).scalar_one_or_none()
        if not viewer:
            raise HTTPException(404, f"Nobody here is called @{handle}")
        if viewer.id == u.id:
            raise HTTPException(400, "You can already see your own videos")
        viewers = [viewer]
    elif group_id is not None:
        group = db.get(Group, int(group_id))
        if not group or group.owner_id != u.id:
            raise HTTPException(404, "No such group")
        viewers = [m.user for m in group.members if m.user_id != u.id]
        if not viewers:
            raise HTTPException(400, f"“{group.name}” has no members yet")
    else:
        raise HTTPException(400, "handle or group_id required")

    if v is not None and v.reply_to is not None:
        answered = db.get(Video, v.reply_to)
        if answered and any(viewer.id != answered.user_id for viewer in viewers):
            raise HTTPException(400, "An attempt at someone's video can only go back to them. "
                                     "Record your own video to teach it.")

    if series is not None:
        # A standing offer per person. Re-sharing after an end asks again.
        made = []
        for viewer in viewers:
            sg = db.execute(select(SeriesGrant).where(SeriesGrant.series_id == series.id,
                                                       SeriesGrant.viewer_id == viewer.id)).scalars().first()
            if sg:
                if sg.revoked_at is not None:
                    sg.revoked_at = None
                    sg.accepted_at = None
            else:
                sg = SeriesGrant(series_id=series.id, owner_id=u.id, viewer_id=viewer.id)
                db.add(sg)
            if group is not None:
                sg.group_id = group.id
            made.append(sg)
        db.commit()
        return {"series_id": series.id,
                "granted": [{"id": sg.id, "handle": sg.viewer.handle,
                             "display_name": sg.viewer.display_name} for sg in made]}

    grants = [_grant(db, u, viewer, v, group) for viewer in viewers]
    db.commit()
    for g in grants:
        db.refresh(g)
    first = grants[0]
    return {"id": first.id, "handle": first.viewer.handle,
            "display_name": first.viewer.display_name, "video_id": v.id,
            "granted": [{"id": g.id, "handle": g.viewer.handle,
                         "display_name": g.viewer.display_name} for g in grants]}


@app.delete("/v1/shared/{grant_id}")
def decline_share(grant_id: int, u: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """Turn down an offer, or stop a share you accepted. The same timestamp
    the owner's revoke sets — the grant is over either way, and the owner
    sees it gone."""
    g = db.get(Grant, grant_id)
    if not g or g.viewer_id != u.id:
        raise HTTPException(404, "No such share")
    g.revoked_at = dt.datetime.utcnow()
    db.commit()
    return {"ok": True, "declined": grant_id}


# ── series: folders in My videos, shared as a standing offer ───────────────

def _own_series(db: Session, series_id: int, u: User) -> Series:
    s = db.get(Series, series_id)
    if not s or s.owner_id != u.id:
        raise HTTPException(404, "No such series")
    return s


@app.get("/v1/series")
def list_series(u: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Series).where(Series.owner_id == u.id)
                      .order_by(Series.created_at)).scalars().all()
    return {"series": [_series_card(s, for_owner=True) for s in rows]}


@app.post("/v1/series")
def create_series(payload: dict, u: User = Depends(current_user), db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "Give the series a name")
    s = Series(owner_id=u.id, name=name)
    db.add(s)
    for vid in payload.get("video_ids") or []:
        v = db.get(Video, int(vid))
        if v and v.user_id == u.id:
            s.items.append(SeriesVideo(video_id=v.id))
    db.commit(); db.refresh(s)
    return _series_card(s, for_owner=True)


@app.post("/v1/series/{series_id}/videos")
def add_to_series(series_id: int, payload: dict, u: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """Put a video in the series — and, the live part, in front of everyone
    who already accepted the series."""
    s = _own_series(db, series_id, u)
    v = db.get(Video, int(payload.get("video_id") or 0))
    if not v or v.user_id != u.id:
        raise HTTPException(404, "No such video")
    if v.reply_to is not None:
        raise HTTPException(400, "An attempt at someone's video is not a lesson. Record your own.")
    if not any(i.video_id == v.id for i in s.items):
        s.items.append(SeriesVideo(video_id=v.id))
        db.flush()
        for sg in s.grants:
            if sg.active:
                _materialise(db, sg)
    db.commit(); db.refresh(s)
    return _series_card(s, for_owner=True)


@app.delete("/v1/series/{series_id}/videos/{video_id}")
def remove_from_series(series_id: int, video_id: int, u: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    """Takes the video out of the folder. Grants it already made stay — they
    are ordinary shares now, revocable one by one."""
    s = _own_series(db, series_id, u)
    s.items = [i for i in s.items if i.video_id != video_id]
    db.commit(); db.refresh(s)
    return _series_card(s, for_owner=True)


@app.delete("/v1/series/{series_id}")
def delete_series(series_id: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """Ends every share the series made, then removes the folder. The videos stay."""
    s = _own_series(db, series_id, u)
    for sg in s.grants:
        if sg.revoked_at is None:
            _end_series_grant(sg, db)
    db.delete(s)
    db.commit()
    return {"ok": True, "deleted": series_id}


@app.delete("/v1/series/grants/{series_grant_id}")
def revoke_series_grant(series_grant_id: int, u: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    """The owner takes a series back from one person — every video it brought."""
    sg = db.get(SeriesGrant, series_grant_id)
    if not sg or sg.owner_id != u.id:
        raise HTTPException(404, "No such share")
    _end_series_grant(sg, db)
    db.commit()
    return {"ok": True}


# ── groups: people you share with together ─────────────────────────────────

def _group_card(g: Group) -> dict:
    return {"id": g.id, "name": g.name,
            "members": [{"handle": m.user.handle, "display_name": m.user.display_name,
                         "avatar": f"/avatar/{m.user.handle}.jpg" if m.user.avatar_key else ""}
                        for m in g.members]}


def _own_group(db: Session, group_id: int, u: User) -> Group:
    g = db.get(Group, group_id)
    if not g or g.owner_id != u.id:
        raise HTTPException(404, "No such group")
    return g


def _add_member(db: Session, g: Group, handle: str, u: User) -> None:
    handle = handle.strip().lstrip("@").lower()
    if not handle:
        return
    member = db.execute(select(User).where(User.handle == handle)).scalar_one_or_none()
    if not member:
        raise HTTPException(404, f"Nobody here is called @{handle}")
    if member.id == u.id:
        raise HTTPException(400, "You are the group's owner — no need to add yourself")
    if any(m.user_id == member.id for m in g.members):
        return
    g.members.append(GroupMember(user_id=member.id))


@app.get("/v1/groups")
def list_groups(u: User = Depends(current_user), db: Session = Depends(get_db)):
    """`groups` you own; `member_of` the ones someone put you in."""
    rows = db.execute(select(Group).where(Group.owner_id == u.id)
                      .order_by(Group.created_at)).scalars().all()
    mine = db.execute(select(GroupMember).where(GroupMember.user_id == u.id)).scalars().all()
    return {"groups": [_group_card(g) for g in rows],
            "member_of": [dict(_group_card(m.group),
                               owner={"handle": m.group.owner.handle,
                                      "display_name": m.group.owner.display_name})
                          for m in mine]}


def _group_of(db: Session, group_id: int, u: User) -> tuple[Group, bool]:
    """The group and whether you own it. Members get in; strangers get a 404."""
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(404, "No such group")
    if g.owner_id == u.id:
        return g, True
    if any(m.user_id == u.id for m in g.members):
        return g, False
    raise HTTPException(404, "No such group")


@app.get("/v1/groups/{group_id}/wall")
def group_wall(group_id: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """What went through this group. `lessons`: the owner's videos shared to
    it. `replies`: what members shared back — the owner sees everyone's,
    a member sees only their own."""
    g, owner = _group_of(db, group_id, u)
    grants = db.execute(select(Grant).where(Grant.group_id == g.id,
                                            Grant.revoked_at.is_(None))).scalars().all()
    lessons: dict[int, dict] = {}
    replies: list[dict] = []
    for gr in grants:
        v = gr.video
        if v is None:
            continue
        if gr.owner_id == g.owner_id:
            card = lessons.setdefault(v.id, dict(_card(v), members=[], replies=[]))
            card["members"].append({"handle": gr.viewer.handle, "accepted": gr.accepted_at is not None,
                                    "grant_id": gr.id if gr.viewer_id == u.id else None})
        elif owner or gr.owner_id == u.id:
            replies.append(dict(_card(v), grant_id=gr.id if owner else None))
    # A reply that names its lesson sits under it; the rest stay loose.
    loose = []
    for r in sorted(replies, key=lambda c: c["id"], reverse=True):
        if r["reply_to"] in lessons:
            lessons[r["reply_to"]]["replies"].append(r)
        else:
            loose.append(r)
    # Series first: a video that came through a series shows under it.
    by_series: dict[int | None, dict] = {}
    for card in lessons.values():
        grant = next((gr for gr in grants if gr.video_id == card["id"] and gr.owner_id == g.owner_id
                      and gr.series_grant_id), None)
        sg = db.get(SeriesGrant, grant.series_grant_id) if grant else None
        key = sg.series_id if sg else None
        entry = by_series.setdefault(key, {"id": key, "name": sg.series.name if sg else None, "videos": []})
        entry["videos"].append(card)
    series_out = sorted([e for e in by_series.values() if e["id"] is not None], key=lambda e: e["name"])
    loose_videos = by_series.get(None, {"videos": []})["videos"]
    return {"group": dict(_group_card(g), owner={"handle": g.owner.handle,
                                                 "display_name": g.owner.display_name},
                          mine=owner),
            "series": [dict(e, videos=sorted(e["videos"], key=lambda c: c["id"])) for e in series_out],
            "lessons": sorted(loose_videos, key=lambda c: c["id"], reverse=True),
            "replies": loose}


@app.get("/g/{group_id}", response_class=HTMLResponse)
def group_page(group_id: int, request: Request, me: User | None = Depends(optional_user),
               db: Session = Depends(get_db)):
    """The group's wall, for its owner and its members."""
    if not me:
        return RedirectResponse("/signin", status_code=303)
    wall = group_wall(group_id, me, db)
    own_videos = sorted(me.videos, key=lambda v: v.created_at, reverse=True) if not wall["group"]["mine"] else []
    return templates.TemplateResponse(request, "group.html",
                                      {"w": wall, "own": own_videos})


@app.post("/v1/groups/{group_id}/share")
def share_to_group(group_id: int, payload: dict, u: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    """A member shares one of their own videos back to the group. It goes to
    the owner, already accepted — a reply is an answer, not a question — and
    is filed under the group."""
    g, owner = _group_of(db, group_id, u)
    if owner:
        raise HTTPException(400, "Share to your own group with Group share")
    v = db.get(Video, int(payload.get("video_id") or 0))
    if not v or v.user_id != u.id:
        raise HTTPException(404, "No such video")
    gr = _grant(db, u, g.owner, v, g, accepted=True)
    db.commit(); db.refresh(gr)
    return {"id": gr.id, "group": g.name, "to": g.owner.handle}


@app.post("/v1/groups")
def create_group(payload: dict, u: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """A named group, optionally with its first members."""
    name = (payload.get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400, "Give the group a name")
    g = Group(owner_id=u.id, name=name)
    db.add(g)
    for h in payload.get("handles") or []:
        _add_member(db, g, str(h), u)
    db.commit(); db.refresh(g)
    return _group_card(g)


@app.post("/v1/groups/{group_id}/members")
def add_group_member(group_id: int, payload: dict, u: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    g = _own_group(db, group_id, u)
    _add_member(db, g, payload.get("handle") or "", u)
    db.commit(); db.refresh(g)
    return _group_card(g)


@app.delete("/v1/groups/{group_id}/members/{handle}")
def remove_group_member(group_id: int, handle: str, u: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    """Leaves existing grants alone: what was shared stays shared until revoked."""
    g = _own_group(db, group_id, u)
    handle = handle.strip().lstrip("@").lower()
    g.members = [m for m in g.members if m.user.handle != handle]
    db.commit(); db.refresh(g)
    return _group_card(g)


@app.delete("/v1/groups/{group_id}")
def delete_group(group_id: int, u: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    g = _own_group(db, group_id, u)
    db.delete(g)
    db.commit()
    return {"ok": True, "deleted": group_id}


@app.delete("/v1/grants/{grant_id}")
def revoke_grant(grant_id: int, u: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Take it back. The next request from that person is refused."""
    g = db.get(Grant, grant_id)
    if not g or g.owner_id != u.id:
        raise HTTPException(404, "No such grant")
    g.revoked_at = dt.datetime.utcnow()
    db.commit()
    return {"ok": True, "revoked": g.viewer.handle}


@app.get("/v1/videos/{video_id}/playback")
def playback(video_id: int, u: User | None = Depends(optional_user),
             db: Session = Depends(get_db)):
    """A short-lived URL for one video, checked against who is asking.

    Handing out an expiring link rather than a permanent path is what makes access
    revocable: turn a post private and the next link is refused, while the one
    already issued dies on its own.
    """
    v = db.get(Video, video_id)
    if not _may_view(v, u, db) or not v.video_key:
        raise HTTPException(404, "No video")
    st = get_storage()
    if isinstance(st, LocalStorage):
        expires = int(time.time()) + PLAYBACK_TTL
        url = f"/video/{v.video_key}.mov?t={_playback_token(v.video_key, expires)}"
    else:
        url = st.video_url(v.video_key)
    return {"url": url, "expires_in": PLAYBACK_TTL}


@app.post("/v1/videos/{video_id}/visibility")
def set_visibility(video_id: int, payload: dict,
                   u: User = Depends(current_user), db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v or v.user_id != u.id:
        raise HTTPException(404, "Not your video")
    want = payload.get("visibility")
    if want not in ("private", "granted", "public"):
        raise HTTPException(400, "visibility must be private or public")
    v.visibility = "public" if want == "public" else "private"
    db.commit()
    return {"ok": True, "visibility": v.visibility}


RESERVED = {"signin", "signout", "me", "city", "v", "pose", "video", "static",
            "api", "v1", "admin", "about", "help", "support", "settings", "new",
            "search", "explore", "login", "logout", "signup", "terms", "privacy"}


@app.get("/v1/handles/{handle}/available")
def handle_available(handle: str, db: Session = Depends(get_db)):
    h = handle.strip().lower()
    if len(h) < 3:
        return {"ok": False, "why": "At least 3 characters"}
    if len(h) > 30:
        return {"ok": False, "why": "At most 30 characters"}
    if not h.isalnum():
        return {"ok": False, "why": "Letters and numbers only"}
    if h in RESERVED:
        return {"ok": False, "why": "That one is reserved"}
    taken = db.execute(select(User).where(User.handle == h)).scalar_one_or_none()
    return {"ok": not taken, "why": "Already taken" if taken else "Available"}
