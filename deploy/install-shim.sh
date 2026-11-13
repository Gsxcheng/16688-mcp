#!/usr/bin/env bash
# 在服务器上安装 / 更新 dujiao-shim（16688 → Dujiao-Next 上游转接器）。用 root 执行：bash deploy/install-shim.sh
# 默认是「模拟模式」：不会真实向 16688 下单。真实采购要显式开启：LIVE=1 bash /opt/dujiao-shim/deploy/install-shim.sh
set -euo pipefail
D=/opt/dujiao-shim
REPO=${REPO:-https://github.com/Gsxcheng/16688-mcp}
IMG=gsx/16688-mcp:latest
PORT=${PORT:-3360}
MCP_ENV=/opt/16688-mcp/.env
GW=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 172.17.0.1)

echo "== 1/5 拉取代码"
if [ -d "$D/.git" ]; then
  git -C "$D" pull --ff-only
else
  [ -e "$D" ] && mv "$D" "$D.old.$(date +%s)"
  git clone --depth 1 "$REPO" "$D"
fi
cd "$D"
git log -1 --format='当前版本：%h %s'

echo "== 2/5 配置（已存在则保留）"
umask 077
touch .env
grep -q '^SHIM_API_KEY=.' .env || echo "SHIM_API_KEY=gg-upstream" >> .env
grep -q '^SHIM_API_SECRET=.\{32,\}' .env || echo "SHIM_API_SECRET=$(openssl rand -hex 32)" >> .env
grep -q '^SHIM_ADMIN_TOKEN=.\{32,\}' .env || echo "SHIM_ADMIN_TOKEN=$(openssl rand -hex 32)" >> .env
grep -q '^SHIM_CALLBACK_URL=' .env || echo "SHIM_CALLBACK_URL=https://shop.gsxcheng.ltd/api/v1/upstream/callback" >> .env
grep -q '^SHIM_MAX_ORDERS_PER_DAY=' .env || echo "SHIM_MAX_ORDERS_PER_DAY=20" >> .env
grep -q '^SHIM_MIN_BALANCE=' .env || echo "SHIM_MIN_BALANCE=0" >> .env
if ! grep -q '^OPEN16688_APP_ID=.' .env; then
  if [ -f "$MCP_ENV" ] && grep -q '^OPEN16688_APP_ID=.' "$MCP_ENV"; then
    grep -E '^OPEN16688_(APP_ID|SECRET)=' "$MCP_ENV" >> .env
    echo "已复用 16688-mcp 的接入凭证"
  else
    echo "找不到 16688-mcp 的凭证，请手动补充 OPEN16688_APP_ID / OPEN16688_SECRET 到 $D/.env"
  fi
fi
if ! grep -q '^SHIM_FEISHU_WEBHOOK=' .env; then
  read -rp "飞书群机器人 webhook 地址（没有就直接回车，之后可在 .env 里补）: " HOOK
  echo "SHIM_FEISHU_WEBHOOK=$HOOK" >> .env
fi
if [ "${LIVE:-0}" = "1" ]; then
  sed -i '/^SHIM_LIVE_PURCHASE=/d' .env; echo "SHIM_LIVE_PURCHASE=1" >> .env
elif ! grep -q '^SHIM_LIVE_PURCHASE=' .env; then
  echo "SHIM_LIVE_PURCHASE=0" >> .env
fi
chmod 600 .env

echo "== 3/5 商品目录（已存在则保留；改完重跑本脚本生效）"
mkdir -p data
[ -f data/catalog.json ] || cp deploy/shim-catalog.example.json data/catalog.json
chown -R 10001:10001 data

echo "== 4/5 构建镜像（国内 pip 源）"
docker build -t "$IMG" --build-arg PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple .

echo "== 5/5 启动容器 dujiao-shim（127.0.0.1:$PORT 和 $GW:$PORT）"
docker rm -f dujiao-shim >/dev/null 2>&1 || true
docker run -d --name dujiao-shim --restart unless-stopped --env-file .env -e SHIM_DB=/data/shim.db -e SHIM_CATALOG=/data/catalog.json -v "$D/data:/data" -p 127.0.0.1:$PORT:3000 -p $GW:$PORT:3000 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m --cap-drop ALL --security-opt no-new-privileges:true --memory 128m --cpus 0.5 --pids-limit 64 --log-opt max-size=5m --log-opt max-file=2 "$IMG" dujiao-shim >/dev/null
sleep 4
docker ps --filter name=dujiao-shim --format '{{.Names}}  {{.Status}}  {{.Ports}}'
echo -n "健康检查： "; curl -s http://127.0.0.1:$PORT/health; echo
if grep -q '^SHIM_LIVE_PURCHASE=1' .env; then echo "模式：真实采购（会扣 16688 运营钱包）"; else echo "模式：模拟（不会真实下单）"; fi

cat <<NEXT

== 安装完成。在 Dujiao 后台新增「对接连接」时填：
  站点地址： http://$GW:$PORT
  API Key：  gg-upstream
  API Secret：执行  grep SHIM_API_SECRET $D/.env  取值
  回调地址（Callback URL）：https://shop.gsxcheng.ltd/api/v1/upstream/callback
人工采购后台令牌：执行  grep SHIM_ADMIN_TOKEN $D/.env
NEXT
