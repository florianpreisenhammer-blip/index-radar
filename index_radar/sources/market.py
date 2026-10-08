"""Marktdaten von Yahoo Finance: Universum (Screener) + Fundamentaldaten.

Jeder Lauf holt die Daten frisch - kein Cache, keine eingefrorene Kandidaten-
liste. Genau deshalb kann dem Dashboard nicht passieren, was dem Vorbild
passiert ist: ein Titel, der neu ueber die Schwelle waechst, ist beim naechsten
Start automatisch im Pool.
"""
from __future__ import annotations

import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yfinance as yf
from yfinance.data import YfData

from .. import cache
from ..config import (FUNDAMENTALS_TTL_SECONDS, MAX_WORKERS, UNIVERSE_MAX_ROWS,
                      UNIVERSE_MIN_CAP)
from ..util import clean_ticker, get, log

QS_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
QS_MODULES = ("defaultKeyStatistics,summaryProfile,summaryDetail,price,"
              "incomeStatementHistoryQuarterly,quoteType")

_yf_data: YfData | None = None


def _data() -> YfData:
    global _yf_data
    if _yf_data is None:
        _yf_data = YfData()
    return _yf_data


@dataclass
class Fundamentals:
    ticker: str
    name: str = ""
    market_cap: float = 0.0
    price: float = 0.0
    shares_outstanding: float = 0.0
    float_shares: float = 0.0
    avg_volume_3m: float = 0.0
    country: str = ""
    exchange: str = ""
    sector: str = ""
    industry: str = ""
    first_trade: str = ""          # ISO-Datum
    quote_type: str = ""
    quarterly_net_income: list[float] = field(default_factory=list)
    quarter_end_dates: list[str] = field(default_factory=list)
    error: str = ""

    def to_cache(self) -> dict:
        d = dict(self.__dict__)
        return d

    @classmethod
    def from_cache(cls, payload: dict) -> "Fundamentals":
        f = cls(ticker=payload.get("ticker", ""))
        for k, v in payload.items():
            if hasattr(f, k):
                setattr(f, k, v)
        return f

    # ---- abgeleitete Kennzahlen -------------------------------------
    @property
    def iwf(self) -> float:
        """Investable Weight Factor - Anteil Streubesitz."""
        if self.float_shares > 0 and self.shares_outstanding > 0:
            return min(1.0, self.float_shares / self.shares_outstanding)
        return 0.0

    @property
    def float_market_cap(self) -> float:
        if self.float_shares > 0 and self.price > 0:
            return self.float_shares * self.price
        return self.market_cap * self.iwf

    @property
    def liquidity_ratio(self) -> float:
        """Jahres-Dollarumsatz / Streubesitz-Marktkapitalisierung."""
        if self.float_shares > 0 and self.avg_volume_3m > 0:
            return (self.avg_volume_3m * 252.0) / self.float_shares
        return 0.0

    @property
    def net_income_ttm(self) -> float | None:
        vals = [v for v in self.quarterly_net_income[:4] if v is not None]
        return sum(vals) if len(vals) == 4 else None

    @property
    def net_income_latest(self) -> float | None:
        return self.quarterly_net_income[0] if self.quarterly_net_income else None

    @property
    def profitable_quarters(self) -> int:
        return sum(1 for v in self.quarterly_net_income[:4] if v is not None and v > 0)

    @property
    def days_since_ipo(self) -> float | None:
        if not self.first_trade:
            return None
        try:
            d = datetime.fromisoformat(self.first_trade).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return (datetime.now(timezone.utc) - d).days


# ---------------------------------------------------------------------------
# Universum
# ---------------------------------------------------------------------------
NASDAQ_TRADED = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt"


def fetch_listed_symbols() -> list[str]:
    """Alle regulaer gehandelten US-Aktien aus der offiziellen Nasdaq-Symboldatei.

    Dient als Rueckfallebene, wenn der Yahoo-Screener nicht antwortet.
    """
    rows = get(NASDAQ_TRADED).text.splitlines()
    if not rows or "Symbol" not in rows[0]:
        return []
    head = rows[0].split("|")
    idx = {name: i for i, name in enumerate(head)}
    out: list[str] = []
    for line in rows[1:]:
        parts = line.split("|")
        if len(parts) < len(head):
            continue
        if parts[idx.get("ETF", 5)] == "Y" or parts[idx.get("Test Issue", 7)] == "Y":
            continue
        if parts[idx.get("Listing Exchange", 3)] not in ("N", "A", "P", "Z", "Q"):
            continue
        sym = clean_ticker(parts[idx.get("Symbol", 1)])
        # Vorzugsaktien, Warrants, Units und Rechte tragen Suffixe
        if not sym or len(sym) > 5 or "-" in sym:
            continue
        out.append(sym)
    log.info("Nasdaq-Symbolliste: %d handelbare US-Aktien", len(out))
    return sorted(set(out))


