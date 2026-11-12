"""Hintergrund: Ordner abfragen (Polling), Aufträge anlegen und abarbeiten."""
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from . import einstellungen as einst
from . import verarbeitung as verarb
from .db import Datei, Job, Profil, jetzt, pfad_hash, sitzung
from .protokoll import drossel, log

IGNORIERTE_ORDNER = {"@eaDir", "#recycle", "#snapshot", "$RECYCLE.BIN", "System Volume Information", "lost+found"}
IGNORIERTE_ENDUNGEN = (".part", ".tmp", ".crdownload", ".partial", ".download", ".fehler.txt")

stopp = threading.Event()
zustand: dict = {"profile": {}, "gestartet": time.time(), "letzter_lauf": None}
_job_lock = threading.Lock()


def art_fuer(datei: Path, opt: dict) -> str | None:
    endung = datei.suffix.lower().lstrip(".")
    if endung == "pdf":
        return "pdf" if opt["pdf"]["aktiv"] else None
    if endung in ("heic", "heif") and opt["heic"]["aktiv"]:
        return "heic"
    if opt["bilder"]["aktiv"] and endung in {t.lower().lstrip(".") for t in opt["bilder"]["typen"]}:
        return "bild"
    return None


def dateien(ordner: verarb.Ordner, rekursiv: bool):
    sonder = {str(p) for p in ordner.sonderordner()}
    stapel = [ordner.wurzel]
    while stapel:
        d = stapel.pop()
        try:
            eintraege = list(os.scandir(d))
        except OSError as e:
            log.debug("Ordner nicht lesbar: %s (%s)", d, e)
            continue
        for e in eintraege:
            name = e.name
            if name.startswith(".") or name.startswith("~$"):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if rekursiv and name not in IGNORIERTE_ORDNER and e.path not in sonder:
                        stapel.append(Path(e.path))
                elif e.is_file(follow_symlinks=False) and not name.lower().endswith(IGNORIERTE_ENDUNGEN):
                    st = e.stat(follow_symlinks=False)
                    yield Path(e.path), st.st_size, st.st_mtime_ns
            except OSError:
                continue


