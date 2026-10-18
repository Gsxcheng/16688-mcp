#!/usr/bin/env bash
# 在服务器上安装 / 更新 16688-mcp。用 root 执行：bash deploy/install.sh
# 重复执行 = 拉最新代码并重建；访问令牌和 16688 凭证保留在 /opt/16688-mcp/.env（600）。
set -euo pipefail
D=/opt/16688-mcp
REPO=${REPO:-https://github.com/Gsxcheng/16688-mcp}
IMG=gsx/16688-mcp:latest
PUBLIC_HOST=${PUBLIC_HOST:-16688-mcp-origin.gsxcheng.ltd}
PORT=${PORT:-3320}

echo "== 1/5 拉取代码（私有仓库：输入 GitHub 只读令牌，只用于这一次拉取，不保存；公开仓库直接回车）"
read -rsp "GitHub 令牌: " GH_TOKEN; echo
GIT_AUTH=()
if [ -n "$GH_TOKEN" ]; then
  GIT_AUTH=(-c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)")
fi
if [ -d "$D/.git" ]; then
  git "${GIT_AUTH[@]}" -C "$D" pull --ff-only
else
  [ -e "$D" ] && mv "$D" "$D.old.$(date +%s)"
  git "${GIT_AUTH[@]}" clone --depth 1 "$REPO" "$D"
fi
unset GH_TOKEN GIT_AUTH
chmod 700 "$D"
cd "$D"
git log -1 --format='当前版本：%h %s'

echo "== 2/5 访问令牌（已存在则保留）"
umask 077
touch .env
grep -q '^MCP_BEARER_TOKEN=.\{32,\}' .env || echo "MCP_BEARER_TOKEN=$(openssl rand -hex 32)" >> .env
grep -q '^MCP_ALLOWED_HOSTS=' .env || echo "MCP_ALLOWED_HOSTS=$PUBLIC_HOST" >> .env

echo "== 3/5 16688 接入凭证（已存在则跳过；输入时不显示）"
if ! grep -q '^OPEN16688_APP_ID=.' .env; then
  read -rp "app_id: " APP_ID
  echo "OPEN16688_APP_ID=$APP_ID" >> .env
fi
if ! grep -q '^OPEN16688_SECRET=.' .env; then
  read -rsp "secret（开放平台点「显示密钥」后复制）: " SECRET; echo
  echo "OPEN16688_SECRET=$SECRET" >> .env
fi
chmod 600 .env

echo "== 4/5 构建镜像（国内 pip 源）"
docker build -t "$IMG" --build-arg PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple .

echo "== 5/5 启动容器 16688-mcp（127.0.0.1:$PORT）"
docker rm -f 16688-mcp >/dev/null 2>&1 || true
docker run -d --name 16688-mcp --restart unless-stopped \
  --env-file .env -p 127.0.0.1:$PORT:3000 \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --memory 128m --cpus 0.5 --pids-limit 64 \
  --log-opt max-size=5m --log-opt max-file=2 \
  "$IMG" >/dev/null
sleep 4
docker ps --filter name=16688-mcp --format '{{.Names}}  {{.Status}}  {{.Ports}}'
echo -n "无令牌访问应返回 401： "; curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:$PORT/mcp
echo -n "凭证自检（查钱包余额，应看到 \"code\":1）： "
docker exec 16688-mcp python -c "
import json; from open16688_mcp.server import build_server
import anyio
s = build_server()
r = anyio.run(lambda: s.call_tool('call_api', {'path': '/openApi/merchant/balance'}))
t = r.content[0].text if hasattr(r, 'content') else r[0][0].text
print(t.split(chr(10), 1)[-1][:300])
"

cat <<NEXT

== 安装完成。Cloudflare MCP Portal 里新增服务器时，Authorization 标头值 = "Bearer " + 下面这串：

  grep MCP_BEARER_TOKEN $D/.env

隧道路由：$PUBLIC_HOST → http://127.0.0.1:$PORT
NEXT
