"""A still for every post that has a video: one frame from the clip with the
skeleton drawn on it, so a wall of cards shows dancers rather than stick
figures on grey. Made once at upload; served at /thumb/<id>.jpg.

    python3 -m dsplatform.thumbs backfill      # every post with a video and no still
"""
import io, json, subprocess, sys, tempfile
from PIL import Image, ImageDraw

WIDTH = 480
BONES33 = [[0,2],[0,5],[2,7],[5,8],[9,10],[11,12],[11,13],[13,15],[12,14],[14,16],
           [15,17],[15,19],[15,21],[17,19],[16,18],[16,20],[16,22],[18,20],
           [11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],
           [27,29],[27,31],[29,31],[28,30],[28,32],[30,32]]
BONES17 = [[0,1],[0,2],[1,3],[2,4],[5,6],[5,7],[7,9],[6,8],[8,10],
           [5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]]
# the app's dancer colours: teal for the first, magenta for the second
COLOURS = [(48, 232, 220), (236, 72, 200)]


def frame_at(video: bytes, seconds: float) -> Image.Image | None:
    """One upright frame, via ffmpeg, scaled to WIDTH."""
    with tempfile.NamedTemporaryFile(suffix=".mov") as f:
        f.write(video); f.flush()
        try:
            out = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{seconds:.2f}", "-i", f.name,
                 "-frames:v", "1", "-vf", f"scale={WIDTH}:-2", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                capture_output=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
    if out.returncode != 0 or not out.stdout:
        return None
    return Image.open(io.BytesIO(out.stdout)).convert("RGB")


def duration_of(video: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".mov") as f:
        f.write(video); f.flush()
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                  "-of", "json", f.name], capture_output=True, timeout=30)
            return float(json.loads(out.stdout)["format"]["duration"])
        except Exception:
            return 0.0


def draw_pose(img: Image.Image, dancers: list, alpha: float = 1.0) -> None:
    """Normalised 2D joints (x, y in 0..1, -1 when unseen) onto the image."""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lw = max(2, w // 160)
    for i, pose in enumerate(dancers):
        colour = COLOURS[i % len(COLOURS)] + (int(255 * alpha),)
        bones = BONES33 if len(pose) >= 33 else BONES17
        pts = [(p[0] * w, p[1] * h) if len(p) >= 2 and p[0] >= 0 and p[1] >= 0 else None for p in pose]
        for a, b in bones:
            if a < len(pts) and b < len(pts) and pts[a] and pts[b]:
                d.line([pts[a], pts[b]], fill=colour, width=lw)
        r = lw + 1
        for p in pts:
            if p:
                d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=colour)
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"))


def make_thumb(video: bytes, pose2d: dict | None) -> bytes | None:
    """JPEG bytes, or None when ffmpeg cannot read the clip."""
    dur = duration_of(video)
    at = min(max(dur * 0.3, 0.5), max(dur - 0.2, 0.0)) if dur > 0 else 0.5
    img = frame_at(video, at)
    if img is None:
        return None
    if pose2d and pose2d.get("j"):
        frames = len(pose2d["j"][0])
        t = pose2d.get("t") or []
        if len(t) == frames and frames:
            idx = min(range(frames), key=lambda i: abs(t[i] - at))
        else:
            idx = min(frames - 1, int(at * float(pose2d.get("fps") or 30)))
        draw_pose(img, [dancer[idx] for dancer in pose2d["j"] if idx < len(dancer)])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def backfill():
    """Every post with a video and no still. Fetches the clip from storage."""
    import urllib.request
    from sqlalchemy import select
    from .db import SessionLocal
    from .models import Video
    from .storage import get_storage, LocalStorage
    db = SessionLocal(); st = get_storage()
    todo = db.execute(select(Video).where(Video.video_key != "", Video.thumb_key == "")).scalars().all()
    print(len(todo), "to do", flush=True)
    for v in todo:
        try:
            if isinstance(st, LocalStorage):
                data = st.video_path(v.video_key).read_bytes()
            else:
                data = urllib.request.urlopen(st.video_url(v.video_key), timeout=120).read()
            pose = st.get_pose(v.pose2d_key) if v.pose2d_key else None
            jpg = make_thumb(data, pose)
            if not jpg:
                print(f"  {v.id}: no frame", flush=True); continue
            st.put_thumb(v.video_key, jpg)
            v.thumb_key = v.video_key
            db.commit()
            print(f"  {v.id}: ok", flush=True)
        except Exception as e:
            print(f"  {v.id}: {e}", flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["backfill"]:
        backfill()
    else:
        print(__doc__)
