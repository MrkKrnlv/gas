#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
(sleep 2; xdg-open http://localhost:8000 2>/dev/null || open http://localhost:8000) &
uvicorn main:app --port 8000
