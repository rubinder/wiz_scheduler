#!/bin/bash
# Remove the LaunchAgent. Clones, config and state are kept.
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.wizscheduler.factory.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "Uninstalled. Clones kept in ${FACTORY_HOME:-$HOME/factory}."
