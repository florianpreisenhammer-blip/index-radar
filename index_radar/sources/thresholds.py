"""Groessengrenzen direkt von S&P beziehen.

S&P ueberprueft die Marktkapitalisierungs-Grenzen fuer Aufnahmen in den
S&P Composite 1500 zu jedem Quartalsbeginn und passt sie bei Bedarf an
(Ziel: das 85.-93. Perzentil des S&P Total Market Index). Fest einprogrammierte
Werte veralten damit zwangslaeufig.

Die Methodologie-PDF ist nicht maschinell abrufbar (403), aber jede Aenderung
wird als Pressemeldung veroeffentlicht - dieselbe Quelle, aus der schon die
Indexaenderungen kommen. Gefunden wird die juengste Meldung
"Announces Update to S&P Composite 1500 Market Cap Guidelines"; daraus wird
der mit "Updated" ueberschriebene Block gelesen.
"""
from __future__ import annotations

import html as html_mod
import re
from dataclasses import asdict, dataclass
from datetime import datetime

from .. import cache
from ..util import get, log

LISTING = "https://press.spglobal.com/index.php?s=2429&l=100&o={offset}"
RELEASE_PATTERN = re.compile(r"Market-Cap(italization)?-Guidelines", re.I)
CACHE_KEY = "sp_market_cap_guidelines"
CACHE_TTL = 24 * 3600
LISTING_PAGES = 8          # rund drei Jahre Pressemeldungen

# "US$ 22.7 billion", "US$ 900 million", "US$ 1 billion"
AMOUNT = r"US\$\s*([\d.,]+)\s*(billion|million)"

RE_SP500 = re.compile(AMOUNT + r"\s*or more for the S&P 500", re.I)
RE_SP400 = re.compile(AMOUNT + r"\s*to\s*" + AMOUNT + r"\s*for the S&P MidCap 400", re.I)
RE_SP600 = re.compile(AMOUNT + r"\s*to\s*" + AMOUNT + r"\s*for the S&P SmallCap 600", re.I)
RE_EFFECTIVE = re.compile(r"Effective\s+([A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4})", re.I)


@dataclass
class Thresholds:
    sp500_min: float
    sp400_min: float
    sp400_max: float
    sp600_min: float
    sp600_max: float
    effective_date: str
    source_url: str
    source: str = "S&P-Pressemeldung"

    def to_dict(self) -> dict:
        return asdict(self)


def _amount(value: str, unit: str) -> float:
    number = float(value.replace(",", ""))
    return number * (1e9 if unit.lower() == "billion" else 1e6)


def _strip_html(raw: str) -> str:
    body = re.sub(r"<(script|style).*?</\1>", " ", raw, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", html_mod.unescape(body))


def _find_release() -> tuple[str, str] | None:
    """(Datum, URL) der juengsten Guidelines-Meldung."""
    hits: list[tuple[str, str]] = []
    for page in range(LISTING_PAGES):
        try:
            raw = get(LISTING.format(offset=page * 100), tries=2).text
        except Exception as exc:  # noqa: BLE001
            log.debug("Guidelines-Suche, Seite %d: %s", page + 1, exc)
            break
        found = re.findall(r'href="(https://press\.spglobal\.com/(20\d\d-\d\d-\d\d)-[^"]+)"', raw)
        if not found:
            break
        hits += [(d, u) for u, d in found if RELEASE_PATTERN.search(u)]
    return max(hits) if hits else None


def _parse(text: str, url: str, fallback_date: str) -> Thresholds | None:
    # Die Meldung stellt "Previous" und "Updated" nebeneinander - gueltig ist
    # jeweils der letzte Treffer.
    m500 = RE_SP500.findall(text)
    m400 = RE_SP400.findall(text)
    m600 = RE_SP600.findall(text)
    if not (m500 and m400 and m600):
        return None

    sp500_min = _amount(*m500[-1])
    sp400_min, sp400_max = _amount(*m400[-1][:2]), _amount(*m400[-1][2:])
    sp600_min, sp600_max = _amount(*m600[-1][:2]), _amount(*m600[-1][2:])

    effective = fallback_date
    m = RE_EFFECTIVE.search(text)
    if m:
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y"):
            try:
                effective = datetime.strptime(
                    re.sub(r"\s+", " ", m.group(1)).replace(".", ""), fmt).date().isoformat()
                break
            except ValueError:
                continue

    th = Thresholds(sp500_min=sp500_min, sp400_min=sp400_min, sp400_max=sp400_max,
                    sp600_min=sp600_min, sp600_max=sp600_max,
                    effective_date=effective, source_url=url)
    return th if is_plausible(th) else None


def is_plausible(th: Thresholds) -> bool:
    """Die Baender muessen luecken- und ueberschneidungsfrei aneinandergrenzen."""
    checks = (
        0.2e9 <= th.sp600_min < th.sp600_max,
        abs(th.sp600_max - th.sp400_min) < 1e6,      # SmallCap-Obergrenze = MidCap-Untergrenze
        th.sp400_min < th.sp400_max,
        abs(th.sp400_max - th.sp500_min) < 1e6,      # MidCap-Obergrenze = S&P-500-Untergrenze
        5e9 <= th.sp500_min <= 200e9,
    )
    if not all(checks):
        log.warning("Gelesene Groessengrenzen unplausibel: %s", th)
        return False
    return True


def fetch(use_cache: bool = True) -> Thresholds | None:
    if use_cache:
        cached = cache.kv_get(CACHE_KEY, CACHE_TTL)
        if cached:
            th = Thresholds(**cached)
            log.info("Groessengrenzen aus Cache (gueltig ab %s)", th.effective_date)
            return th

    hit = _find_release()
    if not hit:
        log.warning("Keine S&P-Meldung zu den Groessengrenzen gefunden")
        return None
    date, url = hit
    try:
        text = _strip_html(get(url, tries=2).text)
    except Exception as exc:  # noqa: BLE001
        log.warning("Guidelines-Meldung nicht ladbar: %s", exc)
        return None

    th = _parse(text, url, date)
    if th is None:
        log.warning("Groessengrenzen aus %s nicht lesbar", url)
        return None
    cache.kv_set(CACHE_KEY, th.to_dict())
    log.info("Groessengrenzen von S&P gelesen (gueltig ab %s): 500 ab %.1f Mrd., "
             "400 %.1f-%.1f Mrd., 600 %.1f-%.1f Mrd.",
             th.effective_date, th.sp500_min / 1e9, th.sp400_min / 1e9,
             th.sp400_max / 1e9, th.sp600_min / 1e9, th.sp600_max / 1e9)
    return th
