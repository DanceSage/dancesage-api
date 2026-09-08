"""Refine: the bodies made after the fact, and the worker that makes them.

Tiers, matching the product:
  refined   both dancers as bodies, from a GPU worker, minutes and cents
  3d        the full tracked body, turnable, the paid stage

The platform never runs a model. It keeps a queue (body_tracks), hands jobs to a
worker that asks for them, stores what the worker sends back, and starts a GPU
machine on RunPod when the queue is non-empty and nothing is running. The worker
lives in dancesage-research/refine-worker.
"""
import datetime as dt
import json
import os
import urllib.request

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import current_user, optional_user
from .db import get_db
from .models import BodyTrack, User, Video
from .storage import LocalStorage, get_storage

router = APIRouter()

TIERS = ("refined", "3d")
# What each tier needs from the account. "refined" is free while it costs cents;
# 3d is the paid stage. Flip here, not in the routes.
TIER_PLAN = {"refined": "free", "3d": "pro"}
WORKER_TOKEN = os.environ.get("REFINE_WORKER_TOKEN", "")
RUNPOD_KEY = os.environ.get("RUNPOD_API_KEY", "")
RUNPOD_GPU = os.environ.get("RUNPOD_GPU", "NVIDIA A40")
RUNPOD_VOLUME = os.environ.get("RUNPOD_VOLUME_ID", "")         # keeps the 24 GB of weights between pods
RUNPOD_IMAGE = os.environ.get("RUNPOD_IMAGE", "pytorch/pytorch:2.7.1-cuda11.8-cudnn9-devel")
PLATFORM_BASE = os.environ.get("PLATFORM_BASE", "https://dancesage-api.fly.dev")
# The worker bootstraps itself on a bare PyTorch pod from this script: no image to build.
BOOTSTRAP = (os.path.join(os.path.dirname(__file__), "refine_bootstrap.sh"))


# ── what the app and the web see ────────────────────────────────────────────

def body_summary(v: Video, db: Session) -> dict:
    """Per tier: the latest track's status, so a card can say Refining… or 3D."""
    rows = db.execute(select(BodyTrack).where(BodyTrack.video_id == v.id)
                      .order_by(BodyTrack.created_at.desc())).scalars().all()
    out = {}
    for t in rows:
        if t.tier in out:
            continue
        out[t.tier] = {"id": t.id, "status": t.status, "fps": t.fps, "dancers": t.dancers,
                       "has_mesh": bool(t.has_mesh), "has_turntable": bool(t.has_turntable)}
    return out


def _may_view(v, u, db):
    from .main import _may_view as mv   # the video's own rule, shared with everything else
    return mv(v, u, db)


@router.post("/v1/videos/{video_id}/refine")
def request_refine(video_id: int, payload: dict | None = None,
                   u: User = Depends(current_user), db: Session = Depends(get_db)):
    """Ask for a body. Queued at once; a worker picks it up; the card shows Refining…"""
    v = db.get(Video, video_id)
    if not v or v.user_id != u.id:
        raise HTTPException(404, "Not your video")
    if not v.video_key:
        raise HTTPException(400, "This post has no video to refine — the phone's skeleton is all there is")
    tier = (payload or {}).get("tier") or "refined"
    if tier not in TIERS:
        raise HTTPException(400, f"tier must be one of {TIERS}")
    if TIER_PLAN[tier] == "pro" and u.plan != "pro":
        raise HTTPException(402, "The 3D body is part of the paid plan")
    live = db.execute(select(BodyTrack).where(BodyTrack.video_id == v.id, BodyTrack.tier == tier,
                                              BodyTrack.status.in_(("queued", "running")))).scalars().first()
    if live:
        return {"id": live.id, "status": live.status, "tier": tier}
    t = BodyTrack(video_id=v.id, tier=tier)
    db.add(t); db.commit(); db.refresh(t)
    _wake_worker(db)
    return {"id": t.id, "status": t.status, "tier": tier}


@router.get("/v1/videos/{video_id}/body")
def get_body(video_id: int, tier: str = "", u: User | None = Depends(optional_user),
             db: Session = Depends(get_db)):
    """The best body there is for this video: which tier, and where its files are."""
    v = db.get(Video, video_id)
    if not _may_view(v, u, db):
        raise HTTPException(404, "No such video")
    q = select(BodyTrack).where(BodyTrack.video_id == v.id, BodyTrack.status == "done")
    if tier:
        q = q.where(BodyTrack.tier == tier)
    rows = db.execute(q.order_by(BodyTrack.created_at.desc())).scalars().all()
    if not rows:
        return {"summary": body_summary(v, db), "track": None}
    # 3d beats refined when both exist
    t = sorted(rows, key=lambda r: (r.tier == "3d", r.created_at), reverse=True)[0]
    files = {"joints": f"/body/{t.id}/joints.json", "meta": f"/body/{t.id}/meta.json"}
    if t.has_mesh:
        files["mesh"] = f"/body/{t.id}/mesh.bin"
    if t.has_turntable:
        files["turntable"] = f"/body/{t.id}/turntable.mp4"
    import time
    from .main import PLAYBACK_TTL
    token = _view_token(t.id, int(time.time()) + PLAYBACK_TTL)
    return {"summary": body_summary(v, db),
            "track": {"id": t.id, "tier": t.tier, "engine": t.engine, "fps": t.fps,
                      "dancers": t.dancers, "frames": t.frames, "files": files,
                      "view_url": f"/body/{t.id}/view?t={token}" if t.has_mesh else None}}


