#!/usr/bin/env bash
# Cloud-side half of TripPilot's one-command deployment. Run by deploy_cloud.ps1.
set -Eeuo pipefail

PROJECT_ROOT="${1:?project directory required}"
cd "$PROJECT_ROOT"

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [[ -z "$PYTHON_BIN" && -x /root/miniconda3/bin/python ]]; then PYTHON_BIN=/root/miniconda3/bin/python; fi
[[ -n "$PYTHON_BIN" ]] || { echo "Python 3 is required."; exit 22; }

test -f .env || { echo ".env was not uploaded."; exit 24; }
mkdir -p logs data/knowledge

"$PYTHON_BIN" -m venv --system-site-packages .venv-cloud
source .venv-cloud/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

test -f frontend/dist/index.html || { echo "Prebuilt frontend is missing."; exit 23; }

export PYTHONPATH="$PROJECT_ROOT/backend"
trippilot init-db
trippilot create-admin --from-env || true

pkill -f "uvicorn trippilot.app:app" || true
pkill -f "trippilot worker" || true
nohup python -m uvicorn trippilot.app:app --host 0.0.0.0 --port 6006 > logs/api.log 2>&1 &
nohup trippilot worker > logs/worker.log 2>&1 &

sleep 3
curl -fsS http://127.0.0.1:6006/api/v1/health
echo
echo "TripPilot deployment completed. Configure the cloud provider's TCP/HTTP port mapping for 6006."


