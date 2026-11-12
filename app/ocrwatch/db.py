"""Datenbank: Modelle, Verbindung, Migrationen (SQLite oder MariaDB)."""
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String, Text,
                        BigInteger, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import config


def jetzt() -> datetime:
    """Zeitpunkt in UTC ohne Zeitzone (gleich in SQLite und MariaDB)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def pfad_hash(pfad: str) -> str:
    return hashlib.sha256(pfad.encode("utf-8", "surrogateescape")).hexdigest()


class Base(DeclarativeBase):
    pass


class Einstellung(Base):
    __tablename__ = "einstellungen"
    schluessel: Mapped[str] = mapped_column(String(64), primary_key=True)
    wert: Mapped[object] = mapped_column(JSON, nullable=True)


class Profil(Base):
    __tablename__ = "profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True)
    pfad: Mapped[str] = mapped_column(String(1024))
    optionen: Mapped[dict] = mapped_column(JSON, default=dict)
    erstellt: Mapped[datetime] = mapped_column(DateTime, default=jetzt)
    geaendert: Mapped[datetime] = mapped_column(DateTime, default=jetzt, onupdate=jetzt)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profil_id: Mapped[int | None] = mapped_column(
        ForeignKey("profile.id", ondelete="SET NULL"), nullable=True, index=True)
    profil_name: Mapped[str] = mapped_column(String(100), default="")
    art: Mapped[str] = mapped_column(String(20))            # pdf | bilder | heic
    status: Mapped[str] = mapped_column(String(20), index=True)  # wartet|laeuft|fertig|uebersprungen|fehler|wiederholt
    quellen: Mapped[list] = mapped_column(JSON, default=list)    # ursprüngliche Pfade
    fehler_pfade: Mapped[list] = mapped_column(JSON, default=list)
    ziel: Mapped[str] = mapped_column(String(1024), default="")
    meldung: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    erstellt: Mapped[datetime] = mapped_column(DateTime, default=jetzt, index=True)
    gestartet: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    beendet: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Datei(Base):
    """Bereits gesehene/erzeugte Dateien – damit nichts doppelt verarbeitet wird."""
    __tablename__ = "dateien"
    pfad_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    pfad: Mapped[str] = mapped_column(String(1024))
    groesse: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[int] = mapped_column(BigInteger)
    profil_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20))        # eingereiht|erledigt|erzeugt
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gesehen: Mapped[datetime] = mapped_column(DateTime, default=jetzt)


_engine = None
_Session = None


def engine():
    global _engine, _Session
    if _engine is None:
        url = config.database_url()
        if url.startswith("sqlite"):
            _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(_engine, "connect")
            def _pragmas(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        else:
            _engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def sitzung():
    engine()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def migrieren() -> None:
    """Datenbank auf den neuesten Stand bringen (alembic upgrade head)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    with engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
