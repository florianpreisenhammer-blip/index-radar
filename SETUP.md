# Automatische Aktualisierung einrichten

Ziel: Die Netlify-Seite rechnet sich zweimal taeglich selbst neu, ohne dass du
etwas hochlaedst. Dafuer laeuft die Python-Pipeline in GitHub Actions und schiebt
das Ergebnis per Netlify-API.

Einmalig, danach laeuft es von allein. Dauer: ungefaehr 10 Minuten.

---

## 0. Git auf dem Mac freischalten (einmalig)

`git` ist aktuell blockiert, weil die Xcode-Lizenz nicht bestaetigt ist:

```bash
sudo xcodebuild -license
```

Mit Leertaste bis ans Ende blaettern, `agree` tippen. Danach:

```bash
git --version
```

## 1. Eigenes Repository anlegen

**Wichtig:** Nicht das `Trends`-Repository hochladen. Dort liegen der
Firebase-Admin-Schluessel und der APNs-Key (`AuthKey_*.p8`) im Klartext - auf
GitHub waeren die kompromittiert. `index_radar/` ist komplett eigenstaendig und
enthaelt keine Geheimnisse.

```bash
cd ~/Trends/index_radar
git init -b main
git add .
git commit -m "Index-Radar: S&P-Aufnahmeprognose"
```

Dann auf GitHub ein **neues, privates** Repository anlegen (z. B. `index-radar`),
ohne README/Lizenz, und pushen:

```bash
git remote add origin https://github.com/<dein-benutzername>/index-radar.git
git push -u origin main
```

## 2. Netlify-Seite anlegen

Die Seite muss einmal existieren, damit es eine Site-ID gibt:

1. [app.netlify.com/drop](https://app.netlify.com/drop) oeffnen.
2. Den Ordner `dist` hineinziehen (`./run.sh --export` erzeugt ihn).
3. Oben rechts ueber "Site configuration" einen sprechenden Namen vergeben.

**Site-ID holen:** Site configuration -> General -> Site information ->
*Site ID* (sieht aus wie `a1b2c3d4-...`).

**Zugriffstoken holen:** oben rechts auf das Nutzerbild ->
User settings -> Applications -> Personal access tokens ->
"New access token". Den Wert sofort kopieren, er wird nur einmal angezeigt.

## 3. Secrets im Repository hinterlegen

Auf GitHub: Repository -> Settings -> Secrets and variables -> Actions ->
"New repository secret". Zwei Stueck:

| Name | Wert |
|---|---|
| `NETLIFY_AUTH_TOKEN` | das Personal Access Token aus Schritt 2 |
| `NETLIFY_SITE_ID` | die Site-ID aus Schritt 2 |

## 4. Erstmals ausloesen

Repository -> Actions -> "Dashboard aktualisieren und deployen" ->
"Run workflow". Der Lauf dauert zwei bis drei Minuten. In der
Zusammenfassung stehen danach die Netlify-Adresse und der Datenstand.

Ab dann laeuft er automatisch:

- werktags und samstags um 06:00 UTC (08:00 deutscher Sommerzeit),
- werktags um 22:30 UTC - S&P veroeffentlicht Indexaenderungen fast immer
  nach US-Boersenschluss, dieser Lauf hat sie also am selben Abend.

---

## Was passiert, wenn etwas schiefgeht

Yahoo Finance drosselt Abfragen aus Rechenzentren gelegentlich - und
GitHub-Actions-Laeufer sind genau das. Dagegen sind drei Stufen eingebaut:

1. **Rueckfallquelle:** faellt der Yahoo-Screener aus, nutzt die Pipeline die
   offizielle Nasdaq-Symboldatei plus Sammelabfragen.
2. **Drei Anlaeufe** mit je drei Minuten Pause.
3. **Plausibilitaetspruefung** (`index_radar/validate.py`): stimmen Universum,
   Mitgliederzahlen und Kandidatenzahlen nicht, bricht der Job mit Exit-Code 2
   ab - **deployed wird dann nichts**. Die alte Seite bleibt online, mit ihrem
   sichtbaren (dann aelteren) Datenstand.

GitHub schickt dir bei fehlgeschlagenen Laeufen automatisch eine E-Mail. Der
gebaute Export haengt als Artefakt am Lauf, auch wenn das Deployen scheitert.

## Kosten

Privates Repository: 2.000 Actions-Minuten pro Monat inklusive. Dieser Job
braucht ungefaehr 3 Minuten pro Lauf, also rund 150 Minuten im Monat.
Netlify: im kostenlosen Tarif enthalten (reines Ausliefern, kein Build).
