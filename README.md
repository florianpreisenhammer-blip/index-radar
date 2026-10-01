# Index-Radar

Prognose-Dashboard: **Welche Aktien werden als naechstes in den S&P 500, S&P 100,
S&P MidCap 400 oder S&P SmallCap 600 aufgenommen - und mit welcher Wahrscheinlichkeit?**

Jeder Start rechnet komplett neu. Es gibt keine gepflegte Kandidatenliste,
die veralten koennte.

```bash
./run.sh              # rechnet neu und oeffnet das Dashboard auf http://127.0.0.1:8787
./run.sh --top 15     # nur Terminal-Ausgabe, kein Server
./run.sh --fresh      # ignoriert auch den Fundamentaldaten-Cache
```

## Statischer Export (Netlify & Co.)

```bash
./run.sh --export            # rechnet neu und baut ./dist + ./dist.zip
```

`dist/` enthaelt `index.html`, `data.json`, `_headers` und `netlify.toml`.
Bei [app.netlify.com/drop](https://app.netlify.com/drop) den Ordner (oder das ZIP)
hineinziehen - fertig. Die Seite erkennt selbst, dass kein Python-Server erreichbar
ist, liest dann `data.json` und blendet die Neuberechnen-Schaltflaechen aus.

Wichtig: **Netlify fuehrt kein Python aus.** Ein Upload ist ein eingefrorener
Schnappschuss mit sichtbarem Stichtag. Die laufende Neuberechnung beim Oeffnen
gibt es nur lokal ueber `./run.sh`.

Damit die Online-Version trotzdem aktuell bleibt, liegt in
`.github/workflows/update-dashboard.yml` ein GitHub-Actions-Job bereit, der die
Pipeline zweimal taeglich rechnet und das Ergebnis per Netlify-API deployt.
Einrichtung in [SETUP.md](SETUP.md). Vor dem Deploy prueft
`index_radar/validate.py` den Snapshot auf Plausibilitaet - bei gedrosselten
Datenquellen bricht der Job ab, statt eine halbleere Seite zu veroeffentlichen.

## Was dieses Dashboard anders macht

Der Ausloeser fuer dieses Projekt waren zwei konkrete Fehler eines aehnlichen
Dashboards: **Bloom Energy** stand dort als Kandidat mit einer Wahrscheinlichkeit,
obwohl S&P die Aufnahme laengst offiziell angekuendigt hatte. Und **Illumina**
wurde aufgenommen, ohne je auf dem Dashboard aufgetaucht zu sein.

Beide Fehlerklassen sind hier strukturell ausgeschlossen:

| Fehler | Gegenmassnahme |
|---|---|
| Bereits aufgenommener Titel erscheint als Kandidat | Mitgliederlisten aller vier Indizes werden bei jedem Lauf frisch geladen und Mitglieder hart aus dem Pool entfernt |
| Bereits **angekuendigter** Titel erscheint als Kandidat | Die Pressemitteilungen von S&P Dow Jones Indices werden geparst. Angekuendigte Aufnahmen stehen als Fixum im Banner, nicht in der Prognose |
| Neuaufnahme taucht nie im Dashboard auf | Kandidatenpool ist das **gesamte** US-Aktienuniversum (~6.500 Titel), keine kuratierte Liste |
| Prozentwerte ohne Bedeutung | Wahrscheinlichkeiten werden so normiert, dass ihre Summe der empirisch gemessenen Zahl der Aufnahmen entspricht |

## Datenquellen

| Quelle | Wofuer | Frische |
|---|---|---|
| [press.spglobal.com](https://press.spglobal.com/) | angekuendigte Indexaenderungen mit Stichtag | jeder Lauf |
| Wikipedia (Mitgliederlisten + Aenderungshistorie) | aktuelle Mitglieder, GICS-Sektoren, Aufnahmeraten | jeder Lauf |
| Yahoo Finance (Screener + quoteSummary) | Universum, Marktkapitalisierung, Streubesitz, Quartalsgewinne, Liquiditaet | Kurse jeder Lauf, Fundamentaldaten max. 12 h Cache |
| nasdaqtrader.com Symboldatei | Rueckfall-Universum, falls Yahoo drosselt | bei Bedarf |

Alle Quellen sind frei und ohne API-Schluessel nutzbar.

## Modell in drei Saetzen

1. **Harte Kriterien** nach S&P U.S. Indices Methodology (Domizil, Boerse, 12 Monate
   Seasoning, Streubesitz >= 50 %, Liquiditaetsquote >= 0,75, positives GAAP-Ergebnis
   im letzten Quartal und ueber vier Quartale, Groessenband) filtern den Pool.
2. **Neun Faktoren** gewichten die verbleibenden Kandidaten - darunter der Herkunftsindex,
   dessen Hebel aus der echten Historie gemessen wird (z. B. kommt ein grosser Teil der
   S&P-500-Aufnahmen per Aufstieg aus dem MidCap 400).
3. **Poisson-Normierung**: `p = 1 - exp(-E * w / Summe(w))`, wobei `E` die aus der
   Aenderungshistorie gemessene Zahl erwarteter Aufnahmen im Horizont ist. Dadurch ist
   die Summe aller Wahrscheinlichkeiten eines Index gleich der Zahl der Plaetze,
   die erfahrungsgemaess frei werden.

Die Groessengrenzen sind bewusst **weich**: S&P behaelt sich Ermessen vor und nimmt
regelmaessig Titel knapp unterhalb des Richtwerts auf. Wer knapp darunter liegt, bleibt
mit reduzierter Chance im Rennen, statt unsichtbar zu werden.

## Aufbau

```
index_radar/
  config.py            Schwellenwerte, Indexdefinitionen, Modellparameter
  eligibility.py       harte Aufnahmekriterien -> pass / warn / fail / unknown
  scoring.py           Faktoren -> Gewicht -> kalibrierte Wahrscheinlichkeit
  calibration.py       Aufnahmeraten und Herkunftsstatistik aus der Historie
  pipeline.py          Orchestrierung, schreibt data/latest.json
  server.py            lokaler Dashboard-Server (nur Standardbibliothek)
  cache.py             SQLite-Cache fuer Fundamentaldaten
  sources/
    constituents.py    Mitgliederlisten
    announcements.py   S&P-Pressemitteilungen (angekuendigte Aenderungen)
    history.py         Aenderungshistorie
    market.py          Universum und Fundamentaldaten
web/index.html         Dashboard
```

## Grenzen

- Der haeufigste Ausloeser einer Aufnahme ist eine Uebernahme, die einen Platz frei macht.
  Das Modell schaetzt, **wer** einen frei werdenden Platz bekommt - nicht, **wann** er frei wird.
- Das S&P-Indexkomitee entscheidet mit Ermessen und begruendet nicht. Das ist nicht modellierbar.
- Streubesitz, Domizil und Gewinne stammen von Yahoo Finance; Sonderfaelle (auslaendische
  Rechtsform mit US-Adresse, mehrere Aktiengattungen) sind in der Detailansicht als Warnung markiert.
- Keine Anlageberatung.