class Beobachter(threading.Thread):
    """Fragt alle aktiven Profile regelmäßig ab. Polling statt inotify, weil inotify auf
    SMB/NFS-Freigaben (z. B. TrueNAS über Unassigned Devices) keine Änderungen meldet."""

    def __init__(self):
        super().__init__(name="beobachter", daemon=True)
        self.stabil: dict[str, tuple[int, int, float]] = {}   # pfad -> (größe, mtime, seit)
        self.gruppen: dict[tuple, dict] = {}                  # (profil, schlüssel) -> {pfade, letzte, ...}

    def run(self):
        while not stopp.is_set():
            try:
                self.durchlauf()
            except Exception:
                log.exception("Fehler beim Abfragen der Ordner")
            stopp.wait(max(2, int(einst.wert("abfrage_intervall") or 10)))

    def durchlauf(self):
        with sitzung() as s:
            profile = [(p.id, p.name, p.pfad, einst.profil_optionen(p.optionen))
                       for p in s.scalars(select(Profil).where(Profil.aktiv.is_(True)))]
        gesehen: set[str] = set()
        aktive_ids = set()
        for pid, name, pfad, opt in profile:
            aktive_ids.add(pid)
            try:
                self.profil_abfragen(pid, name, pfad, opt, gesehen)
            except Exception as e:
                drossel.melden(f"profil-{pid}", "fehler", f"[{name}] Fehler beim Abfragen: {e}")
        for k in [k for k in self.stabil if k not in gesehen]:
            del self.stabil[k]
        for k in [k for k in zustand["profile"] if k not in aktive_ids]:
            del zustand["profile"][k]
        self.gruppen_pruefen()
        zustand["letzter_lauf"] = time.time()

    def profil_abfragen(self, pid, name, pfad, opt, gesehen):
        ordner = verarb.Ordner(pfad, opt)
        z = zustand["profile"].setdefault(pid, {"name": name})
        z["name"] = name
        if not ordner.wurzel.is_dir():
            z.update(ok=False, meldung="Ordner nicht erreichbar", dateien=0)
            drossel.melden(f"ordner-{pid}", "weg", f"[{name}] Ordner {pfad} nicht erreichbar – Profil pausiert")
            return
        drossel.ok(f"ordner-{pid}", f"[{name}] Ordner {pfad} wieder erreichbar")

        with sitzung() as s:
            bekannt = {d.pfad_hash: (d.groesse, d.mtime)
                       for d in s.scalars(select(Datei).where(Datei.profil_id == pid))}
        erstscan = not opt.get("_erstscan_erledigt")
        nur_merken = erstscan and not opt.get("vorhandene_verarbeiten", True)
        stabil_sek = int(einst.wert("stabil_sekunden") or 15)
        jetzt_t = time.time()
        neu_erledigt, anzahl = [], 0

        for datei, groesse, mtime in dateien(ordner, opt.get("rekursiv", True)):
            art = art_fuer(datei, opt)
            if not art:
                continue
            anzahl += 1
            key = str(datei)
            gesehen.add(key)
            h = pfad_hash(key)
            if bekannt.get(h) == (groesse, mtime) or self.in_gruppe(pid, key, groesse, mtime):
                continue
            if nur_merken:
                neu_erledigt.append(Datei(pfad_hash=h, pfad=key, groesse=groesse, mtime=mtime,
                                          profil_id=pid, status="erledigt"))
                continue
            alt = self.stabil.get(key)
            if not alt or alt[:2] != (groesse, mtime):
                self.stabil[key] = (groesse, mtime, jetzt_t)
                continue
            if jetzt_t - alt[2] < stabil_sek or groesse == 0:
                continue
            if not _lesbar(datei):
                continue
            self.einreihen(pid, name, opt, ordner, art, datei, groesse, mtime)
            del self.stabil[key]

        if neu_erledigt:
            with sitzung() as s:
                for d in neu_erledigt:
                    s.merge(d)
            log.info("[%s] %d vorhandene Dateien übernommen (nicht verarbeitet)", name, len(neu_erledigt))
        if erstscan:
            with sitzung() as s:
                p = s.get(Profil, pid)
                if p:
                    p.optionen = {**(p.optionen or {}), "_erstscan_erledigt": True}
        z.update(ok=True, meldung="", dateien=anzahl, pfad=pfad)

    def in_gruppe(self, pid, pfad, groesse, mtime) -> bool:
        for (gpid, _), g in self.gruppen.items():
            if gpid == pid and g["pfade"].get(pfad) == (groesse, mtime):
                return True
        return False

    def einreihen(self, pid, name, opt, ordner, art, datei, groesse, mtime):
        if art == "bild" and opt["bilder"].get("buendeln", "einzeln") != "einzeln":
            modus = opt["bilder"]["buendeln"]
            schl = (pid, str(datei.parent) if modus == "ordner" else "")
            g = self.gruppen.setdefault(schl, {"pfade": {}, "name": name, "opt": opt, "pfad": str(ordner.wurzel),
                                               "modus": modus})
            g["pfade"][str(datei)] = (groesse, mtime)
            g["letzte"] = time.time()
            g["opt"] = opt
            return
        zielname = datei.stem + ".pdf" if art == "bild" else ""
        auftrag_anlegen(pid, name, "bilder" if art == "bild" else art,
                        {str(datei): (groesse, mtime)}, zielname)

    def gruppen_pruefen(self):
        for schl in list(self.gruppen):
            g = self.gruppen[schl]
            if time.time() - g["letzte"] < int(g["opt"]["bilder"].get("wartezeit", 120)):
                continue
            del self.gruppen[schl]
            erste = Path(sorted(g["pfade"])[0])
            stempel = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            if g["modus"] == "ordner":
                basis = erste.parent.name if erste.parent != Path(g["pfad"]) else "Bilder"
                zielname = f"{basis}_{stempel}.pdf"
            else:
                zielname = f"Scan_{stempel}.pdf"
            auftrag_anlegen(schl[0], g["name"], "bilder", g["pfade"], zielname)


