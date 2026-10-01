"""Index-Aenderungen direkt aus den Pressemitteilungen von S&P Dow Jones Indices.

Das ist die wichtigste Quelle des Dashboards. S&P kuendigt Aufnahmen typischer-
weise 3-10 Tage vor dem Wirksamwerden an. Genau in diesem Fenster produzierte
das Vorbild-Dashboard seine Fehler: Bloom Energy war laengst angekuendigt und
wurde trotzdem als "Kandidat mit X % Wahrscheinlichkeit" gefuehrt, Illumina
tauchte nie auf.

Wir nutzen die Pressemeldungen doppelt:
  1. Pending Changes  -> bereits angekuendigte Titel werden aus der Prognose
     genommen und separat als "fix" ausgewiesen.
  2. Historie         -> empirische Aufnahmeraten pro Index und die Frage,
     aus welchem Index Neuzugaenge tatsaechlich kommen (Feeder-Kalibrierung).
"""
from __future__ import annotations

import html as html_mod
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from ..config import MAX_WORKERS
from ..util import clean_ticker, get, log

LISTING = "https://press.spglobal.com/index.php?s=2429&l=100&o={offset}"

# Nur Meldungen, die ueberhaupt Indexaenderungen betreffen.
RELEVANT_URL = re.compile(r"(Set-to-Join|-to-Join-|Will-Replace|-Replace-|Join-S-P)", re.I)

GICS_SECTORS = (
    "Information Technology", "Health Care", "Financials", "Industrials",
    "Consumer Discretionary", "Consumer Staples", "Communication Services",
    "Energy", "Materials", "Real Estate", "Utilities",
)

INDEX_BY_SP_NAME = {
    "S&P 500": "sp500",
    "S&P 100": "sp100",
    "S&P MidCap 400": "sp400",
    "S&P SmallCap 600": "sp600",
}

ROW_RE = re.compile(
    r"(?P<date>[A-Z][a-z]{2,8}\.? ?\d{1,2},? ?\d{4})\s+"
    r"(?P<index>S&P (?:500|100|MidCap 400|SmallCap 600))\s+"
    r"(?P<action>Addition|Deletion)\s+"
    r"(?P<name>.{2,60}?)\s+"
    r"(?P<ticker>[A-Z][A-Z\.\-]{0,5})\s+"
    r"(?P<sector>" + "|".join(GICS_SECTORS) + r")"
)

DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%b. %d, %Y", "%b %d %Y", "%B %d %Y")


@dataclass
class IndexChange:
    index: str            # sp500 | sp100 | sp400 | sp600
    action: str           # addition | deletion
    ticker: str
    company: str
    sector: str
    effective_date: str   # ISO
    announced_date: str   # ISO
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_date(raw: str) -> str | None:
    raw = raw.replace("Sept.", "Sep").replace("Sept ", "Sep ").replace("Sept,", "Sep,")
    raw = re.sub(r"\s+", " ", raw).strip().rstrip(",")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _strip_html(raw: str) -> str:
    body = re.sub(r"<(script|style).*?</\1>", " ", raw, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", html_mod.unescape(body))


def list_releases(pages: int = 2) -> list[tuple[str, str]]:
    """(announced_date_iso, url) aller Pressemeldungen, neueste zuerst."""
    seen: dict[str, str] = {}
    for page in range(pages):
        try:
            raw = get(LISTING.format(offset=page * 100)).text
        except Exception as exc:  # noqa: BLE001
            log.warning("Pressemeldungs-Liste Seite %d nicht erreichbar: %s", page + 1, exc)
            break
        found = re.findall(r'href="(https://press\.spglobal\.com/(20\d\d)-(\d\d)-(\d\d)-[^"]+)"', raw)
        if not found:
            break
        for url, y, m, d in found:
            seen.setdefault(url, f"{y}-{m}-{d}")
    return sorted(((d, u) for u, d in seen.items()), reverse=True)


def parse_release(url: str, announced: str) -> list[IndexChange]:
    try:
        text = _strip_html(get(url, tries=2).text)
    except Exception as exc:  # noqa: BLE001
        log.debug("Pressemeldung nicht ladbar (%s): %s", url, exc)
        return []

    out: list[IndexChange] = []
    seen: set[tuple] = set()
    for m in ROW_RE.finditer(text):
        eff = _parse_date(m.group("date"))
        idx = INDEX_BY_SP_NAME.get(m.group("index"))
        if not eff or not idx:
            continue
        ticker = clean_ticker(m.group("ticker"))
        if not ticker:
            continue
        key = (idx, m.group("action"), ticker, eff)
        if key in seen:
            continue
        seen.add(key)
        out.append(IndexChange(
            index=idx,
            action=m.group("action").lower(),
            ticker=ticker,
            company=m.group("name").strip(" .,-"),
            sector=m.group("sector"),
            effective_date=eff,
            announced_date=announced,
            url=url,
        ))
    return out


def fetch_changes(lookback_days: int = 400, pages: int = 3) -> list[IndexChange]:
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    releases = [(d, u) for d, u in list_releases(pages)
                if d >= cutoff and RELEVANT_URL.search(u)]
    log.info("S&P-Pressemeldungen: %d Index-Meldungen seit %s", len(releases), cutoff)
    changes: list[IndexChange] = []
    with ThreadPoolExecutor(MAX_WORKERS) as ex:
        for part in ex.map(lambda t: parse_release(t[1], t[0]), releases):
            changes.extend(part)
    changes.sort(key=lambda c: (c.effective_date, c.index, c.action), reverse=True)
    log.info("  -> %d einzelne Indexaenderungen geparst", len(changes))
    return changes


def split_pending(changes: list[IndexChange], today: date | None = None):
    """Angekuendigt-aber-noch-nicht-wirksam vs. bereits wirksam."""
    today = today or date.today()
    iso = today.isoformat()
    pending = [c for c in changes if c.effective_date >= iso]
    effective = [c for c in changes if c.effective_date < iso]
    return pending, effective
