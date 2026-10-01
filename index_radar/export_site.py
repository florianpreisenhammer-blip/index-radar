"""Statischen Export fuer Netlify (oder jeden anderen Static-Host) bauen.

Netlify fuehrt kein Python aus. Exportiert wird deshalb ein eingefrorener
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

HEADERS = """\
/data.json
  Cache-Control: public, max-age=0, must-revalidate
/index.html
  Cache-Control: public, max-age=0, must-revalidate
"""

NETLIFY_TOML = """\
# Statischer Export des Index-Radar - es wird nichts gebaut, nur ausgeliefert.
[build]
  publish = "."
  command = ""
"""


def _readme(generated_at: str) -> str:
    return f"""Index-Radar - statischer Export
================================

Datenstand: {generated_at}

Hochladen bei Netlify
---------------------
1. https://app.netlify.com/drop oeffnen (oder im Team "Add new project" ->
   "Deploy manually").
2. Diesen Ordner komplett hineinziehen - nicht die einzelnen Dateien.
3. Fertig. Die Seite ist sofort unter der vergebenen *.netlify.app-Adresse erreichbar.

Aktualisieren
-------------
Der Export ist eingefroren. Fuer neue Zahlen lokal

    ./run.sh --export

ausfuehren und den Ordner erneut hochziehen (Netlify: "Deploys" -> Drag & Drop).
Die laufende Neuberechnung im Browser gibt es nur lokal mit ./run.sh, weil
Netlify kein Python ausfuehrt.

Inhalt
------
index.html     Dashboard (erkennt selbst, dass kein Server da ist)
data.json      Schnappschuss der Berechnung
_headers       verhindert, dass Netlify veraltete Daten cached
netlify.toml   sagt Netlify, dass nichts gebaut werden muss
"""


def build(snapshot: dict, dest: Path = DEFAULT_DEST, make_zip: bool = True) -> Path:
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    shutil.copyfile(WEB_DIR / "index.html", dest / "index.html")
    (dest / "data.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (dest / "_headers").write_text(HEADERS, encoding="utf-8")
    (dest / "netlify.toml").write_text(NETLIFY_TOML, encoding="utf-8")

    stamp = snapshot.get("generated_at", datetime.now(timezone.utc).isoformat())
    (dest / "README.txt").write_text(_readme(stamp), encoding="utf-8")

    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    log.info("Export geschrieben: %s (%.1f MB)", dest, size / 1e6)

    if make_zip:
        archive = shutil.make_archive(str(dest), "zip", root_dir=dest)
        log.info("ZIP fuer den Upload: %s (%.1f MB)",
                 archive, Path(archive).stat().st_size / 1e6)
    return dest
