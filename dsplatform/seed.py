"""Seed the local database from real pose data produced by the research pipeline.

Placeholder skeletons would tell you nothing about how this looks, so the seed uses
actual captured dance: fourteen named figures, real 3D joints, real frame rates.
"""
import json, pathlib, random
from .db import Base, engine, SessionLocal
from .models import User, Video
from .storage import get_storage

RND = pathlib.Path("/Users/abduradi/Documents/dancesage/dancesage-research/rnd")
SCENES = RND / "bachata_out/scenes.json"
SOLO   = RND / "real_videos/solo_scene.json"   # real phone footage, has 2D + 3D

TEACHERS = [
    dict(handle="abdu", display_name="Abdu Radi", city="Toronto",
         styles="Bachata, Salsa", levels="Beginner, Intermediate",
         bio="Physicist and dancer. I teach the count first and the styling after — "
             "most people are told to feel it, which helps nobody who cannot yet hear it."),
    dict(handle="marialopez", display_name="Maria Lopez", city="Toronto",
         styles="Bachata", levels="All levels",
         bio="Sensual bachata, body movement, and the isolations everyone skips. "
             "Twelve years dancing, five teaching."),
    dict(handle="diegosr", display_name="Diego Serrano", city="Toronto",
         styles="Salsa On2, Bachata", levels="Intermediate, Advanced",
         bio="On2 salsa and partnerwork. I care about the lead being readable more "
             "than the pattern being clever."),
]

# which scenes belong to whom, and what they are called on a profile
ASSIGN = {
    "abdu":       [("basic", "The bachata basic, slowly", "Beginner"),
                   ("sidesteps", "Parallel side steps", "Beginner"),
                   ("zigzag", "Travelling zig-zag", "Beginner"),
                   ("improv", "Social dancing, unchoreographed", "Intermediate")],
    "marialopez": [("hip", "Hip isolation drill", "All levels"),
                   ("waist", "Waist cut — where the movement comes from", "All levels"),
                   ("head", "Head roll, and how not to lead with the neck", "Intermediate"),
                   ("style", "Lady styling, solo practice", "All levels")],
    "diegosr":    [("sideturn", "Parallel side turn", "Intermediate"),
                   ("armturn", "Turn at arm's length", "Intermediate"),
                   ("palms", "Circle palms — keeping the connection", "Advanced"),
                   ("tangle", "Shoulder tangle", "Advanced"),
                   ("crossarm", "Crossed-arm throw", "Advanced"),
                   ("lean", "Long-side lean", "Advanced")],
}


def run():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    scenes = json.loads(SCENES.read_text())
    store = get_storage()
    db = SessionLocal()
    nv = 0
    for spec in TEACHERS:
        u = User(**spec, takes_students=1, auth_uid=f"seed-{spec['handle']}")
        db.add(u); db.flush()
        for key, title, level in ASSIGN[u.handle]:
            sc = scenes.get(key)
            if not sc:
                continue
            pose_key = f"{u.handle}/{key}"
            store.put_pose(pose_key, {
                "fps": sc["fps"], "frames": sc["frames"], "height": sc["height"],
                "centre": sc["c"], "dancers": sc["n"], "j": sc["j"],
            })
            db.add(Video(user_id=u.id, title=title, note=sc.get("blurb", ""),
                         style="Bachata", level=level, pose_key=pose_key,
                         dancers=sc["n"], frames=sc["frames"], fps=sc["fps"]))
            nv += 1
    # One entry with real phone video, so video / skeleton / both can all be shown.
    if SOLO.exists():
        solo = json.loads(SOLO.read_text())
        abdu = db.execute(__import__("sqlalchemy").select(User)
                          .where(User.handle == "abdu")).scalar_one()
        store.put_pose("abdu/styling-3d", {
            "fps": solo["fps"], "frames": len(solo["fixed3d"][0]),
            "height": solo["height3d"], "centre": [0, 0, 0], "dancers": 1,
            "j": solo["fixed3d"],
        })
        store.put_pose("abdu/styling-2d", {
            "fps": solo["fps"], "frames": len(solo["smooth"][0]),
            "j": solo["smooth"], "vis": solo["vis"],
        })
        db.add(Video(user_id=abdu.id, title="Styling practice, filmed on a phone",
                     note="Recorded in the app. Switch between the video, the skeleton, "
                          "and both together.",
                     style="Bachata", level="All levels",
                     pose_key="abdu/styling-3d", pose2d_key="abdu/styling-2d",
                     video_key="abdu-styling", dancers=1,
                     frames=len(solo["smooth"][0]), fps=solo["fps"]))
        nv += 1
    db.commit()
    print(f"  seeded {len(TEACHERS)} teachers, {nv} videos")
    db.close()


if __name__ == "__main__":
    run()
