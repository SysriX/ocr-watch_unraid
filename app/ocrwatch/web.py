"""Weboberfläche (FastAPI + Jinja2)."""
import hashlib
import hmac
import os
import secrets
import time
from datetime import timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, config, dienst
from . import einstellungen as einst
from .db import Job, Profil, sitzung
from .protokoll import log
from .verarbeitung import sprachen_normal
from .statistik import kennzahlen

HIER = Path(__file__).parent
vorlagen = Jinja2Templates(directory=str(HIER / "templates"))

OEFFENTLICH = ("/anmelden", "/einrichten", "/gesund", "/static/")
STATUS_TEXT = {"wartet": "wartet", "laeuft": "läuft", "fertig": "fertig", "uebersprungen": "übersprungen",
               "fehler": "Fehler", "wiederholt": "wiederholt"}
ART_TEXT = {"pdf": "PDF lesbar machen", "bilder": "Bilder → PDF", "heic": "HEIC umwandeln"}


def _zeit(dt) -> str:
    if not dt:
        return "–"
    return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S")


vorlagen.env.filters["zeit"] = _zeit
vorlagen.env.filters["name"] = lambda p: Path(p).name if p else ""
vorlagen.env.globals.update(version=__version__, STATUS_TEXT=STATUS_TEXT, ART_TEXT=ART_TEXT,
                            BILD_TYPEN=einst.BILD_TYPEN)


# ------------------------------------------------------------------ Passwort

def _hash(pw: str, salz: str | None = None) -> str:
    salz = salz or secrets.token_hex(16)
    h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salz), n=2 ** 14, r=8, p=1).hex()
    return f"{salz}${h}"


def _pruefen(pw: str) -> bool:
    if config.UI_PASS:
        return hmac.compare_digest(pw.encode(), config.UI_PASS.encode())
    gespeichert = einst.wert("ui_pass_hash")
    if not gespeichert:
        return False
    salz, _ = gespeichert.split("$", 1)
    return hmac.compare_digest(_hash(pw, salz), gespeichert)


def _passwort_fehlt() -> bool:
    return not config.UI_PASS and not einst.wert("ui_pass_hash")


_fehlversuche: dict[str, list[float]] = {}


def _gesperrt(ip: str) -> bool:
    v = [t for t in _fehlversuche.get(ip, []) if time.time() - t < 300]
    _fehlversuche[ip] = v
    return len(v) >= 5


# ------------------------------------------------------------------ Hilfen

def _redirect(url: str, meldung: str = "", art: str = "ok", request: Request | None = None):
    if request is not None and meldung:
        request.session["meldung"] = [art, meldung]
    return RedirectResponse(url, status_code=303)


def _seite(request: Request, vorlage: str, **ctx):
    ctx["meldung"] = request.session.pop("meldung", None)
    ctx["request"] = request
    return vorlagen.TemplateResponse(request, vorlage, ctx)


def _im_datenbereich(pfad: str) -> Path | None:
    try:
        p = Path(pfad).resolve()
        p.relative_to(config.DATA_ROOT.resolve())
        return p
    except (ValueError, OSError):
        return None


def _typ_wie(vorbild, roh):
    if isinstance(vorbild, bool):
        return roh is not None
    if roh is None:
        return vorbild
    if isinstance(vorbild, int):
        try:
            return int(str(roh).strip())
        except ValueError:
            return vorbild
    return str(roh).strip()


def _profil_aus_formular(form) -> dict:
    opt = {}
    for k, v in einst.PROFIL_STANDARD.items():
        if isinstance(v, dict):
            opt[k] = {}
            for uk, uv in v.items():
                feld = f"{k}.{uk}"
                if isinstance(uv, list):
                    opt[k][uk] = [x for x in form.getlist(feld) if x in einst.BILD_TYPEN]
                else:
                    opt[k][uk] = _typ_wie(uv, form.get(feld))
        else:
            opt[k] = _typ_wie(v, form.get(k))
    opt["sprachen"] = sprachen_normal(opt["sprachen"])
    return opt


