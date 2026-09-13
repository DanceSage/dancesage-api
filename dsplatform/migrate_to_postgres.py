"""Copy a SQLite database into the configured Postgres, ids and all.

    DATABASE_URL=postgresql://... python -m dsplatform.migrate_to_postgres /path/to/dancesage.db

Rows carry their primary keys across, because everything else points at them:
a grant names a video id, a series item names a video id, and a pose key in R2
is filed under an id. Renumbering would strand all of it.

Order matters — a row cannot arrive before the row it references. `videos` is
ordered by id because a reply points at the video it answers, and that video was
always posted first.

Afterwards each sequence is advanced past the highest id it copied. Postgres does
not notice ids inserted by hand, and without this the next insert collides with
row one.

Safe to re-run: it refuses a table that already holds rows unless --force.
"""
import gzip, sys, pathlib
from sqlalchemy import create_engine, text, insert, select, func
from .db import engine, IS_SQLITE
from .models import Base

ORDER = ["users", "groups", "series", "videos", "series_videos",
         "series_grants", "grants", "group_members", "lessons"]


def run(src: pathlib.Path, force: bool = False) -> int:
    if IS_SQLITE:
        print("DATABASE_URL still points at SQLite — nothing to migrate into.")
        return 1
    if not src.exists():
        print(f"no such database: {src}")
        return 1

    source = create_engine(f"sqlite:///{src}")
    Base.metadata.create_all(engine)
    print(f"schema ready on {engine.url.host or engine.url.database}")

    tables = {t.name: t for t in Base.metadata.sorted_tables}
    missing = [n for n in tables if n not in ORDER]
    if missing:
        print(f"refusing to run: {missing} is not in the copy order — add it first")
        return 1

    total = 0
    for name in ORDER:
        table = tables[name]
        with engine.begin() as dest:
            already = dest.execute(select(func.count()).select_from(table)).scalar_one()
            if already and not force:
                print(f"  {name:15} skipped, already holds {already} rows")
                continue
            with source.connect() as s:
                cols = [c.name for c in table.columns]
                order = " order by id" if "id" in cols else ""
                rows = [dict(r._mapping) for r in
                        s.execute(text(f'select {", ".join(cols)} from "{name}"{order}'))]
            if rows:
                dest.execute(insert(table), rows)
            if "id" in cols:
                dest.execute(text(
                    f"select setval(pg_get_serial_sequence('{name}', 'id'), "
                    f"coalesce((select max(id) from \"{name}\"), 1))"))
            print(f"  {name:15} {len(rows):5} rows")
            total += len(rows)

    print(f"{total} rows copied")
    return 0


def verify(src: pathlib.Path) -> int:
    """Count every table on both sides and report any that disagree."""
    source = create_engine(f"sqlite:///{src}")
    bad = 0
    for name in ORDER:
        with source.connect() as s:
            a = s.execute(text(f'select count(*) from "{name}"')).scalar_one()
        with engine.connect() as d:
            b = d.execute(text(f'select count(*) from "{name}"')).scalar_one()
        mark = "ok " if a == b else "DIFF"
        if a != b:
            bad += 1
        print(f"  {mark} {name:15} sqlite {a:5}   postgres {b:5}")
    print("identical" if not bad else f"{bad} table(s) differ")
    return 0 if not bad else 1


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1
    src = pathlib.Path(args[0])
    if src.suffix == ".gz":                      # a backup, straight from R2
        raw = src.with_suffix("")
        raw.write_bytes(gzip.decompress(src.read_bytes()))
        src = raw
    if "--verify" in sys.argv:
        return verify(src)
    return run(src, force="--force" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
