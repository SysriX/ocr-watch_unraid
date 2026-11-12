"use strict";

// ---------------------------------------------------------------- Ordner-Browser
let odZiel = null, odPfad = "";

function ordnerWaehlen(knopf) {
  odZiel = knopf.parentElement.querySelector("input");
  ordnerLaden(odZiel.value || "");
  document.getElementById("ordnerdialog").showModal();
}

async function ordnerLaden(pfad) {
  const r = await fetch("/api/ordner?pfad=" + encodeURIComponent(pfad));
  if (r.status === 401) { location.href = "/anmelden"; return; }
  const d = await r.json();
  odPfad = d.pfad;
  document.getElementById("od-pfad").textContent = d.pfad + (d.fehler ? "  (" + d.fehler + ")" : "");
  const liste = document.getElementById("od-liste");
  liste.replaceChildren();
  if (d.oben) {
    const li = document.createElement("li");
    li.className = "hoch"; li.textContent = "⬑ eine Ebene höher";
    li.onclick = () => ordnerLaden(d.oben);
    liste.append(li);
  }
  for (const name of d.ordner) {
    const li = document.createElement("li");
    li.textContent = "📁 " + name;
    li.onclick = () => ordnerLaden(d.pfad.replace(/\/$/, "") + "/" + name);
    liste.append(li);
  }
  if (!d.ordner.length) {
    const li = document.createElement("li");
    li.className = "hoch"; li.textContent = "(keine Unterordner)";
    liste.append(li);
  }
}

function ordnerUebernehmen() {
  if (odZiel) odZiel.value = odPfad;
  document.getElementById("ordnerdialog").close();
}

async function ordnerNeu() {
  const name = prompt("Name des neuen Ordners in\n" + odPfad);
  if (!name) return;
  const r = await fetch("/api/ordner/neu", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pfad: odPfad, name}),
  });
  const d = await r.json();
  if (d.fehler) { alert(d.fehler); return; }
  ordnerLaden(d.pfad);
}

// ---------------------------------------------------------------- Übersicht live
function uebersichtAktualisieren() {
  const setze = (k, v) => { const el = document.querySelector(`[data-k="${k}"]`); if (el) el.textContent = v; };
  let letzte = null;
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const r = await fetch("/api/status");
      if (!r.ok) return;
      const d = await r.json();
      setze("warteschlange", d.wartet + d.laeuft);
      setze("heute_fertig", d.heute_fertig);
      setze("heute_uebersprungen", d.heute_uebersprungen);
      setze("fehler_offen", d.fehler_offen);
      const neu = d.letzter ? d.letzter.id + d.letzter.status : null;
      if (letzte !== null && neu !== letzte && !document.querySelector("details[open]")) location.reload();
      letzte = neu;
    } catch (e) { /* Server neu gestartet – nächster Versuch */ }
  }, 5000);
}
