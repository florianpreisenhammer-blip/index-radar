"""Aenderungshistorie der Indizes aus Wikipedia.

Liefert das laengere Zeitfenster (mehrere Jahre) fuer die empirische
Aufnahmerate. Die Pressemeldungen (announcements.py) decken nur ~13 Monate ab,
dafuer mit exakten Wirksamkeitsdaten und ohne Redaktionslag.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from ..config import INDEX_SPECS
from ..util import clean_ticker, get, log

WIKI = "https://en.wikipedia.org/wiki/{page}"

DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%B %d %Y")

# Gruende, die nicht aus dem Kandidatenpool heraus prognostizierbar sind.
UNPREDICTABLE = re.compile(r"spin-?off|spun off|ipo of|direct listing|reorganiz", re.I)


@dataclass
class HistoricChange:
    index: str
    date: str
    added: str
    removed: str
    reason: str

    @property
    def predictable(self) -> bool:
        return not UNPREDICTABLE.search(self.reason or "")


def _parse_date(raw: object) -> str | None:
    s = re.sub(r"\[.*?\]", "", str(raw or "")).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [" ".join(str(p) for p in c if str(p) != "nan").strip()
                      for c in df.columns]
    return df


def _pick_history_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    for t in tables:
        t = _flatten(t)
        cols = " ".join(str(c).lower() for c in t.columns)
        if "added" in cols and "removed" in cols and len(t) > 20:
            return t
    return None


def fetch_history(index_key: str) -> list[HistoricChange]:
    spec = INDEX_SPECS[index_key]
    if not spec.wiki_history_page:
        return []
    try:
        html = get(WIKI.format(page=spec.wiki_history_page)).text
        table = _pick_history_table(pd.read_html(io.StringIO(html)))
    except Exception as exc:  # noqa: BLE001
        log.warning("Historie %s nicht ladbar: %s", spec.label, exc)
        return []
    if table is None:
        log.warning("Historie %s: keine passende Tabelle", spec.label)
        return []

    table = _flatten(table)
    def find(*keys: str) -> str | None:
        for c in table.columns:
            low = str(c).lower()
            if all(k in low for k in keys):
                return c
        return None

    c_date = find("date")
    c_add = find("added", "ticker")
    c_rem = find("removed", "ticker")
    c_reason = find("reason")
    if not c_date:
        return []

    out: list[HistoricChange] = []
    for _, row in table.iterrows():
        d = _parse_date(row[c_date])
        if not d:
            continue
        out.append(HistoricChange(
            index=index_key, date=d,
            added=clean_ticker(row[c_add]) if c_add else "",
            removed=clean_ticker(row[c_rem]) if c_rem else "",
            reason=str(row[c_reason]) if c_reason else "",
        ))
    log.info("Historie %-16s %4d Aenderungen (aeltester Eintrag %s)",
             spec.label, len(out), min((c.date for c in out), default="-"))
    return out


def fetch_all_history() -> dict[str, list[HistoricChange]]:
    return {k: fetch_history(k) for k in INDEX_SPECS}
