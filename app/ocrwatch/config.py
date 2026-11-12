"""Feste Einstellungen aus Umgebungsvariablen (nur Start und Datenbank).

Alles andere (Profile, Intervalle, MQTT, ...) liegt in der Datenbank und wird in der
Weboberfläche gepflegt.
"""
import os
from pathlib import Path
from urllib.parse import quote_plus


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DATA_ROOT = Path(_env("DATA_ROOT", "/data"))
CONFIG_DIR = Path(_env("CONFIG_DIR", "/config"))
UI_PORT = int(_env("UI_PORT", "8080") or 8080)
UI_PASS = _env("UI_PASS")

DB_TYPE = _env("DB_TYPE", "sqlite").lower()
DB_HOST = _env("DB_HOST", "mariadb")
DB_PORT = int(_env("DB_PORT", "3306") or 3306)
DB_NAME = _env("DB_NAME", "ocrwatch")
DB_USER = _env("DB_USER", "ocrwatch")
DB_PASS = _env("DB_PASS")


def database_url() -> str:
    if DB_TYPE in ("mariadb", "mysql"):
        return (
            f"mysql+pymysql://{quote_plus(DB_USER)}:{quote_plus(DB_PASS)}"
            f"@{DB_HOST}:{DB_PORT}/{quote_plus(DB_NAME)}?charset=utf8mb4"
        )
    if DB_TYPE != "sqlite":
        raise SystemExit(f"DB_TYPE '{DB_TYPE}' unbekannt (erlaubt: sqlite, mariadb)")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{CONFIG_DIR / 'ocr-watch.db'}"


def database_label() -> str:
    if DB_TYPE in ("mariadb", "mysql"):
        return f"MariaDB {DB_HOST}:{DB_PORT}/{DB_NAME}"
    return f"SQLite {CONFIG_DIR / 'ocr-watch.db'}"
