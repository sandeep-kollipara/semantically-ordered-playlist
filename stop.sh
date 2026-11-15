#!/usr/bin/env bash
# Stop the production gunicorn server.
set -euo pipefail

if [[ -f /etc/systemd/system/vibesort.service ]]; then
  sudo systemctl stop vibesort.service
  echo "Stopped the Vibesort server."
  exit 0
fi

user_unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/vibesort.service"
if [[ -f "$user_unit" ]]; then
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  systemctl --user stop vibesort.service
  echo "Stopped the Vibesort server."
  exit 0
fi

echo "The production service is not installed." >&2
exit 1
