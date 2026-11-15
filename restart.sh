#!/usr/bin/env bash
# Start the production server, or restart it if it is already running.
set -euo pipefail

if [[ -f /etc/systemd/system/vibesort.service ]]; then
  sudo systemctl daemon-reload
  sudo systemctl restart vibesort.service
  sudo systemctl --no-pager --full status vibesort.service
  exit 0
fi

user_unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/vibesort.service"
if [[ -f "$user_unit" ]]; then
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  systemctl --user daemon-reload
  systemctl --user restart vibesort.service
  systemctl --user --no-pager --full status vibesort.service
  exit 0
fi

echo "The production service is not installed. Run ./setup.sh first." >&2
exit 1
