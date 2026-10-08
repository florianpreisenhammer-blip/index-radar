"""Plausibilitaetspruefung eines Snapshots vor dem Deploy.

Im automatisierten Lauf gibt es niemanden, der das Ergebnis anschaut. Wenn
Yahoo drosselt oder Wikipedia eine Tabelle umbaut, soll der Job abbrechen -
und nicht eine halbleere Seite veroeffentlichen.
"""
from __future__ import annotations

from .config import INDEX_SPECS

MIN_UNIVERSE = 2500
MIN_MEMBER_RATIO = 0.9          # je Index mind. 90 % der erwarteten Mitglieder
MIN_ELIGIBLE = {"sp500": 20, "sp100": 50, "sp400": 40, "sp600": 60}
MAX_FAILED_RATIO = 0.25         # Anteil Titel ohne Fundamentaldaten


def check(snapshot: dict) -> list[str]:
    problems: list[str] = []

    universe = snapshot.get("universe_size", 0)
    if universe < MIN_UNIVERSE:
        problems.append(f"Universum nur {universe} Titel (erwartet mind. {MIN_UNIVERSE}) - "
                        f"Datenquelle vermutlich gedrosselt")

    loaded = snapshot.get("fundamentals_loaded", 0)
    failed = snapshot.get("fundamentals_failed", 0)
    if loaded and failed / loaded > MAX_FAILED_RATIO:
        problems.append(f"{failed} von {loaded} Titeln ohne Fundamentaldaten "
                        f"({failed / loaded:.0%}) - Abfragen wurden gedrosselt")

    indices = {i["index"]: i for i in snapshot.get("indices", [])}
    for key, spec in INDEX_SPECS.items():
        idx = indices.get(key)
        if idx is None:
            problems.append(f"{spec.label}: fehlt im Snapshot")
            continue
        if idx["members"] < spec.members_target * MIN_MEMBER_RATIO:
            problems.append(f"{spec.label}: nur {idx['members']} Mitglieder geladen "
                            f"(erwartet ~{spec.members_target}) - Mitgliederliste unvollstaendig")
        if idx["eligible_count"] < MIN_ELIGIBLE.get(key, 10):
            problems.append(f"{spec.label}: nur {idx['eligible_count']} qualifizierte Kandidaten")
        if idx["calibration"]["adds_per_year"] <= 0:
            problems.append(f"{spec.label}: keine Aufnahmerate ermittelt")

    th = snapshot.get("thresholds") or {}
    bands = [th.get("sp600_min"), th.get("sp600_max"), th.get("sp400_min"),
             th.get("sp400_max"), th.get("sp500_min")]
    if not all(isinstance(v, (int, float)) and v > 0 for v in bands):
        problems.append("Groessengrenzen fehlen oder sind unbrauchbar")
    else:
        lo6, hi6, lo4, hi4, lo5 = bands
        if not (lo6 < hi6 and lo4 < hi4):
            problems.append("Groessenbaender sind in sich widerspruechlich")
        if abs(hi6 - lo4) > 1e6 or abs(hi4 - lo5) > 1e6:
            problems.append("Groessenbaender grenzen nicht aneinander - "
                            "vermutlich falsch gelesen")

    if not snapshot.get("generated_at"):
        problems.append("Zeitstempel fehlt")
    return problems
