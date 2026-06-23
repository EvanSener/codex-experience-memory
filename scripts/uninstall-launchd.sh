#!/usr/bin/env bash
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.codex.experience-memory.dreamer.plist"

launchctl unload "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "removed: $PLIST"
