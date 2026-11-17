#!/usr/bin/env bash
# Replace only the dj-shim container on the dujiao-test sandbox.
# Usage (on that machine, as a user who can talk to docker):
#   bash update-dj-shim.sh /path/to/dujiao-shim-v0.1.tar.gz
#
# Keeps shim.env, volumes, the docker network and every other container.
# Refuses to run unless the upstream host contains "mock16688" and formal purchase is off.
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

python3 - "$ENV_FILE" "$BACK/container.env" <<'PY'
import sys
from urllib.parse import urlparse

def load(path):
    out = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v.strip().strip('"').strip("'")
    return out

file_env, run_env = load(sys.argv[1]), load(sys.argv[2])
def host(env):
    return (urlparse(env.get("OPEN16688_BASE_URL", "")).hostname or "").lower()

file_host, run_host = host(file_env), host(run_env)
if not file_host or not run_host:
    sys.exit("停止：环境里没有 OPEN16688_BASE_URL。")
if file_host != run_host:
    sys.exit("停止：shim.env 和正在运行的容器上游主机不一致。没有改任何容器。")
if "mock16688" not in file_host or file_host == "16688.com.cn" or file_host.endswith(".16688.com.cn"):
    sys.exit(f"停止：上游主机不是 mock16688（看到的主机名是 {file_host}）。没有改任何容器。")
for env in (file_env, run_env):
    if env.get("SHIM_AUTHORIZE_FORMAL_PURCHASE", "").lower() in {"1", "true", "yes"}:
        sys.exit("停止：SHIM_AUTHORIZE_FORMAL_PURCHASE 已打开。测试机不允许正式采购。")
print(f"上游主机确认：{file_host}。正式采购开关关闭。")
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

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
tar -xzf "$TARBALL" -C "$STAGE"
# Archives created with git are wrapped in one directory; a raw tree is not.
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
  python3 - "$BACK/inspect.json" "$BACK/container.env" "$IMAGE" "$CONTAINER" "$STAMP" <<'PY'
import json, subprocess, sys
inspect_path, env_path, image, name, stamp = sys.argv[1:]
info = json.load(open(inspect_path))[0]
hc = info["HostConfig"]
args = ["docker", "run", "-d", "--name", name, "--env-file", env_path]
restart = (hc.get("RestartPolicy") or {}).get("Name") or "unless-stopped"
if restart and restart != "no":
    args += ["--restart", restart]
nets = list((info.get("NetworkSettings") or {}).get("Networks") or {})
if nets:
    args += ["--network", nets[0]]
for mount in info.get("Mounts") or []:
    if mount.get("Type") == "bind":
        spec = f"{mount['Source']}:{mount['Destination']}"
        if not mount.get("RW", True):
            spec += ":ro"
        args += ["-v", spec]
    elif mount.get("Type") == "volume" and mount.get("Name"):
        args += ["-v", f"{mount['Name']}:{mount['Destination']}"]
if hc.get("ReadonlyRootfs"):
    args.append("--read-only")
for path, opts in (hc.get("Tmpfs") or {}).items():
    args += ["--tmpfs", f"{path}:{opts}" if opts else path]
if hc.get("Memory"):
    args += ["--memory", str(hc["Memory"])]
if hc.get("NanoCpus"):
    args += ["--cpus", str(hc["NanoCpus"] / 1e9)]
if hc.get("PidsLimit"):
    args += ["--pids-limit", str(hc["PidsLimit"])]
for cap in hc.get("CapDrop") or []:
    args += ["--cap-drop", cap]
for opt in hc.get("SecurityOpt") or []:
    args += ["--security-opt", opt]
# Loopback only. Do not reuse whatever the old container published.
args += ["-p", "127.0.0.1:13360:3000", "-p", "127.0.0.1:13361:3001", image]
cmd = info.get("Config", {}).get("Cmd") or ["dujiao-shim"]
args += cmd
subprocess.check_call(args)
for extra in nets[1:]:
    subprocess.check_call(["docker", "network", "connect", extra, name])
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

if ! python3 - "$BACK/container.env" <<'PY'
import json, urllib.request, sys
token = ""
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if line.startswith("SHIM_ADMIN_TOKEN="):
        token = line.split("=", 1)[1].strip()
req = urllib.request.Request("http://127.0.0.1:13360/ops/api/overview",
                              headers={"x-admin-token": token})
with urllib.request.urlopen(req, timeout=5) as r:
    body = json.load(r)
mode = body.get("purchase_mode")
print(f"采购模式：{mode}")
if mode == "formal" or not body.get("ok"):
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
