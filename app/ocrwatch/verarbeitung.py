"""Die eigentliche Arbeit: PDF lesbar machen, Bilder zu PDF, HEIC umwandeln."""
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import img2pdf
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from . import analyse
from . import einstellungen as einst
from .protokoll import log

register_heif_opener()
Image.MAX_IMAGE_PIXELS = 300_000_000


class Uebersprungen(Exception):
    """Datei bewusst nicht verändert (z. B. schon lesbar, digital signiert)."""


class Ordner:
    """Pfade eines Profils."""

    def __init__(self, pfad: str, opt: dict):
        self.wurzel = Path(pfad)
        self.opt = opt
        self.archiv = Path(opt["archiv_ordner"]) if opt.get("archiv_ordner") else self.wurzel / "_archiv"
        self.fehler = Path(opt["fehler_ordner"]) if opt.get("fehler_ordner") else self.wurzel / "_fehler"
        self.ausgabe = (Path(opt["ausgabe_ordner"])
                        if opt.get("ausgabe") == "ordner" and opt.get("ausgabe_ordner") else None)

    def sonderordner(self) -> list[Path]:
        return [p for p in (self.archiv, self.fehler, self.ausgabe) if p]

    def relativ(self, datei: Path) -> Path:
        try:
            return datei.relative_to(self.wurzel)
        except ValueError:
            return Path(datei.name)

    def ziel_fuer(self, quelle: Path, name: str | None = None) -> Path:
        """Wohin das Ergebnis zu einer Quelldatei kommt."""
        name = name or quelle.name
        if self.ausgabe:
            return self.ausgabe / self.relativ(quelle).parent / name
        return quelle.parent / name


def frei(pfad: Path) -> Path:
    """Freien Dateinamen finden: name.pdf, name (1).pdf, ..."""
    if not pfad.exists():
        return pfad
    for i in range(1, 10000):
        p = pfad.with_name(f"{pfad.stem} ({i}){pfad.suffix}")
        if not p.exists():
            return p
    raise RuntimeError(f"kein freier Name für {pfad}")


