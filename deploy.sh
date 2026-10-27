#!/usr/bin/env bash
# LTX 2.5 frontend deploy script — run on the EC2 host from ~/ltx25
# Pulls latest code, reinstalls deps if needed, restarts the frontend service.
set -e

cd "$(dirname "$0")"
APP_DIR="$(pwd)"

echo "== Pulling latest code =="
git pull --ff-only

echo "== Frontend service =="
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart ltx25-frontend
sleep 2
curl -sf http://127.0.0.1:7777/health && echo " ltx25-frontend OK"

echo "== Done =="
