#!/usr/bin/env bash
# Index-Radar starten: rechnet frisch durch und oeffnet das Dashboard.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c "import yfinance, pandas, requests" 2>/dev/null || {
  echo "Installiere Abhaengigkeiten ..."
  "$PY" -m pip install -r requirements.txt
}
exec "$PY" -m index_radar "$@"
