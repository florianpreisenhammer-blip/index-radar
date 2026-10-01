"""Empirische Kalibrierung aus der tatsaechlichen Indexhistorie.

Zwei Groessen werden gemessen statt geraten:
  * Aufnahmerate pro Index und Jahr (aus der Wikipedia-Aenderungshistorie,
    bereinigt um nicht prognostizierbare Faelle wie Spin-offs).
  * Herkunft der Neuzugaenge: Aus welchem Index kamen sie unmittelbar davor?
    (aus den S&P-Pressemeldungen: eine Aufnahme in Index X am selben Stichtag
    wie eine Streichung desselben Tickers aus Index Y = Auf-/Abstieg Y -> X)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import BASE_RATE_MIN_YEARS, BASE_RATE_YEARS, FALLBACK_ADD_RATE, INDEX_ORDER
from .sources.announcements import IndexChange
from .sources.history import HistoricChange
from .util import log

EXTERNAL = "extern"  # ausserhalb des S&P Composite 1500


@dataclass
class IndexCalibration:
    index: str
    adds_per_year: float
    n_adds: int
    window_years: float
    source: str
    feeder_counts: dict[str, int] = field(default_factory=dict)
    feeder_shares: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "adds_per_year": round(self.adds_per_year, 2),
            "n_adds": self.n_adds,
            "window_years": round(self.window_years, 2),
            "source": self.source,
            "feeder_counts": self.feeder_counts,
            "feeder_shares": {k: round(v, 3) for k, v in self.feeder_shares.items()},
        }


def _rate_from_history(index: str, hist: list[HistoricChange]) -> tuple[float, int, float, str]:
    if not hist:
        return 0.0, 0, 0.0, "keine Historie"
    cutoff = (date.today() - timedelta(days=365 * BASE_RATE_YEARS)).isoformat()
    window = [c for c in hist if c.date >= cutoff and c.added]
    if not window:
        return 0.0, 0, 0.0, "keine Aenderungen im Fenster"
    oldest = min(c.date for c in window)
    years = max(BASE_RATE_MIN_YEARS,
                (date.today() - date.fromisoformat(oldest)).days / 365.0)
    predictable = [c for c in window if c.predictable]
    return len(predictable) / years, len(predictable), years, "Wikipedia-Aenderungshistorie"


def _rate_from_announcements(index: str, changes: list[IndexChange]) -> tuple[float, int, float, str]:
    adds = [c for c in changes if c.index == index and c.action == "addition"]
    if not adds:
        return 0.0, 0, 0.0, "keine Meldungen"
    oldest = min(c.effective_date for c in adds)
    years = max(BASE_RATE_MIN_YEARS,
                (date.today() - date.fromisoformat(oldest)).days / 365.0)
    return len(adds) / years, len(adds), years, "S&P-Pressemeldungen"


def _feeder_stats(changes: list[IndexChange]) -> dict[str, dict[str, int]]:
    """Fuer jede Aufnahme: aus welchem Index kam der Titel am selben Stichtag?"""
    deletions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for c in changes:
        if c.action == "deletion":
            deletions[(c.ticker, c.effective_date)].add(c.index)

    counts: dict[str, dict[str, int]] = {k: defaultdict(int) for k in INDEX_ORDER}
    for c in changes:
        if c.action != "addition":
            continue
        # S&P 100 ist eine Teilmenge des S&P 500 - Herkunft dort immer "sp500".
        if c.index == "sp100":
            counts["sp100"]["sp500"] += 1
            continue
        from_idx = deletions.get((c.ticker, c.effective_date), set()) - {c.index}
        # gleichzeitige Streichung aus 100 zaehlt nicht als Herkunftsindex
        from_idx.discard("sp100")
        counts[c.index][next(iter(sorted(from_idx)), EXTERNAL)] += 1
    return {k: dict(v) for k, v in counts.items()}


def build(history: dict[str, list[HistoricChange]],
          changes: list[IndexChange]) -> dict[str, IndexCalibration]:
    feeders = _feeder_stats(changes)
    out: dict[str, IndexCalibration] = {}
    for key in INDEX_ORDER:
        rate, n, years, src = _rate_from_history(key, history.get(key, []))
        if rate <= 0:
            rate, n, years, src = _rate_from_announcements(key, changes)
        if rate <= 0:
            rate, n, years, src = FALLBACK_ADD_RATE[key], 0, 0.0, "Erfahrungswert (Fallback)"
        counts = feeders.get(key, {})
        total = sum(counts.values()) or 1
        out[key] = IndexCalibration(
            index=key, adds_per_year=rate, n_adds=n, window_years=years, source=src,
            feeder_counts=counts,
            feeder_shares={k: v / total for k, v in counts.items()},
        )
        log.info("Kalibrierung %-6s %.1f Aufnahmen/Jahr (n=%d, %.1f J., %s) Herkunft=%s",
                 key, rate, n, years, src,
                 {k: f"{v:.0%}" for k, v in out[key].feeder_shares.items()})
    return out