def _profil_pruefen(name: str, pfad: str, opt: dict, pid: int | None) -> str | None:
    if not name:
        return "Bitte einen Namen eingeben."
    if not pfad or not Path(pfad).is_absolute():
        return "Bitte einen Watch-Ordner wählen."
    if not Path(pfad).is_dir():
        return f"Ordner {pfad} gibt es nicht (oder er ist nicht eingebunden)."
    if opt["ausgabe"] == "ordner" and not opt["ausgabe_ordner"]:
        return "Bei „in eigenen Ordner“ bitte einen Ausgabe-Ordner wählen."
    if opt["ausgabe"] == "ordner" and Path(opt["ausgabe_ordner"]) == Path(pfad):
        return "Ausgabe-Ordner darf nicht der Watch-Ordner sein."
    if not (opt["pdf"]["aktiv"] or opt["bilder"]["aktiv"] or opt["heic"]["aktiv"]):
        return "Bitte mindestens eine Aktion einschalten."
    if opt["bilder"]["aktiv"] and not opt["bilder"]["typen"]:
        return "Bitte mindestens einen Bildtyp wählen."
    unbekannt = _unbekannte_sprachen(opt["sprachen"])
    if unbekannt:
        return f"Sprache nicht installiert: {', '.join(unbekannt)} (vorhanden: {', '.join(_tesseract_sprachen())})"
    if opt["pdf"]["modus"] not in ("auto", "skip-text", "redo-ocr", "force-ocr"):
        return "Unbekannter PDF-Modus."
    with sitzung() as s:
        q = select(Profil).where(Profil.name == name)
        andere = s.scalars(q).first()
        if andere and andere.id != pid:
            return f"Ein Profil „{name}“ gibt es schon."
    return None


# ------------------------------------------------------------------ App

