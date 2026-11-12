"""Kennzahlen für Übersicht und Home Assistant."""
from datetime import datetime, time as dtime, timezone
from pathlib import Path

from sqlalchemy import func, select

from .db import Job, sitzung


def _heute_utc() -> datetime:
    lokal = datetime.combine(datetime.now().date(), dtime.min).astimezone()
    return lokal.astimezone(timezone.utc).replace(tzinfo=None)


def kennzahlen() -> dict:
    from .dienst import zustand
    ab = _heute_utc()
    with sitzung() as s:
        nach_status = dict(s.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        heute = dict(s.execute(select(Job.status, func.count()).where(Job.erstellt >= ab)
                               .group_by(Job.status)).all())
        letzter = s.scalars(select(Job).where(Job.status.in_(["fertig", "fehler"]))
                            .order_by(Job.beendet.desc()).limit(1)).first()
        letzter_d = None
        if letzter:
            letzter_d = {"id": letzter.id, "status": letzter.status, "profil": letzter.profil_name,
                         "datei": Path(letzter.quellen[0]).name if letzter.quellen else "",
                         "ziel": letzter.ziel, "beendet": letzter.beendet}
    profile = zustand.get("profile", {})
    return {
        "wartet": nach_status.get("wartet", 0),
        "laeuft": nach_status.get("laeuft", 0),
        "fehler_offen": nach_status.get("fehler", 0),
        "heute_fertig": heute.get("fertig", 0),
        "heute_uebersprungen": heute.get("uebersprungen", 0),
        "heute_fehler": heute.get("fehler", 0),
        "ordner_weg": sum(1 for p in profile.values() if p.get("ok") is False),
        "letzter": letzter_d,
    }
