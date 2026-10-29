#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="/home/ssm-user/api-security-scanner"
ENV_FILE="/root/api-scanner-runtime.env"
NETWORK="api-scanner-net"

BACKEND="api-scanner-backend-next"
WORKER="api-scanner-scan-worker"
FRONTEND="api-scanner-frontend"

HEALTH_URL="http://127.0.0.1:8080/api/health"

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: Run this script with sudo." >&2
  exit 1
fi

exec 9>/var/lock/api-scanner-deploy.lock
if ! flock -n 9; then
  echo "ERROR: Another deployment is running." >&2
  exit 1
fi

if [[ ! -r "$ENV_FILE" ]]; then
  echo "ERROR: Runtime environment file is missing or unreadable." >&2
  exit 1
fi

docker network inspect "$NETWORK" >/dev/null

for name in "$BACKEND" "$WORKER" "$FRONTEND"; do
  if ! docker inspect "$name" >/dev/null 2>&1; then
    echo "ERROR: Expected container $name is missing." >&2
    exit 1
  fi
done

if [[ -n "$(sudo -u ssm-user git -C "$REPO_DIR" status --porcelain)" ]]; then
  echo "ERROR: Deployment checkout has uncommitted changes." >&2
  exit 1
fi

COMMIT="$(sudo -u ssm-user git -C "$REPO_DIR" rev-parse --short=12 HEAD)"
TAG="${COMMIT}-$(date -u +%Y%m%d%H%M%S)"

NEW_BACKEND="api-scanner-backend:${TAG}"
NEW_FRONTEND="api-scanner-frontend:${TAG}"

OLD_BACKEND="$(docker inspect --format '{{.Config.Image}}' "$BACKEND")"
OLD_WORKER="$(docker inspect --format '{{.Config.Image}}' "$WORKER")"
OLD_FRONTEND="$(docker inspect --format '{{.Config.Image}}' "$FRONTEND")"

for image in "$OLD_BACKEND" "$OLD_WORKER" "$OLD_FRONTEND"; do
  docker image inspect "$image" >/dev/null
done

cd "$REPO_DIR"

echo "Building backend image: $NEW_BACKEND"
docker build \
  -f conf/docker/prod/fastapi.docker \
  -t "$NEW_BACKEND" \
  --label "org.opencontainers.image.revision=${COMMIT}" .

echo "Building frontend image: $NEW_FRONTEND"
docker build \
  -f conf/docker/prod/vite.docker \
  -t "$NEW_FRONTEND" \
  --label "org.opencontainers.image.revision=${COMMIT}" .

wait_healthy() {
  local name="$1"
  local state=""

  for _ in $(seq 1 60); do
    state="$(docker inspect --format \
      '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "$name" 2>/dev/null || true)"

    if [[ "$state" == "healthy" ]]; then
      return 0
    fi

    if [[ "$state" == "unhealthy" || "$state" == "exited" || "$state" == "dead" || -z "$state" ]]; then
      break
    fi

    sleep 2
  done

  docker logs --tail 80 "$name" >&2 || true
  echo "ERROR: $name did not become healthy." >&2
  return 1
}

start_backend() {
  local image="$1"

  docker rm -f "$BACKEND" >/dev/null 2>&1 || true

  if ! docker run -d \
    --name "$BACKEND" \
    --network "$NETWORK" \
    --network-alias backend-next \
    --env-file "$ENV_FILE" \
    --restart unless-stopped \
    --publish 127.0.0.1:18001:8000 \
    "$image" >/dev/null; then
    return 1
  fi

  wait_healthy "$BACKEND"
}

start_worker() {
  local image="$1"
  local state=""

  docker rm -f "$WORKER" >/dev/null 2>&1 || true

  if ! docker run -d \
    --name "$WORKER" \
    --network "$NETWORK" \
    --env-file "$ENV_FILE" \
    --restart unless-stopped \
    --no-healthcheck \
    "$image" python worker.py >/dev/null; then
    return 1
  fi

  sleep 5
  state="$(docker inspect --format '{{.State.Status}} {{.RestartCount}}' \
    "$WORKER" 2>/dev/null || true)"

  if [[ "$state" == "running 0" ]]; then
    echo "Worker container is running."
    return 0
  fi

  docker logs --tail 80 "$WORKER" >&2 || true
  echo "ERROR: Worker failed to stay running (state: ${state:-unknown})." >&2
  return 1
}

start_frontend() {
  local image="$1"

  docker rm -f "$FRONTEND" >/dev/null 2>&1 || true

  if ! docker run -d \
    --name "$FRONTEND" \
    --network "$NETWORK" \
    --restart unless-stopped \
    --publish 8080:80 \
    "$image" >/dev/null; then
    return 1
  fi

  wait_healthy "$FRONTEND"
}

rollback() {
  local component="$1"
  echo "Rolling back deployment after: $component" >&2

  start_backend "$OLD_BACKEND" || true
  start_worker "$OLD_WORKER" || true
  start_frontend "$OLD_FRONTEND" || true
}

echo "Deploying backend..."
if ! start_backend "$NEW_BACKEND"; then
  rollback "backend health check"
  exit 1
fi

echo "Deploying scan worker..."
if ! start_worker "$NEW_BACKEND"; then
  rollback "worker startup"
  exit 1
fi

echo "Deploying frontend..."
if ! start_frontend "$NEW_FRONTEND"; then
  rollback "frontend health check"
  exit 1
fi

echo "Checking application API..."
for _ in $(seq 1 20); do
  if curl --fail --silent --show-error \
      --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Deployment successful: ${COMMIT}"
    exit 0
  fi
  sleep 2
done

rollback "application API smoke test"
exit 1
