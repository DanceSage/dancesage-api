"""Postgres in the cloud, SQLite locally — the same models over both."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./dancesage.db")

# Hosts hand out `postgresql://…`; SQLAlchemy needs the driver named, and psycopg3
# is the one installed. Rewriting here means the connection string can be pasted
# in exactly as the provider gives it.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# On a mounted volume the directory exists but may be empty on first boot;
# SQLite will not create intermediate directories itself.
if DATABASE_URL.startswith("sqlite:////"):
    os.makedirs(os.path.dirname(DATABASE_URL.replace("sqlite:///", "")), exist_ok=True)

if IS_SQLITE:
    options = {"connect_args": {"check_same_thread": False}}
else:
    # A managed Postgres answers through pgbouncer in transaction mode, where a
    # prepared statement outlives the transaction that made it and the next
    # borrower of that connection fails on a name it never created. Turning
    # preparation off is the documented price of pooling.
    # pre_ping because a pooler drops idle connections and the first query after
    # a quiet night should reconnect rather than raise.
    options = {"connect_args": {"prepare_threshold": None},
               "pool_pre_ping": True, "pool_recycle": 600}

engine = create_engine(DATABASE_URL, **options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
