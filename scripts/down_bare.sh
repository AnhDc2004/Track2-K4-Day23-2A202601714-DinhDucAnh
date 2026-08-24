#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
for f in run/*.pid; do
  [ -f "$f" ] || continue
  pid=$(cat "$f" 2>/dev/null || true)
  if [ -n "${pid:-}" ]; then kill -CONT "$pid" 2>/dev/null; kill -9 "$pid" 2>/dev/null; fi
  rm -f "$f"
done
echo "all stopped"