def _view_token(track_id: int, expires: int) -> str:
    from .main import _playback_token
    return _playback_token(f"body{track_id}", expires)


def _view_ok(track_id: int, t: str) -> bool:
    """A short-lived signed token, so the app's web view can open the page without a cookie."""
    from .main import _playback_ok
    return bool(t) and _playback_ok(f"body{track_id}", t)


def _files(t: BodyTrack, token: str = "") -> dict:
    q = f"?t={token}" if token else ""
    files = {"joints": f"/body/{t.id}/joints.json{q}", "meta": f"/body/{t.id}/meta.json{q}"}
    if t.has_mesh:
        files["mesh"] = f"/body/{t.id}/mesh.bin{q}"
    if t.has_turntable:
        files["turntable"] = f"/body/{t.id}/turntable.mp4{q}"
    return files


@router.get("/body/{track_id}/view")
def body_view(track_id: int, request: Request, t: str = "", u: User | None = Depends(optional_user),
              db: Session = Depends(get_db)):
    """The viewer alone, full screen: for the app's web view and for a share."""
    from .main import templates
    tr = db.get(BodyTrack, track_id)
    if not tr or tr.status != "done" or not tr.has_mesh:
        raise HTTPException(404, "No 3D body here")
    if not (_view_ok(track_id, t) or _may_view(tr.video, u, db)):
        raise HTTPException(404, "No 3D body here")
    return templates.TemplateResponse(request, "body3d.html", {"v": tr.video, "track": tr, "files": _files(tr, t)})


@router.get("/body/{track_id}/{name}")
def body_file(track_id: int, name: str, t: str = "", u: User | None = Depends(optional_user),
              db: Session = Depends(get_db)):
    """A body file, under the video's own access rule; from R2 by redirect in the cloud."""
    tr = db.get(BodyTrack, track_id)
    if not tr or tr.status != "done" or name not in ("joints.json", "mesh.bin", "meta.json", "turntable.mp4"):
        raise HTTPException(404, "No such file")
    if not (_view_ok(track_id, t) or _may_view(tr.video, u, db)):
        raise HTTPException(404, "No such file")
    t = tr
    st = get_storage()
    if isinstance(st, LocalStorage):
        try:
            data = st.body_bytes(t.id, name)
        except FileNotFoundError:
            raise HTTPException(404, "No such file")
        media = {"json": "application/json", "bin": "application/octet-stream", "mp4": "video/mp4"}[name.rsplit(".", 1)[1]]
        return Response(data, media_type=media, headers={"Cache-Control": "private, no-store"})
    return RedirectResponse(st.body_url(t.id, name), status_code=307,
                            headers={"Cache-Control": "private, no-store"})


# ── the worker's side ───────────────────────────────────────────────────────

def _worker(x_worker_token: str = Header(default="")) -> str:
    token = os.environ.get("REFINE_WORKER_TOKEN", "")     # read late: tests and deploys set it after import
    if not token or x_worker_token != token:
        raise HTTPException(401, "Not a worker")
    return x_worker_token


@router.get("/v1/refine/next")
def next_job(worker: str = "", _: str = Depends(_worker), db: Session = Depends(get_db)):
    """Claim the oldest queued job. Empty answer when there is none."""
    from .main import _playback_token, PLAYBACK_TTL
    import time
    t = db.execute(select(BodyTrack).where(BodyTrack.status == "queued")
                   .order_by(BodyTrack.created_at)).scalars().first()
    if not t:
        return {"job": None, "queued": 0}
    t.status = "running"; t.started_at = dt.datetime.utcnow(); t.worker = worker[:80]
    db.commit(); db.refresh(t)
    v = t.video
    st = get_storage()
    if isinstance(st, LocalStorage):
        expires = int(time.time()) + PLAYBACK_TTL
        video_url = f"{PLATFORM_BASE}/video/{v.video_key}.mov?t={_playback_token(v.video_key, expires)}"
    else:
        video_url = st.video_url(v.video_key)
    return {"job": {"id": t.id, "video_id": v.id, "tier": t.tier, "dancers": v.dancers,
                    "video_url": video_url, "title": v.title},
            "queued": db.execute(select(BodyTrack).where(BodyTrack.status == "queued")).scalars().all().__len__()}


