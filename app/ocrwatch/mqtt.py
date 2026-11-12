"""Optional: Zustand per MQTT-Discovery an Home Assistant melden."""
import json
import re
import threading

from . import __version__
from . import einstellungen as einst
from .protokoll import drossel, log
from .statistik import kennzahlen

try:
    import paho.mqtt.client as paho
except ImportError:          # pragma: no cover
    paho = None


def _id(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "ocr_watch"


class Melder(threading.Thread):
    def __init__(self, stopp: threading.Event):
        super().__init__(name="mqtt", daemon=True)
        self.stopp = stopp
        self.client = None
        self.verbunden_mit = None
        self.wecken = threading.Event()

    def run(self):
        while not self.stopp.is_set():
            e = einst.alle()
            try:
                if e.get("mqtt_aktiv") and e.get("mqtt_host"):
                    self.verbinden(e)
                    self.senden(e)
                else:
                    self.trennen()
            except Exception as ex:
                drossel.melden("mqtt", "fehler", f"MQTT: {ex}")
                self.trennen()
            self.wecken.wait(max(10, int(e.get("mqtt_intervall") or 60)))
            self.wecken.clear()

    def verbinden(self, e):
        ziel = (e["mqtt_host"], int(e.get("mqtt_port") or 1883), e.get("mqtt_user"), e.get("mqtt_pass"),
                e.get("ha_geraet"), e.get("mqtt_prefix"))
        if self.client and self.verbunden_mit == ziel and self.client.is_connected():
            return
        self.trennen()
        geraet = _id(e.get("ha_geraet") or "ocr-watch")
        c = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=f"ocr-watch-{geraet}")
        if e.get("mqtt_user"):
            c.username_pw_set(e["mqtt_user"], e.get("mqtt_pass") or None)
        c.will_set(f"ocr-watch/{geraet}/verfuegbar", "offline", retain=True)
        c.connect(ziel[0], ziel[1], keepalive=60)
        c.loop_start()
        self.client, self.verbunden_mit = c, ziel
        self.discovery(e, geraet)
        c.publish(f"ocr-watch/{geraet}/verfuegbar", "online", retain=True)
        drossel.ok("mqtt", f"MQTT verbunden mit {ziel[0]}:{ziel[1]}")
        log.info("MQTT verbunden mit %s:%s", ziel[0], ziel[1])

    def trennen(self):
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.client = self.verbunden_mit = None

    def discovery(self, e, geraet):
        prefix = e.get("mqtt_prefix") or "homeassistant"
        basis = f"ocr-watch/{geraet}"
        dev = {"identifiers": [f"ocr_watch_{geraet}"], "name": e.get("ha_geraet") or "ocr-watch",
               "manufacturer": "SysriX", "model": "ocr-watch", "sw_version": __version__}
        sensoren = [
            ("sensor", "warteschlange", "Warteschlange", {"icon": "mdi:tray-full", "unit_of_measurement": "Dateien",
                                                          "state_class": "measurement"}),
            ("sensor", "heute_fertig", "Heute verarbeitet", {"icon": "mdi:file-check", "state_class": "total"}),
            ("sensor", "heute_fehler", "Heute Fehler", {"icon": "mdi:file-alert", "state_class": "total"}),
            ("sensor", "letzter_auftrag", "Letzter Auftrag", {"icon": "mdi:file-document",
                                                              "json_attributes_topic": f"{basis}/letzter"}),
            ("binary_sensor", "problem", "Problem", {"device_class": "problem"}),
        ]
        for komp, sid, name, extra in sensoren:
            cfg = {"name": name, "unique_id": f"ocr_watch_{geraet}_{sid}", "object_id": f"{geraet}_{sid}",
                   "state_topic": f"{basis}/zustand", "value_template": "{{ value_json.%s }}" % sid,
                   "availability_topic": f"{basis}/verfuegbar", "device": dev, **extra}
            if komp == "binary_sensor":
                cfg.update(payload_on="ja", payload_off="nein")
            self.client.publish(f"{prefix}/{komp}/ocr_watch_{geraet}/{sid}/config", json.dumps(cfg), retain=True)

    def senden(self, e):
        k = kennzahlen()
        geraet = _id(e.get("ha_geraet") or "ocr-watch")
        letzter = k.get("letzter") or {}
        daten = {
            "warteschlange": k["wartet"] + k["laeuft"],
            "heute_fertig": k["heute_fertig"],
            "heute_fehler": k["heute_fehler"],
            "letzter_auftrag": letzter.get("datei", "–"),
            "problem": "ja" if (k["heute_fehler"] or k["ordner_weg"]) else "nein",
        }
        self.client.publish(f"ocr-watch/{geraet}/zustand", json.dumps(daten), retain=True)
        self.client.publish(f"ocr-watch/{geraet}/letzter", json.dumps(letzter, default=str), retain=True)
