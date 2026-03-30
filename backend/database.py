import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
from dotenv import load_dotenv


load_dotenv()


BASE_DIR = os.path.dirname(__file__)
SQLITE_PATH = os.path.join(BASE_DIR, "billing.db")


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def get_database_url() -> str:
    raw_url = os.getenv("DATABASE_URL")
    if raw_url:
        return _normalize_database_url(raw_url)
    return f"sqlite:///{SQLITE_PATH}"


def _sqlite_connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


database_url = get_database_url()
engine = create_engine(
    database_url,
    future=True,
    pool_pre_ping=True,
    connect_args=_sqlite_connect_args(database_url),
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    # Run PRAGMA only for sqlite; issuing it on PostgreSQL aborts the transaction.
    if not dbapi_connection.__class__.__module__.startswith("sqlite3"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
)
Base = declarative_base()


def get_session():
    return SessionLocal()


def close_session():
    SessionLocal.remove()
