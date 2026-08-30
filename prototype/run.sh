#!/usr/bin/env bash
# ControlPlane.ai prototype launcher
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv
    uv pip install --python .venv/bin/python -r requirements.txt
  else
    python3 -m venv .venv
    .venv/bin/pip install --quiet -r requirements.txt
  fi
fi

exec .venv/bin/streamlit run app.py "$@"
