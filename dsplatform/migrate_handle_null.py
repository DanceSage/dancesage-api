"""Make users.handle nullable.

The column is UNIQUE, and new accounts had no handle yet. Storing "" meant the
second person to sign up collided with the first on a value neither had chosen.
NULL is the right way to say "not yet": SQL lets many rows be NULL and only one
be the empty string.

SQLite cannot drop NOT NULL in place, so the table is rebuilt. Safe to re-run.
"""
import sqlite3
import sys


def migrate(path: str) -> int:
    con = sqlite3.connect(path)
    schema = con.execute(
        "select sql from sqlite_master where type='table' and name='users'"
    ).fetchone()
    if not schema:
        print("no users table"); return 1
    if "handle VARCHAR(40) NOT NULL" not in schema[0]:
        print("  already nullable")
        con.close(); return 0

    cols = [r[1] for r in con.execute("PRAGMA table_info(users)")]
    names = ", ".join(cols)
    new_schema = schema[0].replace("handle VARCHAR(40) NOT NULL",
                                   "handle VARCHAR(40)").replace(
                                   'CREATE TABLE users', 'CREATE TABLE users_new')

    con.execute("PRAGMA foreign_keys=off")
    with con:
        con.execute(new_schema)
        con.execute(f"INSERT INTO users_new ({names}) SELECT {names} FROM users")
        con.execute("DROP TABLE users")
        con.execute("ALTER TABLE users_new RENAME TO users")
        # Indexes went with the old table.
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_handle ON users (handle)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_auth_uid ON users (auth_uid)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_users_city ON users (city)")
        con.execute("UPDATE users SET handle = NULL WHERE handle = ''")
    con.execute("PRAGMA foreign_keys=on")
    bad = con.execute("PRAGMA foreign_key_check").fetchall()
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    n = con.execute("select count(*) from users").fetchone()[0]
    print(f"  rebuilt: {n} users, integrity {ok}, fk problems {len(bad)}")
    con.close()
    return 0 if ok == "ok" and not bad else 1


if __name__ == "__main__":
    import os
    url = os.environ.get("DATABASE_URL", "sqlite:///./dancesage.db")
    sys.exit(migrate(url.replace("sqlite:////", "/").replace("sqlite:///", "")))
