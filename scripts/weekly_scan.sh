#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

mkdir -p data outputs

if [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi

PYTHONPATH=src "$PYTHON" -m trade_strategy.cli weekly-scan \
  --data-dir data \
  --use-finviz-sector-screener \
  --finviz-rsi-mode both \
  --holdings examples/holdings.csv \
  --leading-sector-stocks-only \
  --refresh-data \
  --fetch-start 2020-01-01 \
  --notify \
  --output outputs/weekly_scan.csv