def _lesbar(datei: Path) -> bool:
    """Lässt sich die Datei öffnen? (Schreibt ein anderes Programm noch exklusiv?)"""
    try:
        with open(datei, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


def auftrag_anlegen(pid: int, name: str, art: str, pfade: dict, zielname: str = "") -> int:
    with sitzung() as s:
        job = Job(profil_id=pid, profil_name=name, art=art, status="wartet",
                  quellen=sorted(pfade), fehler_pfade=[], ziel="", meldung="",
                  details={"zielname": zielname} if zielname else {})
        s.add(job)
        s.flush()
        for p, (groesse, mtime) in pfade.items():
            s.merge(Datei(pfad_hash=pfad_hash(p), pfad=p, groesse=groesse, mtime=mtime,
                          profil_id=pid, status="eingereiht", job_id=job.id, gesehen=jetzt()))
        jid = job.id
    log.info("[%s] Auftrag #%d: %s (%d Datei%s)", name, jid, art, len(pfade), "" if len(pfade) == 1 else "en")
    return jid


# ------------------------------------------------------------------ Abarbeiten

class Arbeiter(threading.Thread):
    def __init__(self, nr: int):
        super().__init__(name=f"arbeiter-{nr}", daemon=True)
        self.nr = nr

    def run(self):
        while not stopp.is_set():
            if self.nr >= int(einst.wert("parallel") or 1):
                stopp.wait(10)
                continue
            jid = self.nehmen()
            if jid is None:
                stopp.wait(2)
                continue
            try:
                ausfuehren(jid)
            except Exception:
                log.exception("Auftrag #%d: unerwarteter Fehler", jid)

    @staticmethod
    def nehmen() -> int | None:
        with _job_lock, sitzung() as s:
            job = s.scalars(select(Job).where(Job.status == "wartet").order_by(Job.id).limit(1)).first()
            if not job:
                return None
            job.status = "laeuft"
            job.gestartet = jetzt()
            return job.id


def ausfuehren(jid: int) -> None:
    with sitzung() as s:
        job = s.get(Job, jid)
        profil = s.get(Profil, job.profil_id) if job.profil_id else None
        art, quellen, details = job.art, [Path(q) for q in job.quellen], dict(job.details or {})
        name = job.profil_name
        if profil:
            ordner = verarb.Ordner(profil.pfad, einst.profil_optionen(profil.optionen))
    if not profil:
        _abschliessen(jid, "fehler", "Profil gelöscht", quellen)
        return

    vorhanden = [q for q in quellen if q.exists()]
    if not vorhanden:
        _abschliessen(jid, "uebersprungen", "Datei nicht mehr vorhanden", quellen)
        return

    start = time.time()
    try:
        if art == "pdf":
            erg = verarb.pdf_verarbeiten(vorhanden[0], ordner)
        elif art == "heic":
            erg = verarb.heic_verarbeiten(vorhanden[0], ordner)
        else:
            erg = verarb.bilder_verarbeiten(vorhanden, ordner, details.get("zielname") or vorhanden[0].stem + ".pdf")
    except verarb.Uebersprungen as e:
        log.info("[%s] #%d %s: übersprungen – %s", name, jid, quellen[0].name, e)
        _abschliessen(jid, "uebersprungen", str(e), quellen)
        return
    except Exception as e:
        grund = str(e) or e.__class__.__name__
        log.warning("[%s] #%d %s: FEHLER – %s", name, jid, quellen[0].name, grund.splitlines()[0])
        try:
            fehler_pfade = verarb.in_fehlerordner(vorhanden, ordner, grund)
        except Exception as e2:
            fehler_pfade = []
            grund += f"\n(Verschieben in den Fehlerordner ging nicht: {e2})"
        _abschliessen(jid, "fehler", grund, quellen, fehler_pfade=fehler_pfade)
        return

    erg["dauer_s"] = round(time.time() - start, 1)
    log.info("[%s] #%d %s -> %s (%.0f s)", name, jid, quellen[0].name, Path(erg["ziel"]).name, erg["dauer_s"])
    _abschliessen(jid, "fertig", erg.get("grund") or "", quellen, ziel=erg["ziel"], details=erg)


def _abschliessen(jid, status, meldung, quellen, ziel="", details=None, fehler_pfade=None):
    with sitzung() as s:
        job = s.get(Job, jid)
        job.status = status
        job.meldung = meldung[:4000]
        job.beendet = jetzt()
        job.ziel = ziel
        job.fehler_pfade = fehler_pfade or []
        if details:
            job.details = {**(job.details or {}), **details}
        for q in quellen:
            h = pfad_hash(str(q))
            d = s.get(Datei, h)
            if status == "fehler" or not q.exists():
                if d:
                    s.delete(d)        # damit „erneut verarbeiten“ die Datei wieder findet
                continue
            st = q.stat()
            s.merge(Datei(pfad_hash=h, pfad=str(q), groesse=st.st_size, mtime=st.st_mtime_ns,
                          profil_id=job.profil_id, status="erledigt", job_id=jid, gesehen=jetzt()))
        if ziel and Path(ziel).exists():
            st = Path(ziel).stat()
            s.merge(Datei(pfad_hash=pfad_hash(ziel), pfad=ziel, groesse=st.st_size, mtime=st.st_mtime_ns,
                          profil_id=job.profil_id, status="erzeugt", job_id=jid, gesehen=jetzt()))


def wiederholen(jid: int) -> str:
    """Fehlerhaften Auftrag erneut verarbeiten: Dateien zurück an den alten Ort legen."""
    with sitzung() as s:
        job = s.get(Job, jid)
        if not job or job.status != "fehler":
            return "Auftrag ist nicht im Fehlerzustand"
        zurueck = 0
        for f, q in zip(job.fehler_pfade or [], job.quellen):
            fp, qp = Path(f), Path(q)
            if fp.exists():
                qp.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(fp), str(verarb.frei(qp)))
                Path(str(fp) + ".fehler.txt").unlink(missing_ok=True)
                zurueck += 1
            s.execute(delete(Datei).where(Datei.pfad_hash == pfad_hash(q)))
        job.status = "wiederholt"
        job.meldung = (job.meldung or "") + f"\n→ erneut eingereiht ({datetime.now():%d.%m.%Y %H:%M})"
    return f"{zurueck} Datei(en) zurückgelegt – werden beim nächsten Durchlauf neu verarbeitet"


