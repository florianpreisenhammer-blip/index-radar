"""Bewertungsmodell: von Kriterien zu kalibrierten Wahrscheinlichkeiten.

Kernidee (und der entscheidende Unterschied zu einem frei geschaetzten
Prozentwert): die Summe aller Wahrscheinlichkeiten eines Index entspricht der
empirisch gemessenen Zahl der Aufnahmen im Prognosehorizont.

    erwartete Aufnahmen  E = Aufnahmerate p.a. x Horizont
    Gewicht je Kandidat  w = Produkt der Einzelfaktoren
    Wahrscheinlichkeit   p = 1 - exp(-E * w / Sum(w))      (Poisson-Thinning)

Damit ist "18 %" nicht Bauchgefuehl, sondern: unter den aktuell qualifizierten
Kandidaten faellt statistisch in 18 % der Faelle die naechste freie Position
auf diesen Titel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .calibration import EXTERNAL, IndexCalibration
from .config import (FACTOR_CAPS, INDEX_SPECS, MAX_PROBABILITY, SCORE_TEMPERING,
                     IndexSpec)
from .eligibility import _de, evaluate, summarize
from .sources.market import Fundamentals

# Yahoo-Sektoren auf GICS-Namen abbilden (Wikipedia fuehrt GICS).
YAHOO_TO_GICS = {
    "Technology": "Information Technology",
    "Financial Services": "Financials",
    "Healthcare": "Health Care",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Communication Services": "Communication Services",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Basic Materials": "Materials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}


def gics(sector: str) -> str:
    return YAHOO_TO_GICS.get((sector or "").strip(), (sector or "").strip() or "Unbekannt")


def _clip(key: str, value: float) -> float:
    lo, hi = FACTOR_CAPS[key]
    return max(lo, min(hi, value))


@dataclass
class Factor:
    key: str
    label: str
    value: float
    detail: str

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label,
                "value": round(self.value, 3), "detail": self.detail}


@dataclass
class Candidate:
    ticker: str
    name: str
    market_cap: float
    sector: str
    current_index: str
    status: str                     # kandidat | angekuendigt | gesperrt
    eligible: bool
    blockers: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    gates: list[dict] = field(default_factory=list)
    factors: list[Factor] = field(default_factory=list)
    weight: float = 0.0
    raw_score: float = 0.0
    share_of_next_slot: float = 0.0
    probability: float = 0.0
    probability_quarter: float = 0.0
    rank: int = 0
    effective_date: str = ""
    note: str = ""
    liquidity_ratio: float = 0.0
    iwf: float = 0.0
    profitable_quarters: int = 0
    days_since_ipo: int | None = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "name": self.name, "market_cap": self.market_cap,
            "sector": self.sector, "current_index": self.current_index,
            "status": self.status, "eligible": self.eligible,
            "blockers": self.blockers, "data_gaps": self.data_gaps,
            "gates": self.gates, "factors": [f.to_dict() for f in self.factors],
            "weight": round(self.weight, 4), "raw_score": round(self.raw_score, 3),
            "share_of_next_slot": round(self.share_of_next_slot, 4),
            "probability": round(self.probability, 4),
            "probability_quarter": round(self.probability_quarter, 4),
            "rank": self.rank, "effective_date": self.effective_date, "note": self.note,
            "liquidity_ratio": round(self.liquidity_ratio, 2),
            "iwf": round(self.iwf, 3),
            "profitable_quarters": self.profitable_quarters,
            "days_since_ipo": self.days_since_ipo,
        }


# ---------------------------------------------------------------------------
# Einzelfaktoren
# ---------------------------------------------------------------------------
def factor_cap_fit(f: Fundamentals, spec: IndexSpec, member_caps: list[float]) -> Factor:
    cap = f.market_cap
    if spec.min_cap and spec.max_cap:
        lo, hi = spec.min_cap, spec.max_cap
        center = math.sqrt(lo * hi)
        sigma = math.log(hi / lo) / 3.2
        z = (math.log(max(cap, 1)) - math.log(center)) / sigma
        v = 0.45 + 1.55 * math.exp(-0.5 * z * z)
        detail = f"Bandmitte {_de(center / 1e9)} Mrd. $, Abstand {_de(z, 1)} Sigma"
    elif spec.min_cap:
        # Saettigende Kurve: deutlich ueber der Schwelle bringt kaum noch Vorteil,
        # deutlich darunter faellt die Chance schnell ab.
        ratio = max(cap / spec.min_cap, 0.05)
        v = 2.4 / (1.0 + math.exp(-2.4 * math.log(ratio)))
        detail = f"{_de(ratio, 2)}-fache des Richtwerts ({_de(spec.min_cap / 1e9)} Mrd. $)"
    else:  # S&P 100: relativ zu den bestehenden Mitgliedern
        smaller = sum(1 for c in member_caps if c < cap)
        share = smaller / len(member_caps) if member_caps else 0.5
        v = 0.2 + 3.0 * share ** 2
        detail = f"groesser als {share:.0%} der Indexmitglieder"
    return Factor("cap_fit", "Groesse im Zielband", _clip("cap_fit", v), detail)


def factor_feeder(current_index: str, cal: IndexCalibration,
                  pool_shares: dict[str, float]) -> Factor:
    src = current_index or EXTERNAL
    observed = cal.feeder_shares.get(src, 0.0)
    pool = pool_shares.get(src, 0.0)
    if pool <= 0:
        v, detail = 1.0, "keine Vergleichsbasis"
    elif observed <= 0:
        v = 0.35
        detail = f"in {cal.window_years:.0f} J. kein Zugang aus {_src_label(src)}"
    else:
        v = observed / pool
        detail = (f"{observed:.0%} der Aufnahmen kamen aus {_src_label(src)}, "
                  f"Anteil am Kandidatenpool {pool:.0%}")
    return Factor("feeder", "Herkunftsindex", _clip("feeder", v), detail)


def _src_label(src: str) -> str:
    return {"sp500": "dem S&P 500", "sp400": "dem MidCap 400",
            "sp600": "dem SmallCap 600", EXTERNAL: "ausserhalb des S&P 1500"}.get(src, src)


def factor_liquidity(f: Fundamentals) -> Factor:
    lr = f.liquidity_ratio
    v = 0.6 if lr <= 0 else (lr / 1.5) ** 0.35
    return Factor("liquidity", "Handelsliquiditaet", _clip("liquidity", v),
                  f"Quote {_de(lr, 2)} (Minimum 0,75)")


def factor_float(f: Fundamentals) -> Factor:
    iwf = f.iwf
    v = 0.85 if iwf <= 0 else 0.7 + 0.6 * max(0.0, (iwf - 0.5) / 0.5)
    return Factor("float", "Streubesitz", _clip("float", v),
                  f"{iwf:.0%} frei handelbar" if iwf > 0 else "keine Float-Daten")


def factor_profitability(f: Fundamentals) -> Factor:
    q = f.profitable_quarters
    ttm = f.net_income_ttm
    v = {4: 1.3, 3: 1.05, 2: 0.8}.get(q, 0.55)
    if ttm and f.market_cap:
        margin_boost = min(0.1, max(-0.1, (ttm / f.market_cap) * 2))
        v += margin_boost
    return Factor("profitability", "Gewinnqualitaet", _clip("profitability", v),
                  f"{q} von 4 Quartalen profitabel")


def factor_sector_gap(f: Fundamentals, index_sector_share: dict[str, float],
                      pool_sector_share: dict[str, float]) -> Factor:
    s = gics(f.sector)
    in_idx = index_sector_share.get(s, 0.0)
    in_pool = pool_sector_share.get(s, 0.0)
    gap = in_pool - in_idx
    v = 1.0 + 3.0 * gap
    return Factor("sector_gap", "Sektorbalance", _clip("sector_gap", v),
                  f"{s}: {in_idx:.0%} im Index vs. {in_pool:.0%} im Markt "
                  f"({'unterrepraesentiert' if gap > 0 else 'ausreichend vertreten'})")


def factor_seasoning(f: Fundamentals) -> Factor:
    d = f.days_since_ipo
    if d is None:
        v, detail = 0.9, "IPO-Datum unbekannt"
    elif d < 365:
        v, detail = 0.4, f"erst {d} Tage boersennotiert"
    elif d < 550:
        v, detail = 0.6, "knapp ueber der 12-Monats-Grenze"
    elif d < 900:
        v, detail = 0.85, "rund zwei Jahre boersennotiert"
    else:
        v, detail = 1.1, f"{d // 365} Jahre boersennotiert"
    return Factor("seasoning", "Boersenhistorie", _clip("seasoning", v), detail)


def factor_swap_fit(f: Fundamentals, member_caps: list[float]) -> Factor:
    if not member_caps:
        return Factor("swap_fit", "Groessenvorteil gegenueber Mitgliedern", 1.0, "keine Daten")
    smaller = sum(1 for c in member_caps if c < f.market_cap)
    share = smaller / len(member_caps)
    v = 0.75 + 1.7 * share
    return Factor("swap_fit", "Groessenvorteil gegenueber Mitgliedern", _clip("swap_fit", v),
                  f"groesser als {share:.0%} der aktuellen Mitglieder")


def factor_quality(warn_count: int, gap_count: int) -> Factor:
    """Offene Fragen in den Daten senken die Prognose, statt sie zu verschweigen."""
    v = max(0.5, 0.88 ** (warn_count + gap_count))
    if warn_count + gap_count == 0:
        return Factor("quality", "Datenlage", 1.0, "alle Kriterien belastbar geprueft")
    return Factor("quality", "Datenlage", v,
                  f"{warn_count + gap_count} Kriterium/Kriterien unsicher oder grenzwertig")


def factor_history(ticker: str, index_key: str, recent_deletions: dict[str, str],
                   old_members: dict[str, str]) -> Factor:
    if ticker in recent_deletions:
        return Factor("history", "Indexhistorie", _clip("history", 0.9),
                      f"erst am {recent_deletions[ticker]} aus dem Index gestrichen")
    if ticker in old_members:
        return Factor("history", "Indexhistorie", _clip("history", 1.15),
                      f"frueheres Mitglied (Streichung {old_members[ticker]})")
    return Factor("history", "Indexhistorie", 1.0, "keine Vorgeschichte im Index")


# ---------------------------------------------------------------------------
# Hauptberechnung
# ---------------------------------------------------------------------------
def _share(counter: dict[str, int]) -> dict[str, float]:
    total = sum(counter.values()) or 1
    return {k: v / total for k, v in counter.items()}


def score_index(index_key: str, *, fundamentals: dict[str, Fundamentals],
                universe: dict[str, dict], membership, pending_additions: dict[str, str],
                pending_deletions: dict[str, str], calibration: IndexCalibration,
                recent_deletions: dict[str, str], old_members: dict[str, str],
                horizon_days: int, untested_pool: int = 0) -> dict:
    spec = INDEX_SPECS[index_key]
    members = set(membership.tickers(index_key))
    member_caps = [universe[t]["market_cap"] for t in members
                   if t in universe and universe[t]["market_cap"] > 0]

    # Index-Sektorverteilung: GICS aus Wikipedia, sonst Yahoo
    idx_sectors: dict[str, int] = {}
    for t in members:
        s = membership.sectors(index_key).get(t) or (
            gics(fundamentals[t].sector) if t in fundamentals else "")
        if s:
            idx_sectors[s] = idx_sectors.get(s, 0) + 1
    index_sector_share = _share(idx_sectors)

    # --- Kandidatenpool ------------------------------------------------
    blocked = members | set(pending_additions)
    if index_key == "sp100":
        base_pool = set(membership.tickers("sp500")) - blocked
    else:
        lo = (spec.min_cap or 0) * 0.85
        hi = (spec.max_cap * 1.15) if spec.max_cap else float("inf")
        base_pool = {t for t, u in universe.items()
                     if t not in blocked and lo <= u["market_cap"] <= hi}

    def current_index_of(t: str) -> str:
        for k in ("sp500", "sp400", "sp600"):
            if k != index_key and t in membership.tickers(k):
                return k
        return EXTERNAL

    pool_sources: dict[str, int] = {}
    pool_sectors: dict[str, int] = {}
    for t in base_pool:
        pool_sources[current_index_of(t)] = pool_sources.get(current_index_of(t), 0) + 1
        f = fundamentals.get(t)
        s = gics(f.sector) if f else "Unbekannt"
        pool_sectors[s] = pool_sectors.get(s, 0) + 1
    pool_source_share = _share(pool_sources)
    pool_sector_share = _share(pool_sectors)

    def build_candidate(t: str) -> Candidate | None:
        f = fundamentals.get(t)
        if f is None:
            # Kein Tiefenprofil geladen (z. B. bestehende Indexmitglieder, fuer die
            # die Aufnahmekriterien ohnehin nicht erneut gelten): Stammdaten aus
            # dem Universum genuegen, fehlende Angaben werden als Luecke markiert.
            u = universe.get(t)
            if not u:
                return None
            f = Fundamentals(ticker=t, name=u.get("name", t),
                             market_cap=u.get("market_cap", 0.0),
                             exchange=u.get("exchange", ""),
                             quote_type=u.get("quote_type", ""),
                             sector=membership.sectors(index_key).get(t, "")
                             or membership.sectors("sp500").get(t, ""))
        cur_idx = current_index_of(t)
        gates = evaluate(f, spec, is_member=False,
                         is_migration=cur_idx in ("sp500", "sp400", "sp600"))
        if index_key == "sp100":
            # S&P-100-Kandidaten sind bereits S&P-500-Mitglieder: Groessenband
            # und Seasoning sind dort per Definition erfuellt.
            gates = [g for g in gates if g.key not in ("cap", "float_cap")]
        eligible, blockers, gaps = summarize(gates)
        cur = cur_idx

        cand = Candidate(
            ticker=t, name=f.name or universe.get(t, {}).get("name", t),
            market_cap=f.market_cap or universe.get(t, {}).get("market_cap", 0.0),
            sector=gics(f.sector), current_index=cur, status="kandidat",
            eligible=eligible, blockers=blockers, data_gaps=gaps,
            gates=[g.to_dict() for g in gates],
            liquidity_ratio=f.liquidity_ratio, iwf=f.iwf,
            profitable_quarters=f.profitable_quarters, days_since_ipo=f.days_since_ipo,
        )
        if eligible:
            cand.factors = [
                factor_cap_fit(f, spec, member_caps),
                factor_feeder(cur, calibration, pool_source_share),
                factor_swap_fit(f, member_caps),
                factor_liquidity(f),
                factor_float(f),
                factor_profitability(f),
                factor_sector_gap(f, index_sector_share, pool_sector_share),
                factor_seasoning(f),
                factor_history(t, index_key, recent_deletions, old_members),
                factor_quality(sum(1 for g in gates if g.state == "warn"),
                               sum(1 for g in gates if g.state == "unknown")),
            ]
            raw = 1.0
            for fac in cand.factors:
                raw *= fac.value
            # Temperierung: haelt die Rangfolge, daempft die Extreme des
            # multiplikativen Modells.
            cand.weight = raw ** SCORE_TEMPERING
            cand.raw_score = raw
        return cand

    candidates: list[Candidate] = []
    for t in sorted(base_pool):
        c = build_candidate(t)
        if c is not None:
            candidates.append(c)

    # --- Poisson-Kalibrierung -------------------------------------------
    # Nicht jeder Titel des Pools wird tief geprueft. Damit die Prozentwerte
    # nicht kuenstlich steigen, bekommt der ungeprueftе Rest ein geschaetztes
    # Restgewicht (konservativ: Median der schwaecheren Haelfte).
    weights = sorted(c.weight for c in candidates if c.weight > 0)
    eligible_rate = (len(weights) / len(candidates)) if candidates else 0.0
    # Die ungeprueften Titel sind per Vorauswahl die schwaechsten des Pools -
    # deshalb das 10. Perzentil der geprueften Gewichte als Schaetzer.
    typical_w = weights[max(0, int(0.10 * len(weights)))] if weights else 0.0
    residual_w = untested_pool * eligible_rate * typical_w
    total_w = sum(c.weight for c in candidates) + residual_w
    expected = calibration.adds_per_year * horizon_days / 365.0
    expected_q = calibration.adds_per_year * 91.0 / 365.0
    if total_w > 0:
        for c in candidates:
            share = c.weight / total_w
            c.probability = min(MAX_PROBABILITY, 1.0 - math.exp(-expected * share))
            c.probability_quarter = min(MAX_PROBABILITY,
                                        1.0 - math.exp(-expected_q * share))
            c.share_of_next_slot = share

    candidates.sort(key=lambda c: (-c.probability, -c.market_cap))
    for i, c in enumerate(candidates, 1):
        c.rank = i

    # --- Bereits angekuendigte Aufnahmen --------------------------------
    announced = []
    for t, eff in sorted(pending_additions.items(), key=lambda kv: kv[1]):
        f = fundamentals.get(t)
        u = universe.get(t, {})
        # Schattenbewertung: wo haette das Modell diesen Titel gefuehrt, bevor
        # S&P die Aufnahme bekanntgab? Das ist die ehrlichste Selbstkontrolle.
        shadow = build_candidate(t)
        shadow_rank, shadow_p = 0, 0.0
        if shadow is not None and shadow.weight > 0 and total_w > 0:
            shadow_rank = 1 + sum(1 for c in candidates if c.weight > shadow.weight)
            shadow_p = min(MAX_PROBABILITY,
                           1.0 - math.exp(-expected * shadow.weight / total_w))
        entry = Candidate(
            ticker=t, name=(f.name if f else u.get("name", t)),
            market_cap=(f.market_cap if f else u.get("market_cap", 0.0)),
            sector=gics(f.sector) if f else "",
            current_index=current_index_of(t) if t in universe else "",
            status="angekuendigt", eligible=True, probability=1.0, probability_quarter=1.0,
            effective_date=eff,
            note=f"Von S&P offiziell angekuendigt, wirksam zum {eff}.",
        ).to_dict()
        entry["model_rank"] = shadow_rank
        entry["model_probability"] = round(shadow_p, 4)
        entry["model_blockers"] = shadow.blockers if shadow else []
        announced.append(entry)

    leaving = [{"ticker": t, "effective_date": eff,
                "name": (fundamentals[t].name if t in fundamentals
                         else universe.get(t, {}).get("name", t))}
               for t, eff in sorted(pending_deletions.items(), key=lambda kv: kv[1])]

    return {
        "index": index_key,
        "label": spec.label,
        "notes": spec.notes,
        "members": len(members),
        "min_cap": spec.min_cap,
        "max_cap": spec.max_cap,
        "expected_additions": round(expected, 2),
        "expected_additions_quarter": round(expected_q, 2),
        "calibration": calibration.to_dict(),
        "pool_size": len(candidates),
        "untested_pool": untested_pool,
        "residual_weight_share": round(residual_w / total_w, 4) if total_w else 0.0,
        "eligible_count": sum(1 for c in candidates if c.eligible),
        "index_sector_share": {k: round(v, 4) for k, v in index_sector_share.items()},
        "pool_sector_share": {k: round(v, 4) for k, v in pool_sector_share.items()},
        "announced_additions": announced,
        "validation": {
            "checked": sum(1 for a in announced if a.get("model_rank")),
            "ranks": [a.get("model_rank") for a in announced if a.get("model_rank")],
            "in_top10": sum(1 for a in announced if 0 < (a.get("model_rank") or 0) <= 10),
            "in_top25": sum(1 for a in announced if 0 < (a.get("model_rank") or 0) <= 25),
            "missed": [a["ticker"] for a in announced if not a.get("model_rank")],
        },
        "announced_deletions": leaving,
        "candidates": [c.to_dict() for c in candidates],
    }
