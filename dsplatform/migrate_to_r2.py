"""Copy everything in local storage up to R2, then verify every object.

Copies only — nothing local is deleted, so the machine stays a complete fallback
until you choose otherwise. Safe to re-run: existing objects are skipped unless
--force is given.

    STORAGE_BACKEND stays unset here; both backends are constructed explicitly so
    the migration cannot accidentally read from the place it is writing to.

    python -m dsplatform.migrate_to_r2 [--dry-run] [--force]
"""
import argparse
import os
import pathlib
import sys

from .storage import ROOT, LocalStorage, R2Storage, _inflate

REQUIRED = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]


def _keys() -> list[tuple[str, str, pathlib.Path]]:
    """(kind, key, path) for every stored object."""
    out = []
    for p in sorted((ROOT / "pose").rglob("*.json")):
        out.append(("pose", str(p.relative_to(ROOT / "pose")).removesuffix(".json"), p))
    for p in sorted((ROOT / "video").glob("*.mov")):
        out.append(("video", p.stem, p))
    for p in sorted((ROOT / "avatar").glob("*.jpg")):
        out.append(("avatar", p.stem, p))
    return out


def _remote_key(kind: str, key: str) -> str:
    return {"pose": f"pose/{key}.json", "video": f"video/{key}.mov",
            "avatar": f"avatar/{key}.jpg"}[kind]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list what would move")
    ap.add_argument("--force", action="store_true", help="overwrite objects already there")
    args = ap.parse_args()

    items = _keys()
    if not items:
        print("nothing in local storage to migrate")
        return 0

    total = sum(p.stat().st_size for _, _, p in items)
    print(f"{len(items)} objects, {total/1048576:.2f} MB in {ROOT}")
    for kind in ("pose", "video", "avatar"):
        n = [i for i in items if i[0] == kind]
        if n:
            size = sum(p.stat().st_size for _, _, p in n)
            print(f"  {kind:<7} {len(n):>3} objects  {size/1048576:>7.2f} MB")

    if args.dry_run:
        print("\n--dry-run: nothing uploaded")
        return 0

    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print("\nMissing:", ", ".join(missing))
        print("Run `python -m dsplatform.check_r2` first.")
        return 1

    local, remote = LocalStorage(), R2Storage()
    print(f"\n-> bucket {remote.bucket}\n")

    from botocore.exceptions import ClientError
    moved = skipped = failed = 0

    for kind, key, path in items:
        rkey = _remote_key(kind, key)
        label = f"  {kind:<7} {key[:38]:<40}"
        if not args.force:
            try:
                remote.s3.head_object(Bucket=remote.bucket, Key=rkey)
                print(f"{label} already there")
                skipped += 1
                continue
            except ClientError:
                pass
        try:
            if kind == "pose":
                # Re-encoded through put_pose so it lands compacted and gzipped,
                # whatever shape it happened to be in on disk.
                remote.put_pose(key, local.get_pose(key))
                assert _inflate(remote._get(rkey))["j"], "empty after upload"
            elif kind == "video":
                remote.put_video(key, path.read_bytes())
                assert remote.s3.head_object(Bucket=remote.bucket,
                                             Key=rkey)["ContentLength"] > 0
            else:
                remote.put_avatar(key, path.read_bytes())
                assert remote.s3.head_object(Bucket=remote.bucket,
                                             Key=rkey)["ContentLength"] > 0
            print(f"{label} ok")
            moved += 1
        except Exception as e:
            print(f"{label} FAILED — {e}")
            failed += 1

    print(f"\n{moved} uploaded, {skipped} already there, {failed} failed")
    if failed:
        print("Local files are untouched. Fix the errors and re-run.")
        return 1
    print("\nEverything verified. To use R2:  export STORAGE_BACKEND=r2")
    print("Local storage is left in place as a fallback.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
