#!/usr/bin/env bash
# Start and stop the MERT-v2-30s embedding API.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${MERT_IMAGE:-mert-v2-30s}"
NAME="${MERT_CONTAINER:-mert-v2-30s}"
PORT="${MERT_PORT:-8088}"
DEVICE="${MERT_DEVICE:-cpu}"
CACHE_VOLUME="${MERT_CACHE_VOLUME:-mert-v2-hf-cache}"
CLASS_VOLUME="${MERT_CLASS_VOLUME:-mert-v2-classes}"

usage() {
  cat <<EOF
Usage: ./deploy.sh <start|stop|restart|status|logs>

  start     Build the image, start the container, and wait until it accepts requests.
  stop      Stop and remove the container. Cached model weights are kept.
  restart   Stop, then start.
  status    Show whether the container is running.
  logs      Follow the container logs.

Environment:
  MERT_PORT     Host port (default 8088; 8080 is often already taken)
  MERT_DEVICE   cpu or cuda (default cpu)

The API embeds 30-second clips with m-a-p/MERT-v2-30s.
  POST /embeddings     vector for one clip
  POST /classes        register an example clip for a label
  POST /classify       nearest registered label, plus the embedding
EOF
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not installed." >&2
    exit 1
  fi
  if docker info >/dev/null 2>&1; then
    return 0
  fi
  if docker info 2>&1 | grep -qi "permission denied"; then
    echo "Docker is running, but this user cannot access /var/run/docker.sock." >&2
    echo "Add your user to the docker group and open a new shell:" >&2
    echo "  sudo usermod -aG docker \"\$USER\"" >&2
    echo "Or run this script with sudo." >&2
    exit 1
  fi
  echo "docker is installed but the daemon is not reachable." >&2
  exit 1
}

container_id() {
  docker ps -aq -f "name=^/${NAME}$"
}

running_id() {
  docker ps -q -f "name=^/${NAME}$"
}

stop_container() {
  require_docker
  local id
  id="$(container_id)"
  if [[ -z "${id}" ]]; then
    echo "Container ${NAME} is not running."
    return 0
  fi
  docker stop "${NAME}" >/dev/null
  docker rm "${NAME}" >/dev/null
  echo "Stopped ${NAME}."
}

wait_until_ready() {
  local deadline=$((SECONDS + 1800))
  echo "Waiting for ${NAME} to load MERT-v2-30s..."
  while (( SECONDS < deadline )); do
    if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      echo "Ready at http://127.0.0.1:${PORT}"
      return 0
    fi
    if [[ -z "$(running_id)" ]]; then
      echo "Container ${NAME} exited before it was ready." >&2
      docker logs "${NAME}" >&2 || true
      return 1
    fi
    sleep 5
  done
  echo "Timed out waiting for http://127.0.0.1:${PORT}/health" >&2
  docker logs --tail 80 "${NAME}" >&2 || true
  return 1
}

start_container() {
  require_docker
  if [[ -n "$(running_id)" ]]; then
    echo "Already running at http://127.0.0.1:${PORT}"
    return 0
  fi
  if [[ "${DEVICE}" != "cpu" && "${DEVICE}" != "cuda" ]]; then
    echo "MERT_DEVICE must be cpu or cuda." >&2
    exit 1
  fi
  if ! python3 - "$PORT" <<'PY'
import socket, sys
port = int(sys.argv[1])
sock = socket.socket()
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
  then
    echo "Host port ${PORT} is already in use." >&2
    echo "Choose another port, for example: MERT_PORT=8090 ./deploy.sh start" >&2
    exit 1
  fi

  echo "Building ${IMAGE}..."
  docker build -t "${IMAGE}" "${ROOT}"

  local existing
  existing="$(container_id)"
  if [[ -n "${existing}" ]]; then
    docker rm -f "${NAME}" >/dev/null
  fi

  local gpu_args=()
  if [[ "${DEVICE}" == "cuda" ]]; then
    gpu_args=(--gpus all)
  fi

  docker run -d \
    --name "${NAME}" \
    "${gpu_args[@]}" \
    -p "${PORT}:8080" \
    -e "MERT_DEVICE=${DEVICE}" \
    -e "HF_HOME=/cache/huggingface" \
    -e "CLASS_STORE=/cache/classes/prototypes.json" \
    -v "${CACHE_VOLUME}:/cache/huggingface" \
    -v "${CLASS_VOLUME}:/cache/classes" \
    "${IMAGE}" >/dev/null

  wait_until_ready
}

case "${1:-}" in
  start) start_container ;;
  stop) stop_container ;;
  restart)
    stop_container
    start_container
    ;;
  status)
    require_docker
    if [[ -n "$(running_id)" ]]; then
      echo "${NAME} is running at http://127.0.0.1:${PORT}"
    elif [[ -n "$(container_id)" ]]; then
      echo "${NAME} exists but is stopped."
    else
      echo "${NAME} is not running."
    fi
    ;;
  logs)
    require_docker
    docker logs -f "${NAME}"
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