# ------------------------------------------------------------------ Aufräumen

class Hausmeister(threading.Thread):
    def __init__(self):
        super().__init__(name="hausmeister", daemon=True)

    def run(self):
        while not stopp.wait(3600):
            try:
                tage = int(einst.wert("job_aufbewahrung_tage") or 30)
                grenze = jetzt() - timedelta(days=tage)
                with sitzung() as s:
                    n = s.execute(delete(Job).where(Job.erstellt < grenze,
                                                    Job.status.notin_(["wartet", "laeuft"]))).rowcount
                    alte = s.scalars(select(Datei).where(Datei.gesehen < grenze)).all()
                    weg = [d for d in alte if not os.path.exists(d.pfad)]
                    for d in weg:
                        s.delete(d)
                if n or weg:
                    log.info("Aufgeräumt: %d alte Aufträge, %d Einträge verschwundener Dateien", n, len(weg))
                logging.getLogger().setLevel(einst.wert("log_level") or "INFO")
            except Exception:
                log.exception("Aufräumen fehlgeschlagen")


def vorbereiten() -> None:
    """Nach einem Neustart: unterbrochene Aufträge wieder einreihen."""
    with sitzung() as s:
        for job in s.scalars(select(Job).where(Job.status == "laeuft")):
            job.status = "wartet"
            job.meldung = "nach Neustart erneut eingereiht"


def starten() -> None:
    vorbereiten()
    Beobachter().start()
    for i in range(8):
        Arbeiter(i).start()
    Hausmeister().start()
