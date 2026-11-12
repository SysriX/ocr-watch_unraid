"""Start: Datenbank vorbereiten, Hintergrunddienste starten, Weboberfläche ausliefern."""
import sys
import time

import uvicorn

from . import __version__, config, dienst
from . import einstellungen as einst
from .db import migrieren
from .protokoll import einrichten, log


def datenbank_bereit(versuche: int = 30) -> None:
    """MariaDB startet evtl. nach uns – bis zu 30 × 5 s warten."""
    for i in range(1, versuche + 1):
        try:
            migrieren()
            return
        except Exception as e:
            if config.DB_TYPE == "sqlite" or i == versuche:
                log.error("Datenbank nicht nutzbar (%s): %s", config.database_label(), e)
                sys.exit(1)
            if i == 1 or i % 6 == 0:
                log.warning("Warte auf Datenbank %s … (%s)", config.database_label(), str(e).splitlines()[0])
            time.sleep(5)


def main() -> None:
    einrichten()
    log.info("ocr-watch %s startet – Datenbank: %s, Daten: %s", __version__, config.database_label(),
             config.DATA_ROOT)
    datenbank_bereit()
    einst.initialisieren()
    einrichten(einst.wert("log_level") or "INFO")
    if not config.UI_PASS and not einst.wert("ui_pass_hash"):
        log.warning("Kein Passwort gesetzt (UI_PASS leer) – bitte in der Weboberfläche festlegen")

    from .web import erstellen
    from .mqtt import Melder

    dienst.starten()
    app = erstellen()
    app.state.melder = Melder(dienst.stopp)
    app.state.melder.start()

    # uvicorn fängt SIGTERM/SIGINT selbst ab und kehrt dann zurück
    uvicorn.run(app, host="0.0.0.0", port=config.UI_PORT, log_level="warning", access_log=False,
                proxy_headers=True, forwarded_allow_ips="*")
    log.info("ocr-watch wird beendet")
    dienst.stopp.set()


if __name__ == "__main__":
    main()
