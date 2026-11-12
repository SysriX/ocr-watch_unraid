"""Allgemeine Einstellungen und Profil-Optionen (beides in der Datenbank)."""
import copy
import os
import secrets
import threading
import time

from .db import Einstellung, sitzung

STANDARD = {
    "abfrage_intervall": 10,       # Sekunden zwischen zwei Durchläufen der Ordner
    "stabil_sekunden": 15,         # so lange darf sich eine Datei nicht ändern
    "parallel": 1,                 # gleichzeitige Aufträge
    "ocr_threads": 2,              # CPU-Threads je OCR-Auftrag
    "timeout_minuten": 30,         # Abbruch für einen einzelnen Auftrag
    "sprachen": "deu+eng",         # Standard für neue Profile
    "job_aufbewahrung_tage": 30,
    "log_level": "INFO",
    "mqtt_aktiv": False,
    "mqtt_host": "",
    "mqtt_port": 1883,
    "mqtt_user": "",
    "mqtt_pass": "",
    "mqtt_prefix": "homeassistant",
    "ha_geraet": "ocr-watch",
    "mqtt_intervall": 60,
}

# Werte, die beim ersten Start aus Umgebungsvariablen übernommen werden (falls gesetzt)
_AUS_UMGEBUNG = {
    "mqtt_host": "MQTT_HOST", "mqtt_port": "MQTT_PORT", "mqtt_user": "MQTT_USER",
    "mqtt_pass": "MQTT_PASS", "mqtt_prefix": "MQTT_PREFIX", "ha_geraet": "HA_GERAET",
}

_cache: dict = {}
_cache_zeit = 0.0
_lock = threading.Lock()


def initialisieren() -> None:
    """Fehlende Einstellungen mit Standardwerten anlegen."""
    with sitzung() as s:
        vorhanden = {e.schluessel for e in s.query(Einstellung).all()}
        neu = {}
        for k, v in STANDARD.items():
            env = _AUS_UMGEBUNG.get(k)
            if env and os.environ.get(env, "").strip():
                roh = os.environ[env].strip()
                v = int(roh) if isinstance(v, int) and roh.isdigit() else roh
            neu[k] = v
        if neu["mqtt_host"]:
            neu["mqtt_aktiv"] = True
        neu["geheimnis"] = secrets.token_hex(32)
        for k, v in neu.items():
            if k not in vorhanden:
                s.add(Einstellung(schluessel=k, wert=v))
    neu_laden()


def neu_laden() -> None:
    global _cache, _cache_zeit
    with sitzung() as s:
        werte = {e.schluessel: e.wert for e in s.query(Einstellung).all()}
    with _lock:
        _cache = {**STANDARD, **werte}
        _cache_zeit = time.monotonic()


def alle() -> dict:
    if time.monotonic() - _cache_zeit > 30:
        neu_laden()
    with _lock:
        return dict(_cache)


def wert(schluessel: str):
    return alle().get(schluessel, STANDARD.get(schluessel))


def speichern(neu: dict) -> None:
    with sitzung() as s:
        for k, v in neu.items():
            s.merge(Einstellung(schluessel=k, wert=v))
    neu_laden()


# ---------------------------------------------------------------- Profile

BILD_TYPEN = ["jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp", "gif", "heic", "heif"]

PROFIL_STANDARD = {
    "rekursiv": True,
    "vorhandene_verarbeiten": True,
    "sprachen": "",                 # leer = allgemeine Einstellung
    "ausgabe": "inplace",           # inplace | ordner
    "ausgabe_ordner": "",           # bei "ordner": Pfad unter /data
    "original": "archivieren",      # archivieren | loeschen | behalten
    "archiv_ordner": "",            # leer = <Watch-Ordner>/_archiv
    "fehler_ordner": "",            # leer = <Watch-Ordner>/_fehler
    "pdf": {
        "aktiv": True,
        "modus": "auto",            # auto | skip-text | redo-ocr | force-ocr
        "min_zeichen": 50,          # Seite gilt ab so vielen Zeichen als lesbar
        "min_qualitaet": 60,        # % Buchstaben/Ziffern im Text, darunter = Müll-OCR
        "pdfa": True,
        "drehen": True,
        "begradigen": True,
        "optimieren": 1,            # 0-3
    },
    "bilder": {
        "aktiv": False,
        "typen": ["jpg", "jpeg", "png", "tif", "tiff"],
        "buendeln": "einzeln",      # einzeln | ordner | zeitfenster
        "wartezeit": 120,           # Sekunden ohne neues Bild, dann bündeln
    },
    "heic": {
        "aktiv": False,
        "ziel": "jpg",              # jpg | png | webp
        "qualitaet": 90,
        "exif_behalten": True,
        "original": "archivieren",  # archivieren | loeschen | behalten
    },
}


def _mischen(basis: dict, neu: dict) -> dict:
    erg = copy.deepcopy(basis)
    for k, v in (neu or {}).items():
        if isinstance(v, dict) and isinstance(erg.get(k), dict):
            erg[k] = _mischen(erg[k], v)
        else:
            erg[k] = v
    return erg


def profil_optionen(gespeichert: dict | None) -> dict:
    """Gespeicherte Optionen mit Standardwerten auffüllen (neue Optionen nach Updates)."""
    return _mischen(PROFIL_STANDARD, gespeichert or {})
