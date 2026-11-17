#!/usr/bin/env bash
# Replace only the dj-shim container on the dujiao-test sandbox.
# Usage (on that machine, as a user who can talk to docker):
#   bash update-dj-shim.sh /path/to/dujiao-shim-v0.1.tar.gz
#
# Keeps shim.env (it may only hold secrets), volumes, the docker network and every other container.
# The running container's own environment is the upstream check. The host must be exactly mock16688.
# LIVE=1 against that host is purchase mode "mock", not formal, and is not rolled back for that reason.
# Never prints secret values. A failed health check puts the previous container back.
set -euo pipefail

ROOT=${ROOT:-/data/workspace/dujiao-test}
SRC=${SRC:-$ROOT/shimsrc}
ENV_FILE=${ENV_FILE:-$ROOT/shim.env}
IMAGE=${IMAGE:-gsx/16688-mcp:test}
CONTAINER=${CONTAINER:-dj-shim}
TARBALL=${1:-}

if [[ -z "$TARBALL" || ! -f "$TARBALL" ]]; then
  echo "用法: bash update-dj-shim.sh <源码包.tar.gz>" >&2
  exit 2
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "找不到环境文件（路径已配置为 shim.env），停止。" >&2
  exit 2
fi
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "没有名为 ${CONTAINER} 的容器，停止。本脚本只替换已有测试容器。" >&2
  exit 2
fi

STAMP=$(date +%Y%m%d%H%M%S)
BACK=$ROOT/backup/dj-shim-$STAMP
mkdir -p "$BACK"
chmod 700 "$ROOT/backup" "$BACK"

docker inspect "$CONTAINER" > "$BACK/inspect.json"
chmod 600 "$BACK/inspect.json"
# Full runtime env, for rollback fidelity. Not printed.
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" > "$BACK/container.env"
chmod 600 "$BACK/container.env"
cp -a "$ENV_FILE" "$BACK/shim.env"
chmod 600 "$BACK/shim.env"

# Extract first so the preflight module is the one inside this package. The container is still running.
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
tar -xzf "$TARBALL" -C "$STAGE"
if [[ ! -f "$STAGE/Dockerfile" && ! -f "$STAGE/pyproject.toml" ]]; then
  inner=$(find "$STAGE" -mindepth 2 -maxdepth 2 -name Dockerfile -printf '%h\n' | head -n 1)
  if [[ -z "$inner" ]]; then
    echo "源码包里没有 Dockerfile，停止。容器还是原来的。" >&2
    exit 2
  fi
  STAGE_SRC=$inner
else
  STAGE_SRC=$STAGE
fi
if [[ ! -f "$STAGE_SRC/deploy/sandbox/preflight.py" ]]; then
  echo "源码包里没有 deploy/sandbox/preflight.py，停止。容器还是原来的。" >&2
  exit 2
fi

python3 - "$ENV_FILE" "$BACK/container.env" "$STAGE_SRC" <<'PY'
import sys
sys.path.insert(0, sys.argv[3] + "/deploy/sandbox")
from preflight import confirm_test_upstream, load_env
host = confirm_test_upstream(load_env(sys.argv[2]), load_env(sys.argv[1]))
print(f"上游主机确认：{host}。正式采购开关关闭。shim.env 未修改。")
PY

# Remember everyone else so we can prove this script did not stop them.
docker ps -a --format '{{.Names}} {{.State}}' | grep -v "^${CONTAINER} " | sort > "$BACK/others-before.txt"
python3 - "$BACK/inspect.json" "$BACK" <<'PY'
import json, shutil, sys
from pathlib import Path
info = json.load(open(sys.argv[1]))[0]
back = Path(sys.argv[2])
for mount in info.get("Mounts") or []:
    src = Path(mount.get("Source") or "")
    files = [src] if src.is_file() else list(src.iterdir()) if src.is_dir() else []
    for child in files:
        if child.name in {"shim.db", "catalog.json"} or child.suffix in {".db"}:
            shutil.copy2(child, back / child.name)
print("已备份数据库和目录文件（不含令牌输出）。")
PY

OLD_IMAGE=$(docker inspect -f '{{.Image}}' "$CONTAINER")
echo "$OLD_IMAGE" > "$BACK/image-id"
docker tag "$IMAGE" "${IMAGE}-rollback-${STAMP}"

python3 - "$STAGE_SRC" "$SRC" <<'PY'
import shutil, sys
from pathlib import Path
src, dest = Path(sys.argv[1]), Path(sys.argv[2])
dest.mkdir(parents=True, exist_ok=True)
for name in ("src", "deploy", "tests", ".github"):
    target = dest / name
    if target.exists():
        shutil.rmtree(target)
    incoming = src / name
    if incoming.exists():
        shutil.copytree(incoming, target)
