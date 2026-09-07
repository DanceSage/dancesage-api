"""The page scripts must at least run.

A template can render perfectly and still ship a script that dies on its
first line — which is invisible to every other test here and took the
thumbnails down with it once. So: render each page with one video on it,
pull the inline scripts out, and execute them in node against a stub DOM.
"""
import pathlib
import re
import shutil
import subprocess

import jinja2
import pytest

HERE = pathlib.Path(__file__).parent
TEMPLATES = HERE.parent / "dsplatform" / "templates"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


class _User:
    id = 1
    handle = "abdu"
    display_name = "Abdu"
    city = ""
    avatar_key = ""


class _Video:
    def __init__(self, i):
        self.id = i
        self.title = "T"
        self.seconds = 3
        self.has_video = False
        self.visibility = "private"
        self.pose_key = "k"
        self.pose2d_key = ""
        self.video_key = ""
        self.frames = 30
        self.fps = 15
        self.dancers = 1
        self.style = "Bachata"
        self.level = "All levels"
        self.note = ""


def _scripts(template: str, **ctx) -> list[str]:
    # Autoescape on, as FastAPI's Jinja2Templates renders .html — an inline
    # script with a quoted value is exactly where the two differ.
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    html = env.get_template(template).render(**ctx)
    return re.findall(r"<script>(.*?)</script>", html, re.S)


def _run(scripts: list[str], tmp_path: pathlib.Path) -> None:
    files = []
    for i, js in enumerate(scripts):
        f = tmp_path / f"script_{i}.js"
        f.write_text(js)
        files.append(str(f))
    r = subprocess.run(["node", str(HERE / "js" / "dom_shim.js"), *files],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "without throwing" in r.stdout, r.stderr or r.stdout


def test_profile_page_scripts_run(tmp_path):
    me = _User()
    _run(_scripts("me.html", u=me, me=me, videos=[_Video(1)], shared_ids=set(),
                  shared=[], offers=[], offer_count=0, own_groups=[], member_of=[],
                  series=[{"id": 1, "name": "S", "video_count": 1, "videos": [_Video(1)], "shared_with": []}],
                  in_series={1}, series_offers=[], series_in=[]), tmp_path)


def test_group_page_scripts_run(tmp_path):
    me = _User()
    w = {"group": {"id": 1, "name": "G", "mine": False, "members": [],
                   "owner": {"handle": "t", "display_name": "T"}},
         "lessons": [], "replies": [], "series": []}
    _run(_scripts("group.html", w=w, own=[_Video(1)], me=me, offer_count=0), tmp_path)


def test_video_page_scripts_run_for_the_owner(tmp_path):
    me = _User()
    v = _Video(1)
    _run(_scripts("video.html", v=v, u=me, me=me, more=[], offer_count=0, can_share=True), tmp_path)


def test_video_page_scripts_run_for_a_visitor_sharing_a_public_clip(tmp_path):
    me = _User(); owner = _User(); owner.id = 2
    v = _Video(1); v.visibility = "public"
    _run(_scripts("video.html", v=v, u=owner, me=me, more=[], offer_count=0, can_share=True), tmp_path)


def test_lessons_page_scripts_run(tmp_path):
    me = _User()
    lessons = [{"id": 1, "name": "Basic", "lesson": {"id": 1, "title": "Basic"}, "teacher": {"handle": "t", "display_name": "T"},
                "group": None, "series": {"id": 1, "name": "Friday"},
                "attempts": [{"id": 2, "title": "try", "created_at": "2026-09-07T00:00:00", "sent": False, "has_video": True}]}]
    classes = [{"lesson": {"id": 3, "title": "Copa"}, "series": None, "groups": ["g"],
                "students": [{"handle": "s", "display_name": "S", "accepted": True}],
                "attempts": [{"id": 4, "title": "try", "by": {"display_name": "S"}, "sent_at": "2026-09-07T00:00:00", "has_video": False}]}]
    _run(_scripts("lessons.html", lessons=lessons, classes=classes, me=me, offer_count=0), tmp_path)


def test_attempt_page_runs_the_replay(tmp_path):
    """replay.js itself, driven the way the attempt page drives it."""
    me = _User(); owner = _User(); owner.id = 2
    v = _Video(1); v.reply_to = 2; v.pose2d_key = "k"; v.video_key = "vk"; v.has_video = True; v.mirrored = True
    lesson = _Video(2); lesson.has_video = True; lesson.video_key = "lk"; lesson.user = owner
    scripts = _scripts("video.html", v=v, u=me, me=me, more=[], offer_count=0, can_share=False, lesson=lesson)
    replay = (TEMPLATES.parent / "static" / "replay.js").read_text()
    frames = 4
    joints = [[[0.3 + 0.01 * j, 0.2 + 0.02 * j] for j in range(33)] for _ in range(frames)]
    track = {"fps": 15, "frames": frames, "t": [i / 15 for i in range(frames)], "ta": [i / 15 for i in range(frames)],
             "j": [joints, joints]}
    drive = f"""
      startReplay({{ root: 'replay', track: {__import__('json').dumps(track)}, mirrored: true,
                     teacherVideo: '/video/lk.mov', youVideo: '/video/vk.mov' }});
    """
    _run([replay, drive] + scripts, tmp_path)
