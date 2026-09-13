"""Back the database up to R2.

The database is the part that cannot be rebuilt. R2 holds the pose tracks and
the video, but nothing there says who owns them — lose the volume and you keep a
bucket of anonymous files. So this runs on a schedule and keeps a rolling window.

Uses SQLite's own backup API rather than copying the file: a plain copy of a
database being written to is not guaranteed to be a valid database.

    python -m dsplatform.backup            take one
    python -m dsplatform.backup --list     what is stored
    python -m dsplatform.backup --restore backups/2026-08-30T04-00-00.db
"""
import argparse
import datetime as dt
import gzip
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

KEEP = int(os.environ.get("BACKUP_KEEP", "30"))
PREFIX = "backups/"


def _db_path() -> pathlib.Path:
    url = os.environ.get("DATABASE_URL", "sqlite:///./dancesage.db")
    return pathlib.Path(url.replace("sqlite:////", "/").replace("sqlite:///", ""))


def _client():
    from .storage import R2Storage
    return R2Storage()


def _snapshot(src: pathlib.Path) -> bytes:
    """A consistent copy, taken while the server may be mid-write."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        out = pathlib.Path(tmp.name)
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        dest = sqlite3.connect(out)
        with dest:
            source.backup(dest)
        source.close(); dest.close()
        return gzip.compress(out.read_bytes(), 6)
    finally:
        out.unlink(missing_ok=True)


def _pg_dump() -> bytes:
    """Postgres, as a plain SQL dump.

    The provider takes its own snapshots, but those are theirs: they end when the
    account does, and they cannot be read anywhere else. A dump in our own bucket
    restores into any Postgres, which is the property that matters when the thing
    going wrong is the provider.
    """
    url = os.environ["DATABASE_URL"]
    out = subprocess.run(["pg_dump", "--no-owner", "--no-privileges", url],
                         capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {out.stderr.decode()[-400:]}")
    return gzip.compress(out.stdout, 6)


def take() -> int:
    from .db import IS_SQLITE
    st = _client()
    stamp = dt.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")

    if not IS_SQLITE:
        blob = _pg_dump()
        key = PREFIX + stamp + ".sql.gz"
        st.s3.put_object(Bucket=st.bucket, Key=key, Body=blob,
                         ContentType="application/gzip")
        print(f"{key}  {len(blob)/1024:.0f} KB  (pg_dump)")
        _prune(st)
        return 0

    src = _db_path()
    if not src.exists():
        print(f"no database at {src}")
        return 1
    blob = _snapshot(src)
    key = PREFIX + stamp + ".db.gz"
    st.s3.put_object(Bucket=st.bucket, Key=key, Body=blob,
                     ContentType="application/gzip")
    print(f"{key}  {len(blob)/1024:.0f} KB  (from {src.stat().st_size/1024:.0f} KB)")
    _prune(st)
    return 0


def _prune(st) -> None:
    """Rolling window. Old backups cost money and answer no question a recent one
    does not, but keep enough that a problem noticed late is still recoverable."""
    keys = sorted(o["Key"] for o in
                  st.s3.list_objects_v2(Bucket=st.bucket, Prefix=PREFIX)
                    .get("Contents", []))
    for old in keys[:-KEEP]:
        st.s3.delete_object(Bucket=st.bucket, Key=old)
        print(f"  pruned {old}")
    print(f"  {min(len(keys), KEEP)} backups retained")


def show() -> int:
    st = _client()
    items = st.s3.list_objects_v2(Bucket=st.bucket, Prefix=PREFIX).get("Contents", [])
    if not items:
        print("no backups yet")
        return 0
    for o in sorted(items, key=lambda x: x["Key"]):
        print(f"  {o['Key']}  {o['Size']/1024:>6.0f} KB  {o['LastModified']:%Y-%m-%d %H:%M}")
    print(f"\n{len(items)} backups")
    return 0


def restore(key: str) -> int:
    """Writes over the live database. Deliberately not automatic."""
    from .db import IS_SQLITE
    st = _client()
    if not IS_SQLITE:
        # A SQL dump has to be replayed by Postgres itself, and doing that over a
        # live database is a decision with consequences — so this prints the
        # command instead of running it.
        print("Postgres restores are run by hand, on purpose:\n"
              f"  fly storage ... aws s3 cp s3://<bucket>/{key} - | gunzip | psql \"$DATABASE_URL\"\n"
              "Drop or rename the existing schema first, or the replay will collide.")
        return 1
    blob = st.s3.get_object(Bucket=st.bucket, Key=key)["Body"].read()
    data = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob
    dest = _db_path()
    if dest.exists():
        aside = dest.with_suffix(f".before-restore-{int(dt.datetime.utcnow().timestamp())}")
        dest.rename(aside)
        print(f"  moved the current database to {aside.name}")
    dest.write_bytes(data)
    con = sqlite3.connect(dest)
    users = con.execute("select count(*) from users").fetchone()[0]
    videos = con.execute("select count(*) from videos").fetchone()[0]
    con.close()
    print(f"restored {key}: {users} users, {videos} videos")
    print("Restart the server.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", metavar="KEY")
    a = ap.parse_args()
    if a.list:
        return show()
    if a.restore:
        return restore(a.restore)
    return take()


if __name__ == "__main__":
    sys.exit(main())
