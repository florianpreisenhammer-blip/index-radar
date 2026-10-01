"""Harte Aufnahmekriterien nach S&P-Methodologie.

Jede Pruefung liefert einen von drei Zustaenden:
  pass    - Kriterium erfuellt
  fail    - Kriterium verletzt (Aufnahme derzeit ausgeschlossen)
  unknown - Datenlage unklar (wird transparent ausgewiesen, nicht verschwiegen)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import (ELIGIBLE_EXCHANGES, ELIGIBLE_QUOTE_TYPES, EXCLUDED_NAME_PATTERNS,
                     MIN_IWF, MIN_LIQUIDITY_RATIO, MIN_SEASONING_DAYS, SOFT_CAP_CEILING,
                     SOFT_CAP_FLOOR, IndexSpec)
from .sources.market import Fundamentals

PASS, FAIL, UNKNOWN, WARN = "pass", "fail", "unknown", "warn"


@dataclass
class Gate:
    key: str
    label: str
    state: str
    detail: str

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "state": self.state, "detail": self.detail}


def _de(value: float, digits: int = 1) -> str:
    """Deutsche Zahlenschreibweise: Punkt als Tausender-, Komma als Dezimaltrenner."""
    return f"{value:,.{digits}f}".replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def _fmt_cap(v: float) -> str:
    return f"{_de(v / 1e9)} Mrd. $"


def _exchange_ok(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if n in ELIGIBLE_EXCHANGES:
        return True
    low = n.lower()
    return any(tok in low for tok in ("nyse", "nasdaq", "cboe", "iex", "bats", "arca"))


# Wechselt ein Titel innerhalb des S&P Composite 1500 den Index, greifen die
# Aufnahmekriterien laut Methodologie nicht erneut - er ist ja bereits geprueftes
# Indexmitglied. Entscheidend ist dann nur noch die Groesseneinordnung.
MIGRATION_EXEMPT = {"earnings", "liquidity", "seasoning", "float", "float_cap",
                    "domicile", "domicile_form", "type", "exchange"}


def evaluate(f: Fundamentals, spec: IndexSpec, *, is_member: bool = False,
             is_migration: bool = False) -> list[Gate]:
    g: list[Gate] = []

    # --- Domizil -------------------------------------------------------
    if not f.country:
        g.append(Gate("domicile", "US-Domizil", UNKNOWN, "Land unbekannt"))
    elif f.country == "United States":
        g.append(Gate("domicile", "US-Domizil", PASS, "United States"))
    else:
        g.append(Gate("domicile", "US-Domizil", FAIL, f.country))

    # --- Boerse --------------------------------------------------------
    g.append(Gate("exchange", "Zugelassene US-Boerse",
                  PASS if _exchange_ok(f.exchange) else (UNKNOWN if not f.exchange else FAIL),
                  f.exchange or "unbekannt"))

    # --- Wertpapierart -------------------------------------------------
    name_low = (f.name or "").lower()
    bad_form = any(p in name_low for p in EXCLUDED_NAME_PATTERNS)
    if f.quote_type and f.quote_type not in ELIGIBLE_QUOTE_TYPES:
        g.append(Gate("type", "Stammaktie (kein Fonds/LP/Trust)", FAIL, f.quote_type))
    elif bad_form:
        g.append(Gate("type", "Stammaktie (kein Fonds/LP/Trust)", UNKNOWN,
                      "Rechtsform laut Name pruefen"))
    else:
        g.append(Gate("type", "Stammaktie (kein Fonds/LP/Trust)", PASS, f.quote_type or "Equity"))

    # --- Seasoning -----------------------------------------------------
    d = f.days_since_ipo
    if d is None:
        g.append(Gate("seasoning", "Mind. 12 Monate seit Boersengang", UNKNOWN, "IPO-Datum fehlt"))
    elif d >= MIN_SEASONING_DAYS:
        g.append(Gate("seasoning", "Mind. 12 Monate seit Boersengang", PASS,
                      f"seit {f.first_trade} ({d // 365} J.)"))
    else:
        g.append(Gate("seasoning", "Mind. 12 Monate seit Boersengang", FAIL,
                      f"erst {d} Tage (seit {f.first_trade})"))

    # --- Marktkapitalisierung ------------------------------------------
    cap = f.market_cap
    if cap <= 0:
        g.append(Gate("cap", "Marktkapitalisierung im Zielband", UNKNOWN, "keine Daten"))
    else:
        lo, hi = spec.min_cap, spec.max_cap
        band = (f"ab {_fmt_cap(lo)}" if lo and not hi else
                f"{_fmt_cap(lo)} - {_fmt_cap(hi)}" if lo and hi else "kein festes Band")
        if lo is None:
            g.append(Gate("cap", "Marktkapitalisierung im Zielband", PASS, _fmt_cap(cap)))
        elif cap < lo * SOFT_CAP_FLOOR:
            g.append(Gate("cap", "Marktkapitalisierung im Zielband", FAIL,
                          f"{_fmt_cap(cap)} - weit unter dem Richtwert ({band})"))
        elif hi and cap > hi * SOFT_CAP_CEILING:
            g.append(Gate("cap", "Marktkapitalisierung im Zielband", FAIL,
                          f"{_fmt_cap(cap)} - weit oberhalb des Bandes ({band})"))
        elif cap < lo:
            g.append(Gate("cap", "Marktkapitalisierung im Zielband", WARN,
                          f"{_fmt_cap(cap)} - unter dem Richtwert {band}; S&P nimmt "
                          f"solche Titel nur in Ausnahmefaellen auf"))
        elif hi and cap > hi:
            g.append(Gate("cap", "Marktkapitalisierung im Zielband", WARN,
                          f"{_fmt_cap(cap)} - oberhalb von {band}; eher Aufstiegs- "
                          f"als Aufnahmekandidat"))
        else:
            g.append(Gate("cap", "Marktkapitalisierung im Zielband", PASS,
                          f"{_fmt_cap(cap)} (Band {band})"))

    # --- Streubesitz ----------------------------------------------------
    iwf = f.iwf
    if iwf <= 0:
        g.append(Gate("float", "Streubesitz mind. 50 %", UNKNOWN, "Float-Daten fehlen"))
    elif iwf >= MIN_IWF:
        g.append(Gate("float", "Streubesitz mind. 50 %", PASS, f"{iwf:.0%}"))
    else:
        g.append(Gate("float", "Streubesitz mind. 50 %", FAIL, f"nur {iwf:.0%}"))

    # --- Streubesitz-Marktkapitalisierung -------------------------------
    if spec.min_cap:
        fmc = f.float_market_cap
        need = 0.5 * spec.min_cap
        if fmc <= 0:
            g.append(Gate("float_cap", "Streubesitz-Marktkap. > 50 % der Mindestgroesse",
                          UNKNOWN, "keine Daten"))
        else:
            g.append(Gate("float_cap", "Streubesitz-Marktkap. > 50 % der Mindestgroesse",
                          PASS if fmc >= need else (WARN if fmc >= 0.5 * need else FAIL),
                          f"{_fmt_cap(fmc)} (Richtwert {_fmt_cap(need)})"))

    # --- Liquiditaet -----------------------------------------------------
    lr = f.liquidity_ratio
    if lr <= 0:
        g.append(Gate("liquidity", "Liquiditaetsquote mind. 0,75", UNKNOWN, "Volumendaten fehlen"))
    else:
        g.append(Gate("liquidity", "Liquiditaetsquote mind. 0,75",
                      PASS if lr >= MIN_LIQUIDITY_RATIO else FAIL, _de(lr, 2)))

    # --- Gewinn (GAAP) ---------------------------------------------------
    ttm, last = f.net_income_ttm, f.net_income_latest
    if ttm is None or last is None:
        g.append(Gate("earnings", "Positiver Gewinn: letztes Quartal und Summe 4 Quartale",
                      UNKNOWN, "Quartalszahlen fehlen"))
    else:
        ok = last > 0 and ttm > 0
        g.append(Gate("earnings", "Positiver Gewinn: letztes Quartal und Summe 4 Quartale",
                      PASS if ok else FAIL,
                      f"letztes Quartal {_de(last / 1e6, 0)} Mio. $ | "
                      f"vier Quartale {_de(ttm / 1e6, 0)} Mio. $"))

    # --- Auslaendische Rechtsform ---------------------------------------
    foreign_form = re.search(r"\b(plc|n\.?v\.?|s\.?a\.?|a\.?g\.?|ltd|limited|sa|nv)\b",
                             name_low)
    if foreign_form and f.country == "United States":
        g.append(Gate("domicile_form", "Rechtsform deutet auf US-Inkorporation", WARN,
                      "Name deutet auf auslaendische Inkorporation - S&P prueft das "
                      "Domizil strenger als die Adresse"))

    if is_migration:
        g = [Gate(x.key, x.label,
                  WARN if (x.state in (FAIL, UNKNOWN) and x.key in MIGRATION_EXEMPT) else x.state,
                  (x.detail + " - entfaellt beim Wechsel innerhalb des S&P 1500"
                   if x.state in (FAIL, UNKNOWN) and x.key in MIGRATION_EXEMPT else x.detail))
             for x in g]

    if is_member:
        g.append(Gate("membership", "Noch kein Mitglied", FAIL, "bereits im Index"))
    return g


def summarize(gates: list[Gate]) -> tuple[bool, list[str], list[str]]:
    """(eligible, Blocker, Datenluecken)"""
    fails = [f"{g.label}: {g.detail}" for g in gates if g.state == FAIL]
    unknown = [f"{g.label}: {g.detail}" for g in gates if g.state in (UNKNOWN, WARN)]
    return (not fails), fails, unknown