def fetch_universe(min_cap: float = UNIVERSE_MIN_CAP,
                   max_rows: int = UNIVERSE_MAX_ROWS) -> dict[str, dict]:
    """Alle US-gelisteten Aktien oberhalb einer Marktkapitalisierung."""
    query = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["region", "us"]),
        yf.EquityQuery("gt", ["intradaymarketcap", min_cap]),
    ])
    out: dict[str, dict] = {}
    offset, page_size = 0, 250
    total = None
    while offset < max_rows:
        res, err = None, None
        for attempt in range(4):
            try:
                res = yf.screen(query, sortField="intradaymarketcap", sortAsc=False,
                                size=page_size, offset=offset)
                break
            except Exception as exc:  # noqa: BLE001
                err = exc
                time.sleep(2 ** attempt * 2)
        if res is None:
            log.warning("Screener-Seite bei Offset %d fehlgeschlagen: %s", offset, err)
            break
        quotes = res.get("quotes") or []
        total = res.get("total", total)
        if not quotes:
            break
        for q in quotes:
            tk = clean_ticker(q.get("symbol"))
            cap = q.get("marketCap")
            if not tk or not cap:
                continue
            out.setdefault(tk, {
                "ticker": tk,
                "name": q.get("shortName") or q.get("longName") or tk,
                "market_cap": float(cap),
                "quote_type": q.get("quoteType", ""),
                "exchange": q.get("fullExchangeName") or q.get("exchange", ""),
            })
        offset += page_size
        if total is not None and offset >= total:
            break
    log.info("Universum: %d US-Titel ueber %.1f Mrd. USD (Yahoo meldet %s gesamt)",
             len(out), min_cap / 1e9, total)
    return out


# ---------------------------------------------------------------------------
# Fundamentaldaten
# ---------------------------------------------------------------------------
def _num(node: dict | None, key: str) -> float:
    if not node:
        return 0.0
    v = node.get(key)
    if isinstance(v, dict):
        v = v.get("raw")
    try:
        f = float(v)
        return 0.0 if math.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


