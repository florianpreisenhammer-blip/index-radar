"""Kleiner SQLite-Cache fuer Fundamentaldaten.

Warum ueberhaupt Cache, wenn das Dashboard doch "immer neu rechnen" soll?
Weil sich die Daten unterschiedlich schnell bewegen:

  * Kurse, Marktkapitalisierung, Indexmitglieder, S&P-Ankuendigungen
    -> aendern sich taeglich, werden bei JEDEM Start neu geholt.
  * Quartalsgewinne, Streubesitz, IPO-Datum
    -> aendern sich quartalsweise; ein Cache mit kurzer Haltbarkeit spart
       tausende Anfragen und verhindert, dass Yahoo die Abfrage drosselt.

Die Bewertung selbst wird immer komplett neu gerechnet. Das Alter der
Fundamentaldaten steht im Dashboard.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fundamentals_cache.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker    TEXT PRIMARY KEY,
    fetched   REAL NOT NULL,
    payload   TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.execute(_SCHEMA)
    return c


def load(tickers: list[str], ttl_seconds: float) -> dict[str, dict]:
    if ttl_seconds <= 0 or not tickers:
        return {}
    cutoff = time.time() - ttl_seconds
    out: dict[str, dict] = {}
    with _conn() as c:
        for chunk in (tickers[i:i + 500] for i in range(0, len(tickers), 500)):
            q = ",".join("?" * len(chunk))
            for tk, payload in c.execute(
                    f"SELECT ticker, payload FROM fundamentals "
                    f"WHERE ticker IN ({q}) AND fetched >= ?", (*chunk, cutoff)):
                try:
                    out[tk] = json.loads(payload)
                except json.JSONDecodeError:
                    continue
    return out


def store(rows: dict[str, dict]) -> None:
    if not rows:
        return
    now = time.time()
    with _conn() as c:
        c.executemany("INSERT OR REPLACE INTO fundamentals VALUES (?,?,?)",
                      [(tk, now, json.dumps(v, ensure_ascii=False)) for tk, v in rows.items()])


def age_seconds(tickers: list[str]) -> float | None:
    if not tickers:
        return None
    with _conn() as c:
        row = c.execute("SELECT MIN(fetched) FROM fundamentals").fetchone()
    return (time.time() - row[0]) if row and row[0] else None


def clear() -> None:
    with _conn() as c:
        c.execute("DELETE FROM fundamentals")
