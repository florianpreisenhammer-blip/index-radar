"""Aktuelle Indexmitglieder von Wikipedia.

Warum Wikipedia: die Listen werden taeglich gepflegt und sind frei abrufbar.
Wichtig - und genau der Fehler, den das Vorbild-Dashboard hatte: Wikipedia
bildet den *effektiven* Stand ab, nicht bereits *angekuendigte* Aenderungen.
Deshalb ergaenzt announcements.py die Pending-Changes aus den S&P-Pressemeldungen.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import pandas as pd

from ..config import INDEX_SPECS
from ..util import clean_ticker, get, log

WIKI = "https://en.wikipedia.org/wiki/{page}"


@dataclass
class Membership:
    members: dict[str, dict[str, set[str] | dict]]  # index-key -> info
    fetched_at: str = ""

    def tickers(self, index_key: str) -> set[str]:
        return self.members.get(index_key, {}).get("tickers", set())

    def sectors(self, index_key: str) -> dict[str, str]:
        return self.members.get(index_key, {}).get("sectors", {})


def _pick_constituent_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    best = None
    for t in tables:
        cols = [str(c) for c in t.columns]
        has_sym = any(c.split("'")[0].startswith(("Symbol", "Ticker")) or c in ("Symbol", "Ticker")
                      for c in cols)
        if has_sym and len(t) > 50:
            if best is None or len(t) > len(best):
                best = t
    return best


def _col(df: pd.DataFrame, *names: str) -> str | None:
    for n in names:
        for c in df.columns:
            if str(c).strip().lower() == n.lower():
                return c
    return None


def fetch_index_members(index_key: str) -> dict:
    spec = INDEX_SPECS[index_key]
    html = get(WIKI.format(page=spec.wiki_page)).text
    tables = pd.read_html(io.StringIO(html))
    df = _pick_constituent_table(tables)
    if df is None:
        raise RuntimeError(f"Keine Mitgliederliste fuer {spec.label} gefunden")

    sym_col = _col(df, "Symbol", "Ticker")
    name_col = _col(df, "Security", "Name", "Company")
    sector_col = _col(df, "GICS Sector", "Sector")

    tickers: set[str] = set()
    names: dict[str, str] = {}
    sectors: dict[str, str] = {}
    for _, row in df.iterrows():
        tk = clean_ticker(row[sym_col])
        if not tk or len(tk) > 6:
            continue
        tickers.add(tk)
        if name_col is not None:
            names[tk] = str(row[name_col]).strip()
        if sector_col is not None:
            sectors[tk] = str(row[sector_col]).strip()

    log.info("Mitglieder %-16s %4d Titel", spec.label, len(tickers))
    if len(tickers) < spec.members_target * 0.9:
        log.warning("%s: nur %d von erwarteten ~%d Mitgliedern geparst",
                    spec.label, len(tickers), spec.members_target)
    return {"tickers": tickers, "names": names, "sectors": sectors, "count": len(tickers)}


def fetch_all_members() -> Membership:
    from ..util import now_iso
    out: dict[str, dict] = {}
    for key in INDEX_SPECS:
        out[key] = fetch_index_members(key)
    return Membership(members=out, fetched_at=now_iso())
