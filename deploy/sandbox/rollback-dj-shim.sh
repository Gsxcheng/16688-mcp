#!/usr/bin/env bash
# Put dj-shim back to the container kept by update-dj-shim.sh.
# Usage: bash rollback-dj-shim.sh [stamp]
# stamp defaults to the newest backup under /data/workspace/dujiao-test/backup.
# Does not delete the failed container, and does not touch dj-app, mock16688, redis or aihot.
set -euo pipefail

ROOT=${ROOT:-/data/workspace/dujiao-test}
CONTAINER=${CONTAINER:-dj-shim}
IMAGE=${IMAGE:-gsx/16688-mcp:test}
STAMP=${1:-}

if [[ -z "$STAMP" ]]; then
  STAMP=$(find "$ROOT/backup" -mindepth 1 -maxdepth 1 -type d -name 'dj-shim-*' -printf '%f\n' 2>/dev/null | sed 's/^dj-shim-//' | sort | tail -n 1 || true)
fi
if [[ -z "$STAMP" ]]; then
  echo "没有可回滚的备份。" >&2
  exit 2
fi

PREV="${CONTAINER}-prev-${STAMP}"
if ! docker inspect "$PREV" >/dev/null 2>&1; then
  echo "找不到 ${PREV}。没有改动。" >&2
  exit 2
fi

before=$(mktemp)
docker ps -a --format '{{.Names}} {{.State}}' | grep -v "^${CONTAINER} " | grep -v "^${PREV} " | sort > "$before"

if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  docker stop "$CONTAINER" >/dev/null || true
  docker rename "$CONTAINER" "${CONTAINER}-failed-${STAMP}"
fi
docker rename "$PREV" "$CONTAINER"
docker start "$CONTAINER" >/dev/null
if docker image inspect "${IMAGE}-rollback-${STAMP}" >/dev/null 2>&1; then
  docker tag "${IMAGE}-rollback-${STAMP}" "$IMAGE"
fi

ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf http://127.0.0.1:13360/health >/dev/null; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" != 1 ]]; then
  echo "回滚后的健康检查没过。容器名已经换回 ${CONTAINER}，请看 docker logs ${CONTAINER}。" >&2
  exit 1
fi

after=$(mktemp)
docker ps -a --format '{{.Names}} {{.State}}' | grep -v "^${CONTAINER} " | grep -v "^${CONTAINER}-failed-${STAMP} " | sort > "$after"
if ! diff -q "$before" "$after" >/dev/null; then
  echo "警告：回滚 dj-shim 时其他容器状态有变化。" >&2
  diff -u "$before" "$after" >&2 || true
fi
rm -f "$before" "$after"
echo "已回滚 ${CONTAINER} 到备份 ${STAMP}。失败版本保留为 ${CONTAINER}-failed-${STAMP}。"
echo "镜像 ${IMAGE} 指回 ${IMAGE}-rollback-${STAMP}。"
