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
from .models import User, Video, Grant
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
        return {"me": db.get(User, uid)}
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
    return templates.TemplateResponse(request, "profile.html",
                                      {"u": u, "videos": vids,
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
    return templates.TemplateResponse(request, "video.html",
                                      {"v": v, "u": v.user, "more": more})


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
    return (v.visibility == "granted" and db is not None
            and _has_grant(db, v.user_id, u.id, v.id))


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

    v = Video(user_id=u.id, title=title, note=note, style=style, level=level,
              pose_key=f"{stem}-3d", pose2d_key=pose2d_key, video_key=video_key,
              dancers=len(p3["j"]), frames=frames, fps=int(fps),
              visibility=visibility)
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
                        "video_key": v.video_key, "fps": int(v.fps or 30)}
                       for v in u.videos]}


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
def my_page(request: Request, u: User | None = Depends(optional_user)):
    """Everything you have posted, whatever its visibility. Only you see this."""
    if not u:
        return RedirectResponse("/signin", status_code=303)
    if not u.handle:
        return templates.TemplateResponse(request, "handle.html", {"u": u})
    vids = sorted(u.videos, key=lambda v: v.created_at, reverse=True)
    return templates.TemplateResponse(request, "me.html", {"u": u, "videos": vids})


# ── browsing ───────────────────────────────────────────────────────────────

def _card(v: Video) -> dict:
    """One video as the app draws it. Credit travels with the clip, always."""
    return {"id": v.id, "title": v.title, "style": v.style, "level": v.level,
            "visibility": v.visibility, "frames": v.frames, "fps": int(v.fps or 30),
            "has_video": v.has_video, "dancers": v.dancers,
            "pose_key": v.pose_key, "pose2d_key": v.pose2d_key,
            "video_key": v.video_key, "note": v.note,
            "by": {"handle": v.user.handle, "display_name": v.user.display_name,
                   "avatar": f"/avatar/{v.user.handle}.jpg" if v.user.avatar_key else ""}}


def _visible_videos(db: Session, me: User | None) -> list[Video]:
    vids = db.execute(select(Video).where(Video.visibility == "public")).scalars().all()
    if me:
        rest = db.execute(select(Video).where(Video.visibility != "public")).scalars().all()
        vids += [v for v in rest if _may_view(v, me, db)]
    return vids


@app.get("/v1/shared")
def shared_with_me(me: User = Depends(current_user), db: Session = Depends(get_db)):
    """What other people have let you see. The inbound half of a grant."""
    grants = db.execute(select(Grant).where(Grant.viewer_id == me.id,
                                            Grant.revoked_at.is_(None))).scalars().all()
    out = []
    for g in grants:
        vids = [v for v in g.owner.videos
                if v.visibility == "granted" and g.video_id == v.id]
        if vids:
            out.append({"handle": g.owner.handle,
                        "display_name": g.owner.display_name,
                        "avatar": f"/avatar/{g.owner.handle}.jpg" if g.owner.avatar_key else "",
                        "videos": [_card(v) for v in
                                   sorted(vids, key=lambda v: v.created_at, reverse=True)]})
    return {"from": out}


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
    return {"grants": [{"id": g.id, "handle": g.viewer.handle,
                        "display_name": g.viewer.display_name,
                        "avatar": f"/avatar/{g.viewer.handle}.jpg" if g.viewer.avatar_key else "",
                        "since": g.created_at.isoformat(),
                        "video_id": g.video_id,
                        "scope": g.video.title if g.video else "(video deleted)"}
                       for g in rows]}


@app.post("/v1/grants")
def add_grant(payload: dict, u: User = Depends(current_user),
              db: Session = Depends(get_db)):
    """Let one person see your shared videos. Idempotent — granting twice is fine."""
    handle = (payload.get("handle") or "").strip().lstrip("@").lower()
    if not handle:
        raise HTTPException(400, "handle required")
    viewer = db.execute(select(User).where(User.handle == handle)).scalar_one_or_none()
    if not viewer:
        raise HTTPException(404, f"Nobody here is called @{handle}")
    if viewer.id == u.id:
        raise HTTPException(400, "You can already see your own videos")

    video_id = payload.get("video_id")
    if video_id is None:
        raise HTTPException(400, "video_id required — access is granted per video")
    v = db.get(Video, int(video_id))
    if not v or v.user_id != u.id:
        raise HTTPException(404, "No such video")
    # Sharing a clip is what makes it shared; asking twice would be a trap.
    if v.visibility == "private":
        v.visibility = "granted"

    existing = db.execute(select(Grant).where(
        Grant.owner_id == u.id, Grant.viewer_id == viewer.id,
        Grant.video_id == int(video_id))).scalars().first()
    if existing:
        # Re-granting someone you revoked reuses the row rather than piling up history.
        existing.revoked_at = None
        g = existing
    else:
        g = Grant(owner_id=u.id, viewer_id=viewer.id, video_id=int(video_id))
        db.add(g)
    db.commit(); db.refresh(g)
    return {"id": g.id, "handle": viewer.handle, "display_name": viewer.display_name,
            "video_id": g.video_id}


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
        raise HTTPException(400, "visibility must be private, granted or public")
    v.visibility = want
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
