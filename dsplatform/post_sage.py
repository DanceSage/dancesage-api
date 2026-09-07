"""Give Sage, the AI teacher, its profile and its classes.

Sage is an ordinary account: it owns videos, files them in series, and shares
like any teacher. This posts every clip that build_sage_classes.py produced
(dancesage-research/rnd/sage_out) through the same /v1/videos the app uses,
then files them in one series per dance.

Two steps, because the account is made where the database is:

  1. On the server, create the account and mint a session:
       fly ssh console -a dancesage-api -C "python3 -m dsplatform.post_sage account"
  2. Here, with that token, post the classes:
       SAGE_TOKEN=... .venv/bin/python -m dsplatform.post_sage post [--base https://dancesage.com]

Re-running "post" skips titles Sage already has, so it is safe to repeat.
"""
import json, os, pathlib, sys
import urllib.request, urllib.error

HANDLE = "sage"
PROFILE = dict(
    handle=HANDLE, display_name="Sage", city="Everywhere",
    styles="Salsa, Bachata", levels="Beginner, Intermediate, Advanced",
    takes_students=1, auth_uid="sage-ai-teacher",
    bio="The Dance Sage teacher. Every class from the first basic to the last turn, "
        "salsa and bachata, one move at a time. Add a class to your lessons, dance it, "
        "send me your attempt.",
)
# A first, small set: enough to see how a teacher's course reads. The builder
# made 38; the rest post the day the curriculum wants them.
FIRST = {("Salsa", 101), ("Salsa", 102), ("Salsa", 106), ("Salsa", 109), ("Salsa", 111), ("Salsa", 116),
         ("Bachata", 101), ("Bachata", 102), ("Bachata", 104), ("Bachata", 107),
         ("Salsa", 123), ("Salsa", 124), ("Bachata", 113)}   # styling, solo
MANIFEST = pathlib.Path("/Users/abduradi/Documents/dancesage/dancesage-research/rnd/sage_out/manifest.json")


def account():
    """Server side: the account, and a session token to post with."""
    from sqlalchemy import select
    from .db import SessionLocal
    from .models import User
    from .auth import issue_session
    db = SessionLocal()
    u = db.execute(select(User).where(User.handle == HANDLE)).scalar_one_or_none()
    if not u:
        u = User(**PROFILE)
        db.add(u); db.commit(); db.refresh(u)
        print(f"created @{HANDLE} as user {u.id}")
    else:
        print(f"@{HANDLE} exists as user {u.id}")
    print("SAGE_TOKEN=" + issue_session(u))


def _call(base, token, method, path, data=None, form=None):
    if form is not None:
        boundary = "----sage"
        body = b""
        for k, v in form.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n").encode() + str(v).encode() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(base + path, data=body, method=method,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                     method=method, headers={"Content-Type": "application/json"})
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path}: {e.code} {e.read().decode(errors='replace')[:400]}")


def post(base):
    token = os.environ.get("SAGE_TOKEN") or sys.exit("SAGE_TOKEN missing: run the account step first")
    me = _call(base, token, "GET", "/v1/me")
    have = {v["title"]: v["id"] for v in me.get("videos", [])}
    series = {s["name"]: s for s in _call(base, token, "GET", "/v1/series")["series"]}
    manifest = json.loads(MANIFEST.read_text())
    for dance in ("Salsa", "Bachata"):
        name = f"{dance} classes"
        if name not in series:
            series[name] = _call(base, token, "POST", "/v1/series", {"name": name})
            print("series", name)
    for item in sorted(manifest, key=lambda m: (m["dance"], m["num"])):
        if (item["dance"], item["num"]) not in FIRST:
            continue
        sc = json.loads(pathlib.Path(item["file"]).read_text())
        if sc["title"] in have:
            vid = have[sc["title"]]
        else:
            r = _call(base, token, "POST", "/v1/videos", form={
                "title": sc["title"], "note": sc["note"], "style": sc["style"], "level": sc["level"],
                "fps": sc["fps"], "visibility": "public",
                # millimetres are plenty; float32 artefacts would triple the size
                "pose3d": json.dumps({"j": [[[[round(x, 3) for x in p] for p in fr] for fr in d] for d in sc["j"]],
                                      "height": sc["height"], "centre": sc["centre"]}, separators=(",", ":")),
            })
            vid = r["id"]; have[sc["title"]] = vid
            print(f"posted {sc['title']} -> /v/{vid}")
        s = series[f"{item['dance']} classes"]
        if vid not in {v["id"] for v in s.get("videos", [])}:
            _call(base, token, "POST", f"/v1/series/{s['id']}/videos", {"video_id": vid})
            s.setdefault("videos", []).append({"id": vid})
    print(f"@{HANDLE} has {len(have)} classes at {base}/@{HANDLE}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "post"
    if cmd == "account":
        account()
    else:
        base = sys.argv[sys.argv.index("--base") + 1] if "--base" in sys.argv else "https://dancesage.com"
        post(base)
