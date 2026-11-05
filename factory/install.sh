#!/bin/bash
# Install the factory: dedicated clones, venv, node_modules, labels, config, LaunchAgent.
set -euo pipefail
FACTORY_HOME="${FACTORY_HOME:-$HOME/factory}"
APP="$FACTORY_HOME/wiz_scheduler"
MKT="$FACTORY_HOME/wiz_scheduler_marketing"
mkdir -p "$FACTORY_HOME" "$HOME/Library/Logs/wizfactory" "$FACTORY_HOME/digests"

[[ -d "$APP/.git" ]] || git clone https://github.com/rubinder/wiz_scheduler.git "$APP"
[[ -d "$MKT/.git" ]] || git clone https://github.com/rubinder/wiz_scheduler_marketing.git "$MKT"
[[ -x "$APP/backend/.venv/bin/python" ]] || {
  uv venv --python 3.11 "$APP/backend/.venv"
  uv pip install -r "$APP/backend/requirements.txt" --python "$APP/backend/.venv/bin/python"
}
(cd "$APP/frontend" && npm ci)
(cd "$MKT" && npm ci)

[[ -f "$FACTORY_HOME/config.toml" ]] || {
  login=$(gh api user --jq .login) || { echo "gh api user failed; run gh auth login first" >&2; exit 1; }
  if [[ ! "$login" =~ ^[A-Za-z0-9-]+$ ]]; then
    echo "unexpected GitHub login from gh; aborting" >&2
    exit 1
  fi
  cp "$APP/factory/config.example.toml" "$FACTORY_HOME/config.toml"
  sed -i '' -e "s|^owner_login = .*|owner_login = \"$login\"|" "$FACTORY_HOME/config.toml"
  echo "Edit $FACTORY_HOME/config.toml and set notify_email (owner_login set to $login)."
}

for slug in rubinder/wiz_scheduler rubinder/wiz_scheduler_marketing; do
  for spec in "agent-ready:0e8a16" "agent-working:fbca04" "agent-pr-open:1d76db" "agent-blocked:d93f0b" \
              "agent-proposed:c5def5" "factory-miss:b60205"; do
    gh label create "${spec%%:*}" --repo "$slug" --color "${spec##*:}" 2>/dev/null || true
  done
done

PLIST="$HOME/Library/LaunchAgents/com.wizscheduler.factory.plist"
sed -e "s|__APP__|$APP|g" -e "s|__HOME__|$HOME|g" "$APP/factory/com.wizscheduler.factory.plist.template" > "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
sleep 1
for attempt in 1 2 3; do
  launchctl bootstrap "gui/$(id -u)" "$PLIST" && break
  [[ $attempt -eq 3 ]] && { echo "launchctl bootstrap failed" >&2; exit 1; }
  sleep 1
done
echo "Installed. Logs: ~/Library/Logs/wizfactory/. Stop: touch $FACTORY_HOME/STOP"
