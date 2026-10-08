"""Statischen Export fuer Vercel (oder jeden anderen Static-Host) bauen.

Ein Static-Host fuehrt kein Python aus. Exportiert wird deshalb ein eingefrorener
Schnappschuss: dieselbe Seite, aber sie liest `data.json` statt der lokalen
API und blendet die Neuberechnen-Schaltflaechen aus. Das Datum des
Schnappschusses steht gut sichtbar im Kopf der Seite.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .util import log

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DEFAULT_DEST = Path(__file__).resolve().parent.parent / "dist"

# Caching: die Seite selbst und die Daten duerfen nie aus einem alten Cache
# kommen - sonst sieht man nach einem Deploy tagelang den vorherigen Stand.
NO_CACHE = "public, max-age=0, must-revalidate"

VERCEL_JSON = {
    "cleanUrls": True,
    "headers": [
        {"source": "/(index.html)?", "headers": [{"key": "Cache-Control", "value": NO_CACHE}]},
        {"source": "/data.json", "headers": [{"key": "Cache-Control", "value": NO_CACHE}]},
    ],
}

# Vercel Build Output API v3: fertige Dateien ohne Build-Schritt ausliefern.
BUILD_OUTPUT_CONFIG = {
    "version": 3,
    "routes": [
        {"src": "/data.json", "headers": {"cache-control": NO_CACHE}, "continue": True},
        {"src": "/(index.html)?", "headers": {"cache-control": NO_CACHE}, "continue": True},
    ],
}


def _readme(generated_at: str) -> str:
    return f"""Index-Radar - statischer Export
================================

Datenstand: {generated_at}

Veroeffentlichen
----------------
Normalfall: der GitHub-Actions-Job rechnet zweimal taeglich und deployt selbst.
Von Hand geht es mit der Vercel-CLI aus dem Projektverzeichnis:

    ./run.sh --export
    vercel deploy --prebuilt --prod

Der Export ist ein eingefrorener Schnappschuss; die laufende Neuberechnung im
Browser gibt es nur lokal mit ./run.sh, weil ein Static-Host kein Python ausfuehrt.

Inhalt
------
index.html     Dashboard (erkennt selbst, dass kein Server da ist)
data.json      Schnappschuss der Berechnung
vercel.json    Caching-Regeln, damit nach einem Deploy nicht der alte Stand steht
"""


def build_vercel_output(source: Path, dest: Path | None = None) -> Path:
    """Fertigen Export in die Vercel Build Output API uebersetzen.

    Damit laeuft auf Vercel kein Build - die Dateien werden unveraendert
    ausgeliefert. Kein Framework-Raten, keine Python-Laufzeit noetig.
    """
    dest = Path(dest or (Path.cwd() / ".vercel" / "output"))
    if dest.exists():
        shutil.rmtree(dest)
    static = dest / "static"
    static.mkdir(parents=True)

    for item in Path(source).iterdir():
        if item.is_file() and item.name not in ("vercel.json", "README.txt"):
            shutil.copyfile(item, static / item.name)
    (dest / "config.json").write_text(json.dumps(BUILD_OUTPUT_CONFIG, indent=2),
                                      encoding="utf-8")
    log.info("Vercel-Build-Output erzeugt: %s", dest)
    return dest


def build(snapshot: dict, dest: Path = DEFAULT_DEST, make_zip: bool = True) -> Path:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))

    # Die Daten werden in die Seite eingebettet, statt sie nachzuladen. Das macht
    # den Export zu einer einzigen, in sich geschlossenen Datei: keine zweite
    # Anfrage, die ein Inhaltsblocker abfangen kann, und kein Zustand, in dem
    # eine frische Seite alte Daten aus dem Cache bekommt.
    # "<" wird escaped, damit ein "</script>" in den Daten das Skript nicht beendet.
    page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    embed = ('<script id="snapshot" type="application/json">'
             + payload.replace("<", "\\u003c") + "</script>")
    # Wichtig: vor das Hauptskript, nicht ans Dateiende - sonst existiert das
    # Element noch nicht, wenn die Seite startet.
    marker = "<body>"
    if marker in page:
        page = page.replace(marker, marker + "\n" + embed, 1)
    else:
        page = embed + page
    (dest / "index.html").write_text(page, encoding="utf-8")

    # data.json bleibt zusaetzlich liegen - praktisch, um die Zahlen direkt
    # abzurufen, ohne die Seite zu parsen.
    (dest / "data.json").write_text(payload, encoding="utf-8")
    (dest / "vercel.json").write_text(json.dumps(VERCEL_JSON, indent=2), encoding="utf-8")

    stamp = snapshot.get("generated_at", datetime.now(timezone.utc).isoformat())
    (dest / "README.txt").write_text(_readme(stamp), encoding="utf-8")

    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    log.info("Export geschrieben: %s (%.1f MB)", dest, size / 1e6)

    if make_zip:
        archive = shutil.make_archive(str(dest), "zip", root_dir=dest)
        log.info("ZIP fuer den Upload: %s (%.1f MB)",
                 archive, Path(archive).stat().st_size / 1e6)
    return dest
