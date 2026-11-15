import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

os.umask(0o077)
settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False, "timeout": 30})


@event.listens_for(engine, "connect")
def configure(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db


@contextmanager
def transaction():
    with SessionLocal.begin() as db:
        yield db