@router.post("/v1/refine/{track_id}/result")
async def post_result(track_id: int, engine: str = Form(...), fps: float = Form(...),
                      dancers: int = Form(...), frames: int = Form(...), seconds: float = Form(0),
                      joints: UploadFile = File(...), meta: UploadFile = File(...),
                      mesh: UploadFile | None = File(None), turntable: UploadFile | None = File(None),
                      _: str = Depends(_worker), db: Session = Depends(get_db)):
    t = db.get(BodyTrack, track_id)
    if not t or t.status != "running":
        raise HTTPException(404, "No such running job")
    st = get_storage()
    st.put_body(t.id, "joints.json", await joints.read(), "application/json")
    st.put_body(t.id, "meta.json", await meta.read(), "application/json")
    if mesh is not None:
        st.put_body(t.id, "mesh.bin", await mesh.read(), "application/octet-stream"); t.has_mesh = 1
    if turntable is not None:
        st.put_body(t.id, "turntable.mp4", await turntable.read(), "video/mp4"); t.has_turntable = 1
    t.engine, t.fps, t.dancers, t.frames, t.seconds = engine[:40], fps, dancers, frames, seconds
    t.status = "done"; t.finished_at = dt.datetime.utcnow()
    db.commit()
    return {"ok": True, "id": t.id}


@router.post("/v1/refine/{track_id}/fail")
def post_fail(track_id: int, payload: dict, _: str = Depends(_worker), db: Session = Depends(get_db)):
    t = db.get(BodyTrack, track_id)
    if not t or t.status != "running":
        raise HTTPException(404, "No such running job")
    t.status = "failed"; t.error = str(payload.get("error", ""))[:2000]; t.finished_at = dt.datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.get("/v1/refine/queue")
def queue_state(_: str = Depends(_worker), db: Session = Depends(get_db)):
    """How much is waiting — the worker uses it to decide when to shut itself down."""
    n = len(db.execute(select(BodyTrack).where(BodyTrack.status == "queued")).scalars().all())
    r = len(db.execute(select(BodyTrack).where(BodyTrack.status == "running")).scalars().all())
    return {"queued": n, "running": r}


# ── starting the GPU when there is work ────────────────────────────────────

def _runpod(query: str) -> dict:
    req = urllib.request.Request("https://api.runpod.io/graphql", data=json.dumps({"query": query}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {RUNPOD_KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _wake_worker(db: Session) -> None:
    """A GPU pod running the worker, if none is running. It bootstraps itself from
    refine_bootstrap.sh and stops itself when the queue has been empty for a
    while, so idle time costs nothing."""
    if not (RUNPOD_KEY and os.environ.get("REFINE_WORKER_TOKEN")):
        return
    try:
        pods = _runpod('{ myself { pods { id name desiredStatus } } }')["data"]["myself"]["pods"]
        if any(p["name"] == "dancesage-refine-worker" and p["desiredStatus"] == "RUNNING" for p in pods):
            return
        import base64
        script = base64.b64encode(open(BOOTSTRAP, "rb").read()).decode()
        env = {"PLATFORM_BASE": PLATFORM_BASE, "REFINE_WORKER_TOKEN": os.environ.get("REFINE_WORKER_TOKEN", ""),
               "HF_TOKEN": os.environ.get("HF_TOKEN", ""), "RUNPOD_API_KEY": RUNPOD_KEY,
               "GIT_TOKEN": os.environ.get("GIT_TOKEN", ""), "WORKER_REF": os.environ.get("WORKER_REF", "refine"),
               "IDLE_MINUTES": os.environ.get("REFINE_IDLE_MINUTES", "10"), "BOOTSTRAP_B64": script}
        env_gql = ", ".join(f'{{key: {json.dumps(k)}, value: {json.dumps(v)}}}' for k, v in env.items())
        args = json.dumps('bash -c "echo $BOOTSTRAP_B64 | base64 -d > /bootstrap.sh && bash /bootstrap.sh"')
        def deploy(extra):
            return _runpod('mutation { podFindAndDeployOnDemand(input: {gpuCount: 1, '
                           f'gpuTypeId: "{RUNPOD_GPU}", name: "dancesage-refine-worker", imageName: "{RUNPOD_IMAGE}", '
                           f'containerDiskInGb: 60, volumeInGb: 0, minVcpuCount: 8, minMemoryInGb: 32, dockerArgs: {args}, '
                           f'env: [{env_gql}]{extra}}}) {{ id }} }}')
        # With the weights volume when its data centre has a card; otherwise anywhere,
        # and the pod fetches the weights itself (ten minutes more).
        r = deploy(f', networkVolumeId: "{RUNPOD_VOLUME}"') if RUNPOD_VOLUME else {"errors": True}
        if not (r.get("data") or {}).get("podFindAndDeployOnDemand"):
            r = deploy(", cloudType: SECURE")
        ok = (r.get("data") or {}).get("podFindAndDeployOnDemand")
        print(f"refine: worker pod {'started ' + ok['id'] if ok else 'NOT started: ' + str(r)[:300]}", flush=True)
    except Exception as e:      # a queued job waits; the next request tries again
        print(f"refine: could not start worker: {e}", flush=True)
