#!/usr/bin/env bash
set -e

export KITE_API_KEY="YOUR_KEY"
export KITE_ACCESS_TOKEN="YOUR_ACCESS_TOKEN"

# Optional: cache instruments locally to speed up restarts (not a database)
export INSTRUMENTS_CACHE_PATH="./instruments_cache.csv.gz"

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload