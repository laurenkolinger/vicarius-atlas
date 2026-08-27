#!/usr/bin/env bash
# Serve the 3D status viewer locally (browsers block file:// fetch of status.json).
cd "$(dirname "$0")" || exit 1
PORT="${1:-8765}"
echo "3D status viewer -> http://localhost:${PORT}/"
exec python3 -m http.server "$PORT"
