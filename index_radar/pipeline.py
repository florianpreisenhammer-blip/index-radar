"""Orchestrierung: Daten holen, bewerten, Snapshot schreiben.

Jeder Aufruf rechnet komplett neu - es gibt keinen Cache und keine
persistente Kandidatenliste. Das Ergebnis ist ein einziges JSON-Dokument,
das das Dashboard direkt rendert.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import calibration as calib
from . import config as cfg
from .config import (DEPTH_PER_INDEX, FUNDAMENTALS_TTL_SECONDS, HORIZON_DAYS, INDEX_ORDER,
                     INDEX_SPECS, MIN_IWF, MIN_LIQUIDITY_RATIO, MIN_SEASONING_DAYS,
                     SOFT_CAP_CEILING, SOFT_CAP_FLOOR, SP400_MAX_CAP, SP400_MIN_CAP,
                     SP500_MIN_CAP, SP600_MAX_CAP, SP600_MIN_CAP, THRESHOLDS_ASOF,
                     UNIVERSE_MIN_CAP)
from .scoring import score_index
from .sources import announcements, constituents, history, market, thresholds
from .util import log, now_iso

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT = DATA_DIR / "latest.json"


def _pending_maps(pending: list[announcements.IndexChange]) -> tuple[dict, dict]:
    adds: dict[str, dict[str, str]] = {k: {} for k in INDEX_ORDER}
    dels: dict[str, dict[str, str]] = {k: {} for k in INDEX_ORDER}
    for c in pending:
        target = adds if c.action == "addition" else dels
        target.setdefault(c.index, {})[c.ticker] = c.effective_date
    return adds, dels


def _deletion_memory(hist, changes):
    """Wer wurde wann aus welchem Index gestrichen?"""
    recent: dict[str, dict[str, str]] = {k: {} for k in INDEX_ORDER}
    older: dict[str, dict[str, str]] = {k: {} for k in INDEX_ORDER}
    cut_recent = (date.today() - timedelta(days=365)).isoformat()
    cut_old = (date.today() - timedelta(days=365 * 4)).isoformat()
    for key, entries in hist.items():
        for c in entries:
            if not c.removed:
                continue
            if c.date >= cut_recent:
                recent[key][c.removed] = c.date
            elif c.date >= cut_old:
                older[key][c.removed] = c.date
    for c in changes:
        if c.action == "deletion" and c.effective_date >= cut_recent:
            recent.setdefault(c.index, {})[c.ticker] = c.effective_date
    return recent, older


def _at_risk(index_key: str, membership, universe, pending_del: dict[str, str]) -> list[dict]:
    """Mitglieder, deren Marktkapitalisierung das Indexband verlassen hat."""
    spec = INDEX_SPECS[index_key]
    out = []
    for t in membership.tickers(index_key):
        u = universe.get(t)
        if not u or not u["market_cap"]:
            continue
        cap = u["market_cap"]
        reason = ""
        if spec.min_cap and cap < spec.min_cap * 0.7:
            reason = "deutlich unter der Mindestgroesse"
        elif spec.min_cap and cap < spec.min_cap:
            reason = "unter der Mindestgroesse"
        elif spec.max_cap and cap > spec.max_cap * 1.5:
            reason = "deutlich ueber dem Indexband (Aufstiegskandidat)"
        if not reason:
            continue
        out.append({
            "ticker": t, "name": u["name"], "market_cap": cap, "reason": reason,
            "announced_exit": pending_del.get(t, ""),
        })
    out.sort(key=lambda r: r["market_cap"])
    return out[:25]


def _band_priority(cap: float, spec) -> float:
    """Wie gut passt die Groesse ins Zielband? (nur zur Vorauswahl)"""
    import math
    if spec.min_cap and spec.max_cap:
        center = math.sqrt(spec.min_cap * spec.max_cap)
        return -abs(math.log(max(cap, 1) / center))
    return math.log(max(cap, 1))


DETAIL_ROWS = 250


def _slim(index_result: dict) -> None:
    """Detailbegruendungen nur fuer die vorderen Raenge mitschicken.

    Sonst waechst der Snapshot auf zweistellige Megabyte, ohne dass die Details
    je angesehen wuerden - die Blocker-Zusammenfassung bleibt fuer alle erhalten.
    """
    for c in index_result["candidates"]:
        if c["rank"] > DETAIL_ROWS or not c["eligible"]:
            c["gates"] = [g for g in c["gates"] if g["state"] in ("fail", "warn")]
        if c["rank"] > DETAIL_ROWS:
            c["factors"] = []


def run(horizon_days: int = HORIZON_DAYS, progress=None, fresh: bool = False) -> dict:
    t_start = time.time()
    steps = []

    def step(name: str, fn):
        t0 = time.time()
        if progress:
            progress(name)
        log.info("-> %s", name)
        result = fn()
        steps.append({"name": name, "seconds": round(time.time() - t0, 1)})
        return result

    # Zuerst die Groessengrenzen: sie bestimmen, wer ueberhaupt Kandidat ist.
    th = step("Groessengrenzen (S&P-Meldung)",
              lambda: thresholds.fetch(use_cache=not fresh))
    if th is not None:
        cfg.apply_thresholds(th)
    else:
        log.warning("Groessengrenzen nicht abrufbar - es gelten die hinterlegten "
                    "Rueckfallwerte (Stand %s)", cfg.THRESHOLDS_ASOF)

    members = step("Indexmitglieder (Wikipedia)", constituents.fetch_all_members)
    changes = step("S&P-Pressemeldungen", lambda: announcements.fetch_changes())
    hist = step("Aenderungshistorie", history.fetch_all_history)
    universe_source = {"value": ""}

    def load_universe():
        u, src = market.build_universe(UNIVERSE_MIN_CAP)
        universe_source["value"] = src
        return u

    universe = step("Marktuniversum", load_universe)

    pending, _ = announcements.split_pending(changes)
    pend_adds, pend_dels = _pending_maps(pending)
    cal = step("Kalibrierung", lambda: calib.build(hist, changes))
    recent_del, older_del = _deletion_memory(hist, changes)

    # --- Tiefenanalyse-Auswahl ------------------------------------------
    # Vollstaendig geprueft wird nur, wer realistisch in Frage kommt. Der Rest
    # des Pools geht spaeter als Restgewicht in die Normierung ein.
    # Vorfilter: Bilanzwaehrung trennt US-Unternehmen von ADRs auslaendischer
    # Konzerne - das spart tausende teure Einzelabfragen.
    band_relevant: set[str] = set()
    for key in ("sp500", "sp400", "sp600"):
        spec = INDEX_SPECS[key]
        lo = (spec.min_cap or 0) * SOFT_CAP_FLOOR
        hi = (spec.max_cap * SOFT_CAP_CEILING) if spec.max_cap else float("inf")
        band_relevant |= {t for t, u in universe.items()
                          if lo <= u["market_cap"] <= hi and t not in members.tickers(key)}
    step("Vorfilter Bilanzwaehrung",
         lambda: market.enrich_currency(universe, sorted(band_relevant)))

    def reports_usd(t: str) -> bool:
        cur = universe.get(t, {}).get("financial_currency", "")
        return cur in ("", "USD")

    # S&P-500-Mitglieder ausserhalb des S&P 100 sind der Kandidatenpool fuer den
    # S&P 100 - fuer sie lohnt das Tiefenprofil ebenfalls.
    need: set[str] = set(members.tickers("sp500")) - set(members.tickers("sp100"))
    pool_sizes: dict[str, int] = {}
    for key in ("sp500", "sp400", "sp600"):
        spec = INDEX_SPECS[key]
        lo = (spec.min_cap or 0) * SOFT_CAP_FLOOR
        hi = (spec.max_cap * SOFT_CAP_CEILING) if spec.max_cap else float("inf")
        pool = [(t, u["market_cap"]) for t, u in universe.items()
                if lo <= u["market_cap"] <= hi and t not in members.tickers(key)
                and reports_usd(t)]
        pool_sizes[key] = len(pool)
        pool.sort(key=lambda r: -_band_priority(r[1], spec))
        need |= {t for t, _ in pool[:DEPTH_PER_INDEX.get(key, 300)]}
        # Mitglieder anderer S&P-Indizes im Zielband sind die staerksten
        # Kandidaten - sie kommen immer mit hinein.
        for other in ("sp500", "sp400", "sp600"):
            if other == key:
                continue
            need |= {t for t in members.tickers(other)
                     if t in universe and lo <= universe[t]["market_cap"] <= hi}
    for m in pend_adds.values():
        need |= set(m)
    need = {t for t in need if t}

    fund_stats = {}

    def fetch_funds():
        def prog(done, total):
            if progress:
                progress(f"Fundamentaldaten {done}/{total}")
        data, stats = market.fetch_fundamentals_bulk(sorted(need), progress=prog,
                                                     ttl=0 if fresh else FUNDAMENTALS_TTL_SECONDS)
        fund_stats.update(stats)
        return data

    funds = step(f"Fundamentaldaten ({len(need)} Titel)", fetch_funds)

    indices = []
    for key in INDEX_ORDER:
        res = step(f"Bewertung {INDEX_SPECS[key].label}", lambda k=key: score_index(
            k, fundamentals=funds, universe=universe, membership=members,
            pending_additions=pend_adds.get(k, {}), pending_deletions=pend_dels.get(k, {}),
            calibration=cal[k], recent_deletions=recent_del.get(k, {}),
            old_members=older_del.get(k, {}), horizon_days=horizon_days,
            untested_pool=max(0, pool_sizes.get(k, 0) - DEPTH_PER_INDEX.get(k, 0))))
        res["at_risk"] = _at_risk(key, members, universe, pend_dels.get(key, {}))
        _slim(res)
        indices.append(res)

    snapshot = {
        "generated_at": now_iso(),
        "horizon_days": horizon_days,
        "runtime_seconds": round(time.time() - t_start, 1),
        "steps": steps,
        "universe_size": len(universe),
        "universe_source": universe_source["value"],
        "fundamentals_loaded": len(funds),
        "fundamentals_failed": sum(1 for f in funds.values() if f.error),
        "fundamentals_stats": fund_stats,
        "fundamentals_ttl_hours": 0 if fresh else FUNDAMENTALS_TTL_SECONDS / 3600,
        "thresholds": {
            "asof": cfg.THRESHOLDS_ASOF,
            "source": cfg.THRESHOLDS_SOURCE,
            "source_url": cfg.THRESHOLDS_URL,
            "live": th is not None,
            "sp500_min": cfg.SP500_MIN_CAP, "sp400_min": cfg.SP400_MIN_CAP,
            "sp400_max": cfg.SP400_MAX_CAP, "sp600_min": cfg.SP600_MIN_CAP,
            "sp600_max": cfg.SP600_MAX_CAP, "min_iwf": MIN_IWF,
            "min_liquidity": MIN_LIQUIDITY_RATIO, "min_seasoning_days": MIN_SEASONING_DAYS,
        },
        "pending_changes": [c.to_dict() for c in pending],
        "recent_changes": [c.to_dict() for c in changes
                           if c.effective_date < date.today().isoformat()][:60],
        "indices": indices,
        "sources": [
            {"name": "S&P Dow Jones Indices - Pressemeldungen",
             "url": "https://press.spglobal.com/", "use": "angekuendigte Indexaenderungen"},
            {"name": "Wikipedia - Indexmitglieder und Aenderungshistorie",
             "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
             "use": "aktuelle Mitglieder, GICS-Sektoren, Historie"},
            {"name": "Yahoo Finance",
             "url": "https://finance.yahoo.com/", "use": "Universum, Kurse, Fundamentaldaten"},
        ],
    }
    return snapshot


def write(snapshot: dict, path: Path = SNAPSHOT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    log.info("Snapshot geschrieben: %s (%.1f MB)", path, path.stat().st_size / 1e6)
    return path
