#!/usr/bin/env bash
# Pull the latest code and rebuild the running bot. Run on the VM from the project directory.
set -euo pipefail
cd "$(dirname "$0")/../.."
git pull --ff-only
docker compose up -d --build
docker compose ps
