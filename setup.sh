#!/usr/bin/env bash
# Install everything a Debian 12+ machine needs, then prepare the server and
# the MERT image. Start the server afterwards with ./restart.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB="$ROOT/merge-web-server"
AUDIO="$ROOT/offline-batch-testing"
CONTAINER="$ROOT/mert-v2-container"
ENV_FILE="${VIBESORT_ENV_FILE:-$HOME/.config/vibesort.env}"
IMAGE="${MERT_IMAGE:-mert-v2-30s}"
UNIT_PATH="/etc/systemd/system/vibesort.service"
RUN_USER="$(id -un)"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run ./setup.sh as the user that should own the server, not as root." >&2
  exit 1
fi

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

python_is_new_enough() {
  "$1" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

install_apt_packages() {
  export DEBIAN_FRONTEND=noninteractive
  as_root apt-get update || true
  as_root apt-get install -y ca-certificates curl python3 python3-venv python3-pip nodejs docker.io
  if ! as_root apt-get install -y firefox-esr; then
    as_root apt-get install -y firefox || true
  fi
  as_root systemctl enable --now docker
  if ! id -nG "$RUN_USER" | grep -qw docker; then
    as_root usermod -aG docker "$RUN_USER"
  fi
}

require_node() {
  if ! command -v node >/dev/null 2>&1; then
    echo "Node.js is not installed. Debian 12 or newer provides it in the nodejs package." >&2
    exit 1
  fi
  local major
  major="$(node -p 'Number(process.versions.node.split(".")[0])')"
  if [[ "$major" -lt 18 ]]; then
    echo "Node.js 18 or newer is required so YouTube downloads can run. This machine has $(node -v)." >&2
    exit 1
  fi
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_new_enough "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done
  if apt-cache show python3.13 >/dev/null 2>&1; then
    as_root apt-get install -y python3.13 python3.13-venv >&2
    echo python3.13
    return 0
  fi
  if apt-cache show python3.12 >/dev/null 2>&1; then
    as_root apt-get install -y python3.12 python3.12-venv >&2
    echo python3.12
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >&2
  fi
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.12 >&2
  uv python find 3.12
}

run_docker() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    as_root docker "$@"
  fi
}

make_venv() {
  local project="$1"
  local python="$2"
  if [[ ! -x "$project/.venv/bin/python" ]] || ! python_is_new_enough "$project/.venv/bin/python"; then
    "$python" -m venv "$project/.venv"
  fi
  "$project/.venv/bin/python" -m pip install --upgrade pip
  "$project/.venv/bin/python" -m pip install -r "$project/requirements.txt"
}

echo "Installing system packages..."
install_apt_packages
require_node
PYTHON="$(find_python)"

echo "Setting up the web server environment..."
make_venv "$WEB" "$PYTHON"

echo "Setting up the audio tools Vectorize uses..."
make_venv "$AUDIO" "$PYTHON"
"$AUDIO/.venv/bin/python" - "$AUDIO" <<'PY'
import sys
from pathlib import Path
import imageio_ffmpeg

root = Path(sys.argv[1])
tools = root / ".tools" / "bin"
tools.mkdir(parents=True, exist_ok=True)
link = tools / "ffmpeg"
if link.is_symlink() or link.exists():
    raise SystemExit(0)
link.symlink_to(Path(imageio_ffmpeg.get_ffmpeg_exe()))
PY

if [[ ! -f "$ENV_FILE" ]]; then
  install -d -m 700 "$(dirname "$ENV_FILE")"
  key="$("$WEB/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(64))')"
  umask 077
  cat >"$ENV_FILE" <<EOF
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=$key
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,vibesort.io
DJANGO_CSRF_ORIGINS=https://vibesort.io,http://localhost:8765,http://127.0.0.1:8765
DJANGO_BEHIND_HTTPS=false
EOF
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE"
else
  echo "Keeping the existing production environment file."
fi

tmp_unit="$(mktemp)"
cat >"$tmp_unit" <<EOF
[Unit]
Description=Vibesort web server
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
SupplementaryGroups=docker
WorkingDirectory=$WEB
EnvironmentFile=$ENV_FILE
ExecStart=$WEB/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8765 --workers 1 --threads 4 --timeout 120 --access-logfile - --error-logfile -
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
as_root cp "$tmp_unit" "$UNIT_PATH"
rm -f "$tmp_unit"
as_root systemctl daemon-reload
as_root systemctl enable vibesort.service
# An earlier setup used a per-user service. One server can bind the port.
systemctl --user disable --now vibesort.service >/dev/null 2>&1 || true

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
(
  cd "$WEB"
  .venv/bin/python manage.py collectstatic --noinput
)

echo "Building the MERT container image..."
run_docker build -t "$IMAGE" "$CONTAINER"

echo "Setup is complete. Start the server with ./restart.sh"
echo "The server listens on http://127.0.0.1:8765"
if ! command -v firefox >/dev/null 2>&1 && ! command -v firefox-esr >/dev/null 2>&1; then
  echo "Firefox is not installed. Vectorize needs a Firefox profile that is logged into YouTube." >&2
else
  echo "Vectorize reads YouTube cookies from Firefox. Log into YouTube in Firefox on this machine once."
fi
echo "The MERT container starts the first time a playlist is vectorized."
