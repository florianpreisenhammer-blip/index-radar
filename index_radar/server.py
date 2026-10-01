"""Lokaler Dashboard-Server (nur Standardbibliothek).

  /                 Dashboard
  /api/data         aktueller Snapshot
  /api/recompute    Neuberechnung anstossen (POST)
  /api/status       Fortschritt der laufenden Berechnung
"""
from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import pipeline
from .util import log

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

_state = {
    "snapshot": None,
    "running": False,
    "step": "",
    "started_at": "",
    "finished_at": "",
    "error": "",
}
_lock = threading.Lock()


def recompute(fresh: bool = False) -> None:
    with _lock:
        if _state["running"]:
            return
        _state.update(running=True, error="", step="Start",
                      started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def work():
        try:
            snap = pipeline.run(progress=lambda s: _state.update(step=s), fresh=fresh)
            pipeline.write(snap)
            with _lock:
                _state["snapshot"] = snap
        except Exception as exc:  # noqa: BLE001
            log.exception("Neuberechnung fehlgeschlagen")
            with _lock:
                _state["error"] = str(exc)
        finally:
            with _lock:
                _state.update(running=False, step="",
                              finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    threading.Thread(target=work, daemon=True).start()


def load_existing() -> None:
    if pipeline.SNAPSHOT.exists():
        try:
            _state["snapshot"] = json.loads(pipeline.SNAPSHOT.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # ruhiges Log
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def _file(self, name: str) -> None:
        path = (WEB_DIR / name).resolve()
        if not path.is_file() or WEB_DIR.resolve() not in path.parents:
            self._send(b"not found", "text/plain", 404)
            return
        ctype = {".html": "text/html; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8"}.get(path.suffix, "application/octet-stream")
        self._send(path.read_bytes(), ctype)

    def do_GET(self):  # noqa: N802
        route = self.path.split("?")[0]
        if route in ("/", "/index.html"):
            self._file("index.html")
        elif route == "/api/status":
            with _lock:
                snap = _state["snapshot"]
                self._json({
                    "running": _state["running"], "step": _state["step"],
                    "error": _state["error"],
                    "generated_at": (snap or {}).get("generated_at", ""),
                })
        elif route == "/api/data":
            with _lock:
                snap = _state["snapshot"]
            if snap is None:
                self._json({"error": "noch keine Daten"}, 503)
            else:
                self._send(json.dumps(snap, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
        else:
            self._file(route.lstrip("/"))

    def do_POST(self):  # noqa: N802
        route = self.path.split("?")[0]
        if route == "/api/recompute":
            recompute(fresh="fresh=1" in self.path)
            self._json({"ok": True})
        else:
            self._json({"error": "unbekannt"}, 404)


def serve(port: int = 8787, open_browser: bool = True, recompute_on_start: bool = True) -> None:
    load_existing()
    if recompute_on_start:
        recompute()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    log.info("Dashboard laeuft auf %s  (Beenden mit Strg+C)", url)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Beendet.")
    finally:
        httpd.server_close()