def ablegen(tmp: Path, ziel: Path) -> Path:
    """Ergebnis sicher ablegen: erst als versteckte Teildatei kopieren, dann umbenennen."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    teil = ziel.with_name(f".ocrwatch-{uuid.uuid4().hex[:8]}.part")
    shutil.copyfile(tmp, teil)
    os.replace(teil, ziel)
    return ziel


def original_behandeln(quelle: Path, art: str, ordner: Ordner) -> str:
    """archivieren | loeschen | behalten. Gibt den neuen Ort zurück (oder '')."""
    if art == "loeschen":
        quelle.unlink(missing_ok=True)
        return ""
    if art == "archivieren":
        ziel = frei(ordner.archiv / ordner.relativ(quelle))
        ziel.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(quelle), str(ziel))
        return str(ziel)
    return str(quelle)


def in_fehlerordner(quellen: list[Path], ordner: Ordner, grund: str) -> list[str]:
    erg = []
    for q in quellen:
        if not q.exists():
            continue
        ziel = frei(ordner.fehler / ordner.relativ(q))
        ziel.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(q), str(ziel))
        ziel.with_name(ziel.name + ".fehler.txt").write_text(
            f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{q}\n\n{grund}\n", encoding="utf-8")
        erg.append(str(ziel))
    return erg


# ------------------------------------------------------------------ ocrmypdf

def ocrmypdf(eingabe: Path, ausgabe: Path, modus: str | None, opt: dict, sprachen: str) -> str:
    pdf = opt["pdf"]
    cmd = ["ocrmypdf", "-l", sprachen,
           "--output-type", "pdfa" if pdf.get("pdfa", True) else "pdf",
           "--optimize", str(int(pdf.get("optimieren", 1))),
           "--jobs", str(int(einst.wert("ocr_threads") or 2))]
    if pdf.get("drehen", True):
        cmd.append("--rotate-pages")
    if pdf.get("begradigen", True) and modus != "redo-ocr":
        cmd.append("--deskew")
    if modus:
        cmd.append(f"--{modus}")
    cmd += [str(eingabe), str(ausgabe)]
    timeout = int(einst.wert("timeout_minuten") or 30) * 60
    log.debug("Starte: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    meldung = (r.stderr or "").strip()
    if r.returncode == 0:
        return ""
    if r.returncode == 10 and ausgabe.exists():   # PDF/A nicht ganz gültig, PDF aber in Ordnung
        return "PDF/A-Prüfung nicht bestanden, Ergebnis ist normales PDF"
    if r.returncode == 6:
        raise Uebersprungen("enthält schon Text (PriorOcrFound)")
    if r.returncode == 8:
        raise Uebersprungen("verschlüsselt – nicht verändert")
    if "DigitalSignature" in meldung or "digital signature" in meldung.lower():
        raise Uebersprungen("digital signiert – nicht verändert")
    letzte = "\n".join(meldung.splitlines()[-8:])
    raise RuntimeError(f"ocrmypdf Fehler {r.returncode}: {letzte}")


def sprachen_normal(text: str) -> str:
    """'deu eng', 'deu,eng', 'deu + eng' -> 'deu+eng'."""
    return "+".join(t for t in re.split(r"[\s,;+]+", text or "") if t)


def sprachen_fuer(opt: dict) -> str:
    return sprachen_normal(opt.get("sprachen") or einst.wert("sprachen")) or "deu+eng"


# ------------------------------------------------------------------ Aktionen

def pdf_verarbeiten(quelle: Path, ordner: Ordner) -> dict:
    opt = ordner.opt
    p = opt["pdf"]
    befund = analyse.pdf_pruefen(str(quelle), int(p["min_zeichen"]), int(p["min_qualitaet"]))
    if befund.verschluesselt:
        raise Uebersprungen("verschlüsselt – nicht verändert")
    if befund.signiert:
        raise Uebersprungen("digital signiert – nicht verändert")
    modus, grund = analyse.modus_waehlen(befund, p["modus"])
    details = {"befund": befund.als_dict(), "grund": grund}
    if modus is None:
        raise Uebersprungen(grund)

    with tempfile.TemporaryDirectory(prefix="ocrwatch-") as tmp:
        aus = Path(tmp) / "ergebnis.pdf"
        try:
            hinweis = ocrmypdf(quelle, aus, modus, opt, sprachen_fuer(opt))
        except RuntimeError as e:
            if modus != "redo-ocr":
                raise
            log.info("redo-ocr ging nicht (%s) – versuche force-ocr", str(e).splitlines()[0])
            hinweis = ocrmypdf(quelle, aus, "force-ocr", opt, sprachen_fuer(opt))
            modus = "force-ocr"
        details.update(modus=modus, hinweis=hinweis)
        ziel = _ziel_mit_original(quelle, aus, ordner, opt["original"])
    details["ziel"] = str(ziel)
    return details


def _ziel_mit_original(quelle: Path, tmp: Path, ordner: Ordner, original: str) -> Path:
    """Ergebnis ablegen und das Original nach Einstellung behandeln."""
    if ordner.ausgabe is None and original == "behalten":
        ziel = frei(quelle.with_name(f"{quelle.stem}_ocr.pdf"))   # Original bleibt unverändert daneben
        return ablegen(tmp, ziel)
    if ordner.ausgabe is None:
        ziel = quelle.with_suffix(".pdf")
        if original == "loeschen" and ziel == quelle:
            return ablegen(tmp, ziel)                   # ersetzt das Original in einem Schritt
        original_behandeln(quelle, original, ordner)    # Original ins Archiv …
        return ablegen(tmp, frei(ziel))                 # … dann Ergebnis an seine Stelle
    ziel = frei(ordner.ziel_fuer(quelle, quelle.stem + ".pdf"))
    ablegen(tmp, ziel)
    original_behandeln(quelle, original, ordner)
    return ziel


def _bild_fuer_pdf(quelle: Path, tmp: Path) -> list[str]:
    """Bild so vorbereiten, dass img2pdf es verlustfrei übernehmen kann."""
    endung = quelle.suffix.lower()
    if endung in (".jpg", ".jpeg"):
        return [str(quelle)]                    # unverändert (Drehung über EXIF)
    erg = []
    with Image.open(quelle) as img:
        frames = getattr(img, "n_frames", 1)
        for i in range(frames if endung in (".tif", ".tiff") else 1):
            img.seek(i)
            bild = ImageOps.exif_transpose(img)
            if bild.mode not in ("RGB", "L", "1"):
                bild = bild.convert("RGBA").convert("RGB") if "A" in bild.mode or bild.mode == "P" else bild.convert("RGB")
            if endung in (".heic", ".heif", ".webp"):
                ziel = tmp / f"{uuid.uuid4().hex}.jpg"
                bild.save(ziel, "JPEG", quality=92)
            else:
                ziel = tmp / f"{uuid.uuid4().hex}.png"
                bild.save(ziel, "PNG")
            erg.append(str(ziel))
    return erg


def bilder_verarbeiten(quellen: list[Path], ordner: Ordner, zielname: str) -> dict:
    opt = ordner.opt
    quellen = sorted(quellen, key=lambda p: (str(p.parent), p.name.lower()))
    with tempfile.TemporaryDirectory(prefix="ocrwatch-") as tmpd:
        tmp = Path(tmpd)
        seiten = []
        for q in quellen:
            seiten += _bild_fuer_pdf(q, tmp)
        roh = tmp / "bilder.pdf"
        roh.write_bytes(img2pdf.convert(seiten, rotation=img2pdf.Rotation.ifvalid))
        aus = tmp / "ergebnis.pdf"
        hinweis = ocrmypdf(roh, aus, None, opt, sprachen_fuer(opt))
        ziel = frei(ordner.ziel_fuer(quellen[0], zielname))
        ablegen(aus, ziel)
    for q in quellen:
        original_behandeln(q, opt["original"], ordner)
    return {"ziel": str(ziel), "seiten": len(seiten), "bilder": len(quellen), "hinweis": hinweis}


def heic_verarbeiten(quelle: Path, ordner: Ordner) -> dict:
    h = ordner.opt["heic"]
    ziel_art = h.get("ziel", "jpg").lower()
    endung = {"jpg": ".jpg", "jpeg": ".jpg", "png": ".png", "webp": ".webp"}.get(ziel_art)
    if not endung:
        raise RuntimeError(f"Zielformat '{ziel_art}' unbekannt")
    qualitaet = max(1, min(100, int(h.get("qualitaet", 90))))
    with Image.open(quelle) as img:
        exif = img.getexif()
        icc = img.info.get("icc_profile")
        bild = ImageOps.exif_transpose(img)
        if exif:
            exif[0x0112] = 1        # Bild ist jetzt richtig gedreht
        args = {}
        if h.get("exif_behalten", True) and exif:
            args["exif"] = exif.tobytes()
        if icc:
            args["icc_profile"] = icc
        with tempfile.TemporaryDirectory(prefix="ocrwatch-") as tmpd:
            tmp = Path(tmpd) / ("bild" + endung)
            if endung == ".jpg":
                bild.convert("RGB").save(tmp, "JPEG", quality=qualitaet, optimize=True, **args)
            elif endung == ".webp":
                bild.save(tmp, "WEBP", quality=qualitaet, method=4, **args)
            else:
                bild.save(tmp, "PNG", optimize=True, **args)
            ziel = frei(ordner.ziel_fuer(quelle, quelle.stem + endung))
            ablegen(tmp, ziel)
    original_behandeln(quelle, h.get("original", "archivieren"), ordner)
    return {"ziel": str(ziel), "format": ziel_art, "qualitaet": qualitaet}
