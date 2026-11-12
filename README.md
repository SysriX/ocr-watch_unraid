# ocr-watch

Überwacht Ordner mit **Profilen** und macht daraus durchsuchbare Dokumente: PDFs werden Seite für Seite geprüft und nur dort per OCR lesbar gemacht, wo Text fehlt oder Müll ist. Bilder werden zu PDF/A mit Texterkennung, HEIC-Fotos vom iPhone zu JPG, PNG oder WebP. Alles über eine Weboberfläche, optional mit Meldung an Home Assistant.

Weitere Unraid-Pakete: [haproxy-unraid](https://github.com/SysriX/haproxy_unraid), [haproxy-ui](https://github.com/SysriX/haproxy-ui_unraid), [certwarden-unraid](https://github.com/SysriX/certwarden_unraid)

`ghcr.io/sysrix/ocr-watch:latest`

## Was es kann

| Aktion (je Profil wählbar) | Was passiert |
| --- | --- |
| **PDF lesbar machen** | Jede Seite wird geprüft (`pdftotext`). Seiten ohne Text werden erkannt (`--skip-text`), Seiten mit unbrauchbarem Text neu erkannt (`--redo-ocr`). Schon lesbare PDFs bleiben unverändert. Wahlweise fest `skip-text`, `redo-ocr` oder `force-ocr`. Ergebnis als PDF/A, gedreht und begradigt. |
| **Bilder → PDF** | JPG, PNG, TIFF, BMP, WebP, GIF, HEIC (auswählbar) werden verlustfrei zu PDF und dann per OCR durchsuchbar – **je Bild**, **pro Ordner** (z. B. ein Ordner pro Vorgang) oder **nach Zeitfenster** (alle Bilder, die kurz nacheinander kommen). |
| **HEIC umwandeln** | HEIC/HEIF → JPG, PNG oder WebP. Qualität einstellbar, Bild richtig gedreht, EXIF (Datum, Kamera, GPS) auf Wunsch behalten. |

| Reiter | Inhalt |
| --- | --- |
| Übersicht | Warteschlange, heute verarbeitet/übersprungen, offene Fehler, Zustand jedes Profils, letzte Aufträge (aktualisiert sich selbst) |
| Profile | Profile anlegen, bearbeiten, pausieren – mit **Ordner-Browser** für Watch-, Ausgabe-, Archiv- und Fehler-Ordner |
| Aufträge | Verlauf mit Filter, Details (Prüfergebnis je Seite, OCR-Modus, Dauer), **erneut verarbeiten** |
| Einstellungen | Abfrage-Intervall, Wartezeit, gleichzeitige Aufträge, Sprachen, Aufbewahrung, MQTT, Passwort |

**Original und Fehler:** Originale wandern ins Archiv (`_archiv/`, gleiche Unterordner), werden gelöscht oder bleiben liegen. Geht etwas schief, liegt die Datei in `_fehler/` mit einer `.fehler.txt` daneben – „erneut“ in der Weboberfläche legt sie zurück.

**Netzfreigaben:** ocr-watch fragt die Ordner regelmäßig ab (Polling). Das funktioniert auch auf SMB/NFS-Freigaben (z. B. TrueNAS über Unassigned Devices), wo inotify nichts meldet. Eine Datei wird erst verarbeitet, wenn sie sich eine Weile nicht mehr ändert. Ist eine Freigabe weg, pausiert nur dieses Profil – mit **einer** Meldung im Log statt einer pro Minute.

## Installation in Unraid

1. Vorlage holen und in **Apps › Private** `ocr-watch` installieren:
   ```bash
   mkdir -p /boot/config/plugins/community.applications/private/sysrix && curl -fsSL https://raw.githubusercontent.com/SysriX/ocr-watch_unraid/main/templates/ocr-watch.xml -o /boot/config/plugins/community.applications/private/sysrix/ocr-watch.xml
   ```
2. Unter **Daten** den Bereich einbinden, in dem die Watch-Ordner liegen (Standard `/mnt/user`). Netzfreigaben aus Unassigned Devices über **Daten 2** (`/mnt/remotes`, Modus RW/Slave).
3. Aufruf: `http://<Unraid-IP>:8080/` – Passwort aus `UI_PASS` oder beim ersten Aufruf festlegen.
4. **Profile › Neues Profil**: Watch-Ordner wählen, Aktionen anhaken, speichern.

## Einstellungen im Template

Nur Start und Datenbank stehen im Template. Alles andere liegt in der Datenbank und wird in der Weboberfläche gepflegt.

| Feld | Variable / Pfad | Pflicht | Bedeutung |
| --- | --- | --- | --- |
| Weboberfläche | Port `8080` | ja | |
| Konfiguration | `/config` | ja | SQLite-Datenbank |
| Daten | `/data` | ja | Bereich mit den Watch-Ordnern (z. B. `/mnt/user`) |
| Daten 2 | `/data/remotes` | nein | Netzfreigaben (`/mnt/remotes`, RW/Slave) |
| Passwort | `UI_PASS` | nein | leer = in der Weboberfläche festlegen |
| Datenbank | `DB_TYPE` | ja | `sqlite` (Standard) oder `mariadb` |
| MariaDB | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASS` | bei mariadb | Datenbank und Benutzer vorher anlegen, Tabellen legt ocr-watch an |
| Besitzer neuer Dateien | `PUID`, `PGID`, `UMASK` | nein | Standard 99/100/0002 (nobody:users) |
| MQTT (Startwerte) | `MQTT_HOST`, `MQTT_USER`, `MQTT_PASS` | nein | nur beim ersten Start übernommen |

### MariaDB nutzen

```sql
CREATE DATABASE ocrwatch CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'ocrwatch'@'%' IDENTIFIED BY 'geheim';
GRANT ALL PRIVILEGES ON ocrwatch.* TO 'ocrwatch'@'%';
```

Startet MariaDB nach ocr-watch, wartet ocr-watch bis zu 2,5 Minuten. Updates der Datenbank-Struktur laufen beim Start automatisch (Alembic).

## Home Assistant (optional)

Unter **Einstellungen › Home Assistant** den MQTT-Broker eintragen. ocr-watch legt per MQTT-Discovery ein Gerät an:

- **Warteschlange**, **Heute verarbeitet**, **Heute Fehler**
- **Letzter Auftrag** (Attribute: Profil, Ziel, Zeit)
- **Problem** (binary_sensor: Fehler heute oder Ordner nicht erreichbar)

## OCR-Sprachen

Im Image enthalten: Deutsch, Englisch, Französisch, Italienisch, Spanisch, Niederländisch. Mehrere mit `+` verbinden, z. B. `deu+eng`.

## Sicherheit

- Anmeldung mit Passwort, nach 5 Fehlversuchen 5 Minuten Pause
- Ordner-Browser nur unterhalb von `/data`
- nur im internen Netz betreiben, nicht nach außen freigeben
- Logs nur auf stdout, im Template auf 3 × 10 MB begrenzt

## Updates

Neu gebaut, sobald das offizielle Python-Image aktualisiert wird; Unraid zeigt dann „Update verfügbar“.

## Hilfe

Fragen und Fehler bitte als [GitHub-Issue](https://github.com/SysriX/ocr-watch_unraid/issues).

Lizenz: MIT – siehe [LICENSE](LICENSE). Enthält [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) (MPL-2.0), Tesseract (Apache-2.0), Ghostscript (AGPL) und weitere Pakete unter eigenen Lizenzen.