def fetch_fundamentals(ticker: str, tries: int = 3) -> Fundamentals:
    f = Fundamentals(ticker=ticker)
    r = None
    for attempt in range(tries):
        try:
            raw = _data().get_raw_json(QS_URL.format(sym=ticker),
                                       params={"modules": QS_MODULES}, timeout=25)
            results = (raw.get("quoteSummary") or {}).get("result") or []
            if not results:
                f.error = "keine Daten"
                return f
            r = results[0]
            break
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            f.error = name
            if "RateLimit" in name or "429" in str(exc):
                # Yahoo drosselt - kurz warten statt den Titel zu verlieren
                time.sleep(4 * (attempt + 1) + random.random() * 2)
                continue
            return f
    if r is None:
        return f
    f.error = ""

    price = r.get("price") or {}
    ks = r.get("defaultKeyStatistics") or {}
    prof = r.get("summaryProfile") or {}
    det = r.get("summaryDetail") or {}
    qt = r.get("quoteType") or {}

    f.name = price.get("longName") or price.get("shortName") or ticker
    f.market_cap = _num(price, "marketCap")
    f.price = _num(price, "regularMarketPrice")
    f.shares_outstanding = _num(price, "sharesOutstanding") or _num(ks, "sharesOutstanding")
    f.float_shares = _num(ks, "floatShares")
    f.avg_volume_3m = _num(det, "averageVolume") or _num(det, "averageVolume10days")
    f.country = (prof.get("country") or "").strip()
    f.exchange = price.get("exchangeName") or price.get("exchange") or ""
    f.sector = (prof.get("sector") or "").strip()
    f.industry = (prof.get("industry") or "").strip()
    f.quote_type = price.get("quoteType") or qt.get("quoteType") or ""

    ft = qt.get("firstTradeDateEpochUtc")
    if ft:
        try:
            f.first_trade = datetime.fromtimestamp(int(ft), tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            pass

    hist = (r.get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory") or []
    for q in hist[:5]:
        ni = q.get("netIncome")
        ni = ni.get("raw") if isinstance(ni, dict) else ni
        f.quarterly_net_income.append(float(ni) if ni is not None else None)
        end = q.get("endDate")
        f.quarter_end_dates.append((end or {}).get("fmt", "") if isinstance(end, dict) else "")
    return f


def fetch_fundamentals_bulk(tickers: list[str], workers: int = MAX_WORKERS,
                            progress=None, ttl: float = FUNDAMENTALS_TTL_SECONDS,
                            ) -> tuple[dict[str, Fundamentals], dict]:
    cached_raw = cache.load(tickers, ttl)
    out: dict[str, Fundamentals] = {t: Fundamentals.from_cache(p)
                                    for t, p in cached_raw.items()}
    todo = [t for t in tickers if t not in out]
    log.info("Fundamentaldaten: %d aus Cache, %d werden geladen", len(out), len(todo))

    fresh: dict[str, Fundamentals] = {}
    done, rate_limited = 0, 0
    if todo:
        with ThreadPoolExecutor(workers) as ex:
            for fund in ex.map(fetch_fundamentals, todo):
                if "RateLimit" in fund.error:
                    rate_limited += 1
                elif not fund.error:
                    fresh[fund.ticker] = fund
                out[fund.ticker] = fund
                done += 1
                if progress and done % 100 == 0:
                    progress(done, len(todo))
    cache.store({t: f.to_cache() for t, f in fresh.items()})

    errors = sum(1 for f in out.values() if f.error)
    if rate_limited:
        log.warning("Yahoo hat %d Abfragen gedrosselt - diese Titel sind mit "
                    "Datenluecke markiert", rate_limited)
    log.info("Fundamentaldaten: %d Titel verfuegbar (%d ohne Daten)", len(out), errors)
    stats = {"from_cache": len(cached_raw), "fetched": len(fresh),
             "failed": errors, "rate_limited": rate_limited}
    return out, stats


QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"


def fetch_quotes_bulk(symbols: list[str], chunk: int = 50) -> dict[str, dict]:
    """Marktkapitalisierung fuer viele Ticker in Sammelabfragen."""
    chunks = [symbols[i:i + chunk] for i in range(0, len(symbols), chunk)]

    def one(batch: list[str]) -> list[dict]:
        for attempt in range(3):
            try:
                raw = _data().get_raw_json(QUOTE_URL, params={"symbols": ",".join(batch)},
                                           timeout=25)
                return (raw.get("quoteResponse") or {}).get("result") or []
            except Exception as exc:  # noqa: BLE001
                if "RateLimit" in type(exc).__name__ or "429" in str(exc):
                    time.sleep(4 * (attempt + 1) + random.random() * 2)
                    continue
                break
        return []

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max(2, MAX_WORKERS // 2)) as ex:
        for res in ex.map(one, chunks):
            if not res:
                continue
            for q in res:
                tk = clean_ticker(q.get("symbol"))
                cap = q.get("marketCap")
                if not tk or not cap:
                    continue
                out[tk] = {
                    "ticker": tk,
                    "name": q.get("shortName") or q.get("longName") or tk,
                    "market_cap": float(cap),
                    "quote_type": q.get("quoteType", ""),
                    "exchange": q.get("fullExchangeName") or q.get("exchange", ""),
                    # Bilanzwaehrung trennt US-Unternehmen guenstig von
                    # ADRs auslaendischer Konzerne, ohne teure Einzelabfrage.
                    "financial_currency": q.get("financialCurrency", ""),
                }
    return out


def enrich_currency(universe: dict[str, dict], tickers: list[str]) -> int:
    """Bilanzwaehrung per Sammelabfrage nachladen (Vorfilter gegen ADRs)."""
    todo = [t for t in tickers if not universe.get(t, {}).get("financial_currency")]
    if not todo:
        return 0
    for tk, row in fetch_quotes_bulk(todo).items():
        if tk in universe:
            universe[tk]["financial_currency"] = row.get("financial_currency", "")
        else:
            universe[tk] = row
    hits = sum(1 for t in todo if universe.get(t, {}).get("financial_currency"))
    log.info("Bilanzwaehrung fuer %d von %d Titeln ermittelt", hits, len(todo))
    return hits


def build_universe(min_cap: float = UNIVERSE_MIN_CAP,
                   required_tickers: set[str] | None = None) -> tuple[dict[str, dict], str]:
    """Universum aus dem Yahoo-Screener, mit Rueckfall auf die Nasdaq-Symboldatei."""
    source = "Yahoo-Screener"
    try:
        universe = fetch_universe(min_cap)
    except Exception as exc:  # noqa: BLE001
        log.warning("Screener nicht verfuegbar: %s", exc)
        universe = {}

    if len(universe) < 1500:
        log.info("Screener lieferte nur %d Titel - nutze die Nasdaq-Symbolliste",
                 len(universe))
        try:
            symbols = fetch_listed_symbols()
        except Exception as exc:  # noqa: BLE001
            symbols = []
            log.warning("Nasdaq-Symbolliste nicht ladbar: %s", exc)
        if symbols:
            quotes = fetch_quotes_bulk(symbols)
            for tk, row in quotes.items():
                if row["market_cap"] >= min_cap:
                    universe.setdefault(tk, row)
            source = ("Yahoo-Screener + Nasdaq-Symbolliste" if universe else
                      "Nasdaq-Symbolliste")
            log.info("Universum nach Rueckfall: %d Titel", len(universe))

    missing = sorted((required_tickers or set()) - set(universe))
    if missing:
        for tk, row in fetch_quotes_bulk(missing).items():
            universe.setdefault(tk, row)
    if len(universe) < 500:
        raise RuntimeError(
            f"Marktuniversum unvollstaendig ({len(universe)} Titel). Yahoo Finance "
            f"drosselt die Abfragen gerade - bitte in einigen Minuten erneut starten.")
    return universe, source
