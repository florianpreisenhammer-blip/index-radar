"""Einstiegspunkt.

  python3 -m index_radar                 Dashboard starten (rechnet beim Start neu)
  python3 -m index_radar --no-open       ohne Browser
  python3 -m index_radar --json          nur neu berechnen und Snapshot schreiben
  python3 -m index_radar --fresh         Fundamentaldaten-Cache ignorieren
  python3 -m index_radar --top 15        Top-Kandidaten im Terminal ausgeben
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import cache, export_site, pipeline, server, validate
from .config import HORIZON_DAYS
from .util import setup_logging


def print_top(snapshot: dict, n: int) -> None:
    for idx in snapshot["indices"]:
        print(f"\n=== {idx['label']} "
              f"({idx['members']} Mitglieder, erwartete Aufnahmen in "
              f"{snapshot['horizon_days']} Tagen: {idx['expected_additions']}) ===")
        for a in idx["announced_additions"]:
            print(f"  FIX     {a['ticker']:6} {a['name'][:34]:36} "
                  f"angekuendigt, wirksam {a['effective_date']}")
        labels = {"sp500": "aus S&P 500", "sp400": "aus MidCap 400",
                  "sp600": "aus SmallCap 600"}
        for c in idx["candidates"][:n]:
            origin = labels.get(c["current_index"], "ausserhalb des S&P 1500")
            print(f"  {c['probability'] * 100:5.1f}%  {c['ticker']:6} {c['name'][:34]:36}"
                  f"{c['market_cap'] / 1e9:8.1f} Mrd. $  {origin}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="index_radar",
                                description="Prognose-Dashboard fuer S&P-Indexaufnahmen")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--no-open", action="store_true", help="Browser nicht oeffnen")
    p.add_argument("--json", action="store_true", help="nur berechnen, kein Server")
    p.add_argument("--top", type=int, default=0, help="Top-N je Index im Terminal zeigen")
    p.add_argument("--fresh", action="store_true", help="Fundamentaldaten-Cache leeren")
    p.add_argument("--export", nargs="?", const=str(export_site.DEFAULT_DEST), metavar="ORDNER",
                   help="statischen Schnappschuss zum Veroeffentlichen bauen (Standard: ./dist)")
    p.add_argument("--force", action="store_true",
                   help="Export auch bauen, wenn die Plausibilitaetspruefung anschlaegt")
    p.add_argument("--horizon", type=int, default=HORIZON_DAYS, help="Prognosehorizont in Tagen")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    setup_logging(args.verbose)
    if args.fresh:
        cache.clear()

    if args.export:
        snap = pipeline.run(horizon_days=args.horizon, fresh=args.fresh)
        pipeline.write(snap)

        problems = validate.check(snap)
        if problems:
            print("\nSnapshot nicht plausibel - es wird nichts exportiert:", file=sys.stderr)
            for pr in problems:
                print(f"  - {pr}", file=sys.stderr)
            if not args.force:
                return 2
            print("  (--force gesetzt, Export trotzdem gebaut)", file=sys.stderr)

        dest = export_site.build(snap, Path(args.export))
        out = export_site.build_vercel_output(dest)
        print(f"\nStatischer Export fertig: {dest}")
        print(f"ZIP (fuer Drag & Drop):   {dest}.zip")
        print(f"Vercel-Build-Output:      {out}")
        print("\nVeroeffentlichen:  vercel deploy --prebuilt --prod")
        if args.top:
            print_top(snap, args.top)
        return 0

    if args.json or args.top:
        snap = pipeline.run(horizon_days=args.horizon, fresh=args.fresh)
        pipeline.write(snap)
        if args.top:
            print_top(snap, args.top)
        return 0

    server.serve(port=args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    sys.exit(main())
