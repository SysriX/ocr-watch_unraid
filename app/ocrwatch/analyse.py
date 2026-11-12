"""Prüft, wie gut ein PDF schon lesbar ist – Seite für Seite."""
import subprocess
from dataclasses import dataclass, field

import pikepdf


@dataclass
class Befund:
    seiten: int = 0
    ohne_text: list[int] = field(default_factory=list)     # Seitennummern (1-basiert)
    schlechter_text: list[int] = field(default_factory=list)
    signiert: bool = False
    verschluesselt: bool = False

    @property
    def lesbar(self) -> bool:
        return not self.ohne_text and not self.schlechter_text

    def als_dict(self) -> dict:
        return {"seiten": self.seiten, "ohne_text": self.ohne_text,
                "schlechter_text": self.schlechter_text, "signiert": self.signiert}


def qualitaet(text: str) -> tuple[int, int]:
    """(Anzahl Zeichen ohne Leerraum, Anteil Buchstaben/Ziffern in %)."""
    zeichen = [c for c in text if not c.isspace()]
    if not zeichen:
        return 0, 0
    gut = sum(1 for c in zeichen if c.isalnum() or c in ".,;:-–()/%€$&@'\"!?§+")
    return len(zeichen), round(100 * gut / len(zeichen))


def pdf_pruefen(pfad: str, min_zeichen: int, min_qualitaet: int, timeout: int = 120) -> Befund:
    b = Befund()
    try:
        with pikepdf.open(pfad) as pdf:
            b.seiten = len(pdf.pages)
            acro = pdf.Root.get("/AcroForm")
            if acro is not None and acro.get("/SigFlags") is not None:
                b.signiert = int(acro.get("/SigFlags")) & 1 == 1
    except pikepdf.PasswordError:
        b.verschluesselt = True
        return b

    roh = subprocess.run(["pdftotext", "-enc", "UTF-8", "-layout", pfad, "-"],
                         capture_output=True, timeout=timeout).stdout.decode("utf-8", "replace")
    seiten_text = roh.split("\f")
    for nr in range(1, b.seiten + 1):
        text = seiten_text[nr - 1] if nr - 1 < len(seiten_text) else ""
        anzahl, q = qualitaet(text)
        if anzahl < min_zeichen:
            b.ohne_text.append(nr)
        elif q < min_qualitaet:
            b.schlechter_text.append(nr)
    return b


def modus_waehlen(befund: Befund, modus: str) -> tuple[str | None, str]:
    """Liefert (ocrmypdf-Modus oder None = nichts zu tun, Begründung)."""
    if modus != "auto":
        return modus, f"Modus {modus} fest eingestellt"
    if befund.lesbar:
        return None, f"bereits lesbar ({befund.seiten} Seiten mit Text)"
    if befund.schlechter_text:
        return "redo-ocr", f"schlechter Text auf Seite {_liste(befund.schlechter_text)} – OCR wird erneuert"
    return "skip-text", f"kein Text auf Seite {_liste(befund.ohne_text)} – nur diese Seiten werden erkannt"


def _liste(nr: list[int]) -> str:
    return ", ".join(map(str, nr[:10])) + (" …" if len(nr) > 10 else "")
