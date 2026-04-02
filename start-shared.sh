#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "============================================"
echo "  Arvind Billing System - Shared DB Mode"
echo "============================================"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is not installed."
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js is not installed."
  exit 1
fi

echo "Installing Python dependencies..."
pip3 install -r requirements.txt --quiet 2>/dev/null || pip3 install -r requirements.txt --break-system-packages --quiet

if [ ! -d "frontend/node_modules" ]; then
  echo "Installing npm dependencies..."
  (cd frontend && npm install)
fi

echo "Starting shared backend on http://127.0.0.1:5000 ..."
APP_MODE=cloud AUTH_REQUIRED=1 CLOUD_ONLY_MODE=1 LOGIN_ONLY_MODE=1 ALLOW_SELF_REGISTER=0 DEFAULT_ADMIN_USERNAME=admin DEFAULT_ADMIN_PASSWORD=Admin@123 JWT_SECRET=dev-local-change-me python3 backend/app.py &
BACKEND_PID=$!

cleanup() {
  if kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:5000/api/health" >/dev/null; then
    break
  fi
  sleep 0.5
done

echo "Opening web application..."
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:5000" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:5000" >/dev/null 2>&1 || true
fi

echo "Starting desktop in external-backend mode..."
cd frontend
BILLING_USE_EXTERNAL_BACKEND=1 BILLING_CLOUD_ONLY_MODE=1 BILLING_BACKEND_ORIGIN=http://127.0.0.1:5000 npm start
