"""Logs nur auf stdout – und wiederkehrende Meldungen gedrosselt.

Docker/Unraid begrenzt die Größe (--log-opt max-size). /var/log wird nie beschrieben.
"""
import logging
import sys
import threading
import time

log = logging.getLogger("ocr-watch")


def einrichten(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    for laut in ("uvicorn.access", "ocrmypdf", "PIL", "alembic", "httpx"):
        logging.getLogger(laut).setLevel(logging.WARNING)


class Drossel:
    """Meldet einen Zustand nur, wenn er sich ändert – oder höchstens alle `abstand` Sekunden.

    Beispiel: Ordner nicht erreichbar -> eine Warnung, danach Ruhe bis er wieder da ist
    (dann eine Info) oder bis eine Stunde vergangen ist.
    """

    def __init__(self, abstand: float = 3600):
        self.abstand = abstand
        self._zustand: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def melden(self, schluessel: str, zustand: str, text: str, level=logging.WARNING) -> None:
        jetzt = time.monotonic()
        with self._lock:
            alt = self._zustand.get(schluessel)
            if alt and alt[0] == zustand and jetzt - alt[1] < self.abstand:
                return
            self._zustand[schluessel] = (zustand, jetzt)
        log.log(level, text)

    def ok(self, schluessel: str, text: str) -> None:
        """Zustand wieder in Ordnung – nur melden, wenn vorher ein Problem gemeldet war."""
        with self._lock:
            alt = self._zustand.pop(schluessel, None)
        if alt:
            log.info(text)


drossel = Drossel()