def erstellen() -> FastAPI:
    app = FastAPI(title="ocr-watch", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(HIER / "static")), name="static")

    @app.middleware("http")
    async def anmeldung(request: Request, call_next):
        pfad = request.url.path
        if not pfad.startswith(OEFFENTLICH):
            if _passwort_fehlt():
                return RedirectResponse("/einrichten", status_code=303)
            if not request.session.get("ok"):
                if pfad.startswith("/api/"):
                    return JSONResponse({"fehler": "nicht angemeldet"}, status_code=401)
                return RedirectResponse("/anmelden", status_code=303)
        return await call_next(request)

    # Reihenfolge: SessionMiddleware muss außen liegen (zuletzt hinzugefügt)
    app.add_middleware(SessionMiddleware, secret_key=einst.wert("geheimnis"), session_cookie="ocrwatch",
                       max_age=7 * 24 * 3600, same_site="lax", https_only=False)

    @app.get("/gesund", response_class=PlainTextResponse)
    def gesund():
        return "ok"

    # ---------------------------------------------------------- Anmeldung
    @app.get("/einrichten", response_class=HTMLResponse)
    def einrichten_form(request: Request):
        if not _passwort_fehlt():
            return _redirect("/")
        return _seite(request, "einrichten.html")

    @app.post("/einrichten")
    async def einrichten(request: Request):
        if not _passwort_fehlt():
            return _redirect("/")
        f = await request.form()
        pw, pw2 = f.get("pw", ""), f.get("pw2", "")
        if len(pw) < 8 or pw != pw2:
            return _seite(request, "einrichten.html", fehler="Mindestens 8 Zeichen, beide Felder gleich.")
        einst.speichern({"ui_pass_hash": _hash(pw)})
        request.session["ok"] = True
        log.info("Passwort für die Weboberfläche festgelegt")
        return _redirect("/", "Passwort gespeichert. Jetzt das erste Profil anlegen.", request=request)

    @app.get("/anmelden", response_class=HTMLResponse)
    def anmelden_form(request: Request):
        if _passwort_fehlt():
            return _redirect("/einrichten")
        return _seite(request, "anmelden.html")

    @app.post("/anmelden")
    async def anmelden(request: Request):
        ip = request.client.host if request.client else "?"
        if _gesperrt(ip):
            return _seite(request, "anmelden.html", fehler="Zu viele Fehlversuche – bitte 5 Minuten warten.")
        f = await request.form()
        if _pruefen(f.get("pw", "")):
            request.session["ok"] = True
            _fehlversuche.pop(ip, None)
            return _redirect("/")
        _fehlversuche.setdefault(ip, []).append(time.time())
        log.warning("Fehlgeschlagene Anmeldung von %s", ip)
        return _seite(request, "anmelden.html", fehler="Passwort falsch.")

    @app.post("/abmelden")
    def abmelden(request: Request):
        request.session.clear()
        return _redirect("/anmelden")

    # ---------------------------------------------------------- Übersicht
    @app.get("/", response_class=HTMLResponse)
    def uebersicht(request: Request):
        with sitzung() as s:
            profile = s.scalars(select(Profil).order_by(Profil.name)).all()
            jobs = s.scalars(select(Job).order_by(Job.id.desc()).limit(15)).all()
        return _seite(request, "uebersicht.html", k=kennzahlen(), profile=profile, jobs=jobs,
                      zustand=dienst.zustand["profile"], db=config.database_label(),
                      opt=einst.profil_optionen)

    @app.get("/api/status")
    def api_status():
        k = kennzahlen()
        k["profile"] = {str(i): z for i, z in dienst.zustand["profile"].items()}
        k["letzter_lauf"] = dienst.zustand.get("letzter_lauf")
        return JSONResponse(jsonable_encoder(k), headers={"Cache-Control": "no-store"})

    # ---------------------------------------------------------- Ordner-Browser
    @app.get("/api/ordner")
    def api_ordner(pfad: str = ""):
        basis = config.DATA_ROOT.resolve()
        p = _im_datenbereich(pfad or str(basis)) or basis
        try:
            unter = sorted((e.name for e in os.scandir(p)
                            if e.is_dir() and not e.name.startswith(".")), key=str.lower)
        except OSError as e:
            return JSONResponse({"pfad": str(p), "fehler": str(e), "ordner": [], "oben": None})
        oben = str(p.parent) if p != basis else None
        return {"pfad": str(p), "ordner": unter[:500], "oben": oben, "basis": str(basis)}

    @app.post("/api/ordner/neu")
    async def api_ordner_neu(request: Request):
        f = await request.json()
        eltern = _im_datenbereich(f.get("pfad", ""))
        name = (f.get("name") or "").strip()
        if not eltern or not name or "/" in name or name in (".", ".."):
            return JSONResponse({"fehler": "ungültiger Name"}, status_code=400)
        (eltern / name).mkdir(exist_ok=True)
        return {"pfad": str(eltern / name)}

    # ---------------------------------------------------------- Profile
    @app.get("/profile", response_class=HTMLResponse)
    def profile_liste(request: Request):
        with sitzung() as s:
            profile = s.scalars(select(Profil).order_by(Profil.name)).all()
            zahlen = dict(s.execute(select(Job.profil_id, func.count()).group_by(Job.profil_id)).all())
        return _seite(request, "profile.html", profile=profile, zahlen=zahlen,
                      zustand=dienst.zustand["profile"], opt=einst.profil_optionen)

    @app.get("/profile/neu", response_class=HTMLResponse)
    def profil_neu(request: Request):
        opt = einst.profil_optionen({})
        return _seite(request, "profil.html", p=None, opt=opt, name="", pfad="", aktiv=True,
                      standard_sprachen=einst.wert("sprachen"), basis=str(config.DATA_ROOT))

    @app.get("/profile/{pid}", response_class=HTMLResponse)
    def profil_bearbeiten(request: Request, pid: int):
        with sitzung() as s:
            p = s.get(Profil, pid)
        if not p:
            return _redirect("/profile", "Profil nicht gefunden", "fehler", request)
        return _seite(request, "profil.html", p=p, opt=einst.profil_optionen(p.optionen), name=p.name,
                      pfad=p.pfad, aktiv=p.aktiv, standard_sprachen=einst.wert("sprachen"),
                      basis=str(config.DATA_ROOT))

    @app.post("/profile/speichern")
    async def profil_speichern(request: Request):
        f = await request.form()
        pid = int(f["id"]) if f.get("id") else None
        name = (f.get("name") or "").strip()
        pfad = (f.get("pfad") or "").strip().rstrip("/") or ""
        aktiv = f.get("aktiv") is not None
        opt = _profil_aus_formular(f)
        fehler = _profil_pruefen(name, pfad, opt, pid)
        if fehler:
            return _seite(request, "profil.html", p={"id": pid} if pid else None, opt=opt, name=name, pfad=pfad,
                          aktiv=aktiv, fehler=fehler, standard_sprachen=einst.wert("sprachen"),
                          basis=str(config.DATA_ROOT))
        with sitzung() as s:
            if pid:
                p = s.get(Profil, pid)
                alt = p.optionen or {}
                if p.pfad == pfad and alt.get("_erstscan_erledigt"):
                    opt["_erstscan_erledigt"] = True    # Ordner gleich -> Erstdurchlauf nicht wiederholen
                p.name, p.pfad, p.aktiv, p.optionen = name, pfad, aktiv, opt
            else:
                s.add(Profil(name=name, pfad=pfad, aktiv=aktiv, optionen=opt))
        log.info("Profil „%s“ gespeichert (%s)", name, pfad)
        return _redirect("/profile", f"Profil „{name}“ gespeichert.", request=request)

    @app.post("/profile/{pid}/umschalten")
    def profil_umschalten(request: Request, pid: int):
        txt = ""
        with sitzung() as s:
            p = s.get(Profil, pid)
            if p:
                p.aktiv = not p.aktiv
                txt = f"Profil „{p.name}“ {'aktiviert' if p.aktiv else 'pausiert'}."
        return _redirect("/profile", txt, request=request)

    @app.post("/profile/{pid}/loeschen")
    def profil_loeschen(request: Request, pid: int):
        from sqlalchemy import delete
        from .db import Datei
        with sitzung() as s:
            p = s.get(Profil, pid)
            if not p:
                return _redirect("/profile")
            name = p.name
            s.execute(delete(Datei).where(Datei.profil_id == pid))
            s.delete(p)
        log.info("Profil „%s“ gelöscht", name)
        return _redirect("/profile", f"Profil „{name}“ gelöscht. Dateien im Ordner bleiben unverändert.",
                         request=request)

    # ---------------------------------------------------------- Aufträge
    @app.get("/auftraege", response_class=HTMLResponse)
    def auftraege(request: Request, status: str = "", profil: str = "", seite: int = 1):
        pro_seite = 50
        with sitzung() as s:
            q = select(Job)
            if status:
                q = q.where(Job.status == status)
            if profil:
                q = q.where(Job.profil_name == profil)
            gesamt = s.scalar(select(func.count()).select_from(q.subquery()))
            jobs = s.scalars(q.order_by(Job.id.desc()).offset((seite - 1) * pro_seite).limit(pro_seite)).all()
            namen = [n for (n,) in s.execute(select(Job.profil_name).distinct().order_by(Job.profil_name))]
        return _seite(request, "auftraege.html", jobs=jobs, status=status, profil=profil, seite=seite,
                      seiten=max(1, (gesamt + pro_seite - 1) // pro_seite), gesamt=gesamt, namen=namen)

    @app.post("/auftraege/{jid}/wiederholen")
    def auftrag_wiederholen(request: Request, jid: int):
        txt = dienst.wiederholen(jid)
        return _redirect(request.headers.get("referer") or "/auftraege", f"Auftrag #{jid}: {txt}", request=request)

    @app.post("/auftraege/fehler-wiederholen")
    def alle_wiederholen(request: Request):
        with sitzung() as s:
            ids = [j for (j,) in s.execute(select(Job.id).where(Job.status == "fehler"))]
        for jid in ids:
            dienst.wiederholen(jid)
        return _redirect("/auftraege?status=fehler", f"{len(ids)} Aufträge erneut eingereiht.", request=request)

    # ---------------------------------------------------------- Einstellungen
    @app.get("/einstellungen", response_class=HTMLResponse)
    def einstellungen_form(request: Request):
        return _seite(request, "einstellungen.html", e=einst.alle(), db=config.database_label(),
                      pass_aus_env=bool(config.UI_PASS), sprachen=_tesseract_sprachen())

    @app.post("/einstellungen")
    async def einstellungen_speichern(request: Request):
        f = await request.form()
        neu = {k: _typ_wie(v, f.get(k)) for k, v in einst.STANDARD.items()}
        if not f.get("mqtt_pass"):
            neu["mqtt_pass"] = einst.wert("mqtt_pass")      # leer = unverändert
        neu["sprachen"] = sprachen_normal(neu["sprachen"]) or "deu+eng"
        if _unbekannte_sprachen(neu["sprachen"]):
            return _redirect("/einstellungen", "Sprache nicht installiert: "
                             + ", ".join(_unbekannte_sprachen(neu["sprachen"])), "fehler", request)
        neu["parallel"] = max(1, min(8, neu["parallel"]))
        neu["abfrage_intervall"] = max(2, neu["abfrage_intervall"])
        if neu["log_level"] not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            neu["log_level"] = "INFO"
        einst.speichern(neu)
        import logging
        logging.getLogger().setLevel(neu["log_level"])
        if f.get("pw_neu"):
            if config.UI_PASS:
                return _redirect("/einstellungen", "Passwort kommt aus UI_PASS im Template – dort ändern.",
                                 "fehler", request)
            if len(f["pw_neu"]) < 8:
                return _redirect("/einstellungen", "Passwort zu kurz (min. 8 Zeichen).", "fehler", request)
            einst.speichern({"ui_pass_hash": _hash(f["pw_neu"])})
        if getattr(app.state, "melder", None):
            app.state.melder.wecken.set()
        return _redirect("/einstellungen", "Einstellungen gespeichert.", request=request)

    return app


def _unbekannte_sprachen(sprachen: str) -> list[str]:
    vorhanden = _tesseract_sprachen()
    if not vorhanden or not sprachen:
        return []
    return [s for s in sprachen.split("+") if s not in vorhanden]


def _tesseract_sprachen() -> list[str]:
    import subprocess
    try:
        r = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10)
        return [z.strip() for z in r.stdout.splitlines()[1:] if z.strip() and z.strip() != "osd"]
    except Exception:
        return []
