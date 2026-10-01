"""Zentrale Konfiguration: S&P-Aufnahmekriterien und Modellparameter.

Alle Zahlen stammen aus der offiziellen "S&P U.S. Indices Methodology"
(S&P Dow Jones Indices). Die Marktkapitalisierungs-Baender werden von S&P
quartalsweise ueberprueft (Ziel: 85.-93. Perzentil des S&P Total Market Index),
darum sind sie hier zentral und datiert hinterlegt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Marktkapitalisierungs-Baender (Stand der Methodologie: Juli 2026)
# Quelle: S&P U.S. Indices Methodology; Update der Guidelines per 01.07.2025.
# --------------------------------------------------------------------------
THRESHOLDS_ASOF = "2026-07"
SP500_MIN_CAP = 22.7e9
SP400_MIN_CAP = 8.0e9
SP400_MAX_CAP = 22.7e9
SP600_MIN_CAP = 1.2e9
SP600_MAX_CAP = 8.0e9

# Gemeinsame Kriterien fuer alle drei Indizes des S&P Composite 1500
MIN_IWF = 0.50              # mind. 50 % Streubesitz
MIN_LIQUIDITY_RATIO = 0.75  # Jahres-Dollarumsatz / Streubesitz-Marktkap.
MIN_MONTHLY_SHARES = 250_000
MIN_SEASONING_DAYS = 365    # mind. 12 Monate seit IPO (Regel seit 2023)

# Boersen, die S&P als "eligible" fuehrt (Yahoo-Namen)
ELIGIBLE_EXCHANGES = {
    "NYSE", "NYSEArca", "NYSE MKT", "NYSE American", "NYSEAmerican",
    "NasdaqGS", "NasdaqGM", "NasdaqCM", "NMS", "NGM", "NCM", "NYQ", "ASE",
    "Cboe BZX", "BATS", "IEX", "LTSE", "NYSE Texas",
}

# Yahoo liefert bei Fonds/ADR/Trusts andere quoteTypes - nur echte Aktien.
ELIGIBLE_QUOTE_TYPES = {"EQUITY"}

# Rechtsformen, die S&P ausschliesst (Erkennung ueber den Firmennamen)
EXCLUDED_NAME_PATTERNS = (
    " l.p.", " lp", "limited partnership", "trust ", " trust", "etf",
    "fund", "acquisition corp", "spac",
)

# --------------------------------------------------------------------------
# Kandidaten-Universum
# --------------------------------------------------------------------------
UNIVERSE_MIN_CAP = 0.9e9     # unterhalb davon ist selbst der 600er unerreichbar
UNIVERSE_MAX_ROWS = 8000
BAND_TOLERANCE = 0.85        # Kandidaten ab 85 % der Untergrenze mitfuehren
                             # (Grenzfaelle sollen sichtbar sein, nicht verschwinden)

# --------------------------------------------------------------------------
# Modellparameter
# --------------------------------------------------------------------------
# Die Groessenbaender sind Richtwerte, keine harte Grenze: S&P behaelt sich
# ausdruecklich Ermessen vor und nimmt regelmaessig Titel knapp unterhalb der
# Mindestgroesse auf. Deshalb ist die Groesse ein weicher Faktor - hart
# ausgeschlossen wird erst, wer weit ausserhalb liegt.
SOFT_CAP_FLOOR = 0.45        # unter 45 % der Mindestgroesse -> kein Kandidat
SOFT_CAP_CEILING = 1.9       # ueber 190 % der Bandobergrenze -> kein Kandidat

# Temperierung des multiplikativen Scores. Ohne sie multiplizieren sich neun
# Faktoren zu extremen Spreads; 0,55 haelt die Rangfolge, daempft die Spitzen.
SCORE_TEMPERING = 0.55

# Tiefenanalyse: wie viele Kandidaten je Index vollstaendig geprueft werden.
# Der Rest des Pools geht als geschaetztes Restgewicht in die Normierung ein,
# damit die Wahrscheinlichkeiten nicht kuenstlich aufgeblaeht werden.
DEPTH_PER_INDEX = {"sp500": 700, "sp100": 0, "sp400": 700, "sp600": 900}

# Fundamentaldaten (Quartalsgewinne, Streubesitz, IPO-Datum) aendern sich
# quartalsweise - 12 Stunden Cache sparen tausende Abfragen.
FUNDAMENTALS_TTL_SECONDS = 12 * 3600
MAX_PROBABILITY = 0.90       # ohne offizielle Ankuendigung nie "so gut wie sicher"
HORIZON_DAYS = 365           # Haupt-Prognosehorizont
BASE_RATE_YEARS = 3          # Zeitfenster fuer die empirische Aufnahmerate
BASE_RATE_MIN_YEARS = 1.0

# Fallback-Aufnahmeraten pro Jahr, falls die Historie nicht ladbar ist.
FALLBACK_ADD_RATE = {"sp500": 22.0, "sp400": 40.0, "sp600": 50.0, "sp100": 6.0}


@dataclass(frozen=True)
class IndexSpec:
    key: str
    label: str
    min_cap: float | None
    max_cap: float | None
    # Aus welchen Indizes rekrutiert sich der Kandidatenpool bevorzugt?
    feeder_indices: tuple[str, ...] = ()
    # Bell-Kurve (Bandmitte bevorzugt) statt "je groesser desto besser"?
    prefers_band_center: bool = False
    wiki_page: str = ""
    wiki_history_page: str = ""
    sp_name: str = ""
    members_target: int = 0
    notes: str = ""


INDEX_SPECS: dict[str, IndexSpec] = {
    "sp500": IndexSpec(
        key="sp500", label="S&P 500", min_cap=SP500_MIN_CAP, max_cap=None,
        feeder_indices=("sp400", "sp600"), prefers_band_center=False,
        wiki_page="List_of_S%26P_500_companies",
        wiki_history_page="Historical_components_of_the_S%26P_500",
        sp_name="S&P 500", members_target=503,
        notes="Large Cap. Aufnahmen kommen mehrheitlich per Aufstieg aus dem MidCap 400.",
    ),
    "sp100": IndexSpec(
        key="sp100", label="S&P 100", min_cap=None, max_cap=None,
        feeder_indices=("sp500",), prefers_band_center=False,
        wiki_page="S%26P_100", wiki_history_page="",
        sp_name="S&P 100", members_target=101,
        notes="Teilmenge des S&P 500: die groessten, etabliertesten Titel mit liquidem Optionsmarkt.",
    ),
    "sp400": IndexSpec(
        key="sp400", label="S&P MidCap 400", min_cap=SP400_MIN_CAP, max_cap=SP400_MAX_CAP,
        feeder_indices=("sp600",), prefers_band_center=True,
        wiki_page="List_of_S%26P_400_companies", wiki_history_page="List_of_S%26P_400_companies",
        sp_name="S&P MidCap 400", members_target=400,
        notes="Mid Cap. Zufluss aus Aufsteigern des SmallCap 600, IPOs und Absteigern aus dem S&P 500.",
    ),
    "sp600": IndexSpec(
        key="sp600", label="S&P SmallCap 600", min_cap=SP600_MIN_CAP, max_cap=SP600_MAX_CAP,
        feeder_indices=(), prefers_band_center=True,
        wiki_page="List_of_S%26P_600_companies", wiki_history_page="List_of_S%26P_600_companies",
        sp_name="S&P SmallCap 600", members_target=603,
        notes="Small Cap. Groesster Zufluss aus dem nicht-indexierten Universum und von Absteigern.",
    ),
}

INDEX_ORDER = ("sp500", "sp100", "sp400", "sp600")

# Gewichte der Score-Faktoren (multiplikativ, in Log-Raum interpretierbar).
# Empirisch kalibriert wird nur FEEDER_MULTIPLIER (aus der Aenderungshistorie);
# der Rest folgt direkt den Methodologie-Kriterien.
FACTOR_CAPS = {
    "cap_fit": (0.10, 3.0),
    "feeder": (0.25, 6.0),
    "liquidity": (0.6, 1.6),
    "float": (0.7, 1.3),
    "profitability": (0.5, 1.4),
    "sector_gap": (0.7, 1.45),
    "seasoning": (0.4, 1.1),
    "swap_fit": (0.7, 1.6),
    "history": (0.9, 1.25),
}

USER_AGENT = "IndexRadar/1.0 (+personal research tool)"
HTTP_TIMEOUT = 30
MAX_WORKERS = 5