for name in ("Dockerfile", "pyproject.toml", "README.md", "LICENSE", ".dockerignore", ".gitignore"):
    incoming = src / name
    if incoming.exists():
        shutil.copy2(incoming, dest / name)
print("已写入构建目录，未触碰 shim.env / token / data。")
PY

echo "构建 ${IMAGE}（旧镜像已标为 ${IMAGE}-rollback-${STAMP}）"
docker build -t "$IMAGE" "$SRC"

# Swap the container. The previous one is kept stopped under a new name.
docker stop "$CONTAINER" >/dev/null
docker rename "$CONTAINER" "${CONTAINER}-prev-${STAMP}"

run_new() {
  python3 - "$BACK/inspect.json" "$BACK/container.env" "$IMAGE" "$CONTAINER" "$STAGE_SRC" <<'PY'
import json, subprocess, sys
sys.path.insert(0, sys.argv[5] + "/deploy/sandbox")
from preflight import plan_run
info = json.load(open(sys.argv[1]))[0]
args = plan_run(info, sys.argv[2], sys.argv[3], sys.argv[4])
subprocess.check_call(args)
nets = list((info.get("NetworkSettings") or {}).get("Networks") or {})
for extra in nets[1:]:
    subprocess.check_call(["docker", "network", "connect", extra, sys.argv[4]])
PY
}

rollback() {
  echo "健康检查没过，正在把 ${CONTAINER}-prev-${STAMP} 换回去。" >&2
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker rename "${CONTAINER}-prev-${STAMP}" "$CONTAINER"
  docker start "$CONTAINER" >/dev/null
  docker tag "${IMAGE}-rollback-${STAMP}" "$IMAGE"
  echo "已回滚。其他容器没有被这个脚本重启。" >&2
  exit 1
}

if ! run_new; then
  rollback
fi

ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf http://127.0.0.1:13360/health >/dev/null && curl -sf http://127.0.0.1:13361/health >/dev/null; then
    ok=1
    break
  fi
  sleep 1
done
[[ "$ok" == 1 ]] || rollback

if ! python3 - "$BACK/container.env" "$STAGE_SRC" <<'PY'
import json, urllib.request, sys
sys.path.insert(0, sys.argv[2] + "/deploy/sandbox")
from preflight import mode_allows_keep
token = ""
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if line.startswith("SHIM_ADMIN_TOKEN="):
        token = line.split("=", 1)[1].strip().strip('"').strip("'")
req = urllib.request.Request("http://127.0.0.1:13360/ops/api/overview",
                              headers={"x-admin-token": token})
with urllib.request.urlopen(req, timeout=5) as r:
    body = json.load(r)
mode = body.get("purchase_mode")
print(f"采购模式：{mode}")
# mock + SHIM_LIVE_PURCHASE=1 is "mock". Only an official host is "formal".
if not body.get("ok") or not mode_allows_keep(mode):
    sys.exit(1)
PY
then
  rollback
fi

ops=$(curl -sf http://127.0.0.1:13360/ops)
redeem=$(curl -sf http://127.0.0.1:13361/)
case $ops in
  *管理令牌*) ;;
  *) echo "ops 没有登录墙" >&2; rollback ;;
esac
case $ops in
  *data-pa*|*货源池*) echo "未登录就能看到采购台" >&2; rollback ;;
esac
case $redeem in
  *管理令牌*|*货源池*|*ops/api*) echo "兑换页带出了后台" >&2; rollback ;;
esac

docker ps -a --format '{{.Names}} {{.State}}' | grep -v "^${CONTAINER} " | grep -v "^${CONTAINER}-prev-${STAMP} " | sort > "$BACK/others-after.txt"
if ! diff -q "$BACK/others-before.txt" "$BACK/others-after.txt" >/dev/null; then
  echo "其他容器状态变了，回滚 dj-shim。" >&2
  diff -u "$BACK/others-before.txt" "$BACK/others-after.txt" >&2 || true
  rollback
fi

echo "dj-shim 已换成 ${IMAGE}。上一只容器保留为 ${CONTAINER}-prev-${STAMP}。"
echo "备份目录：${BACK}"
echo "回滚：bash deploy/sandbox/rollback-dj-shim.sh ${STAMP}"
echo "端口：127.0.0.1:13360 采购台，127.0.0.1:13361 兑换页。没有公网端口。"
