#!/usr/bin/env bash
# Render the one-page charts docs/source/{code_map,code_llm_flow,db_schema}.html -> docs/*.pdf with headless Chrome (no extra dependencies).
set -euo pipefail
cd "$(dirname "$0")/.."
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[ -x "$CHROME" ] || CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
.venv/bin/python scripts/build_db_schema_page.py
.venv/bin/python scripts/build_flow_page.py
.venv/bin/python scripts/build_call_chain_page.py
for name in code_map code_llm_flow db_schema call_chain; do
  "$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf="$PWD/docs/$name.pdf" \
    "file://$PWD/docs/source/$name.html" 2>/dev/null
  echo "wrote docs/$name.pdf"
done
