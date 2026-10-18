# 16688-mcp

把 [16688](https://www.16688.com.cn) 数字卡密交易平台的开放 API（网关 `https://www.16688.com.cn/openApi`）包成一个 MCP 服务，让 Claude 等 AI 助手可以管理店铺：商品、店内分类、卡密库存、货源采购、店铺订单、投诉和钱包余额。

## 工具

| 工具 | 作用 |
| --- | --- |
| `list_endpoints(group?)` | 列出 43 个接口的路径、说明、参数（`*` 为必填）以及本服务是否放行 |
| `call_api(path, params)` | 对一个接口发起已签名的 POST 请求，返回平台原始 JSON |

`app_id`、`timestamp`、`nonce`、`sign` 由服务端自动补上，调用方只传业务参数。签名按官方文档：
`HMAC-SHA256(secret, "POST\n<控制器小写>\n<动作小写>\n<按键名排序的 k=v&...>")`，结果为小写十六进制。测试里用官方 PHP 示例逐字节对拍。

## 权限

- **默认拒绝 5 个会花钱、退款或删除的接口**：`purchase/create`（扣运营钱包采购）、`order/refund`、`complaint/refund`、`goods/delete`、`stockSecret/invalidateAll`。要放开，在 `.env` 里设 `OPEN16688_ALLOW_DANGEROUS`（逗号分隔路径，或 `*`）。
- `OPEN16688_READ_ONLY=1`：只放行查询类接口。
- 其余接口（建商品、改加价、上下架、上传卡密、人工发货标记、投诉留言等）默认放行。
- HTTP 层要求 `Authorization: Bearer <MCP_BEARER_TOKEN>`；`MCP_ALLOWED_HOSTS` 固定 Host 头。

## 部署

用 root 在服务器上执行 `deploy/install.sh`。脚本会依次提示输入：

1. GitHub 只读令牌（仓库是私有的才需要；只用于本次 clone/pull，不写盘）。用 fine-grained token，只选本仓库，权限只给 Contents: Read-only。
2. 首次安装时的 app_id 和 secret（secret 输入时不回显，只写进 `/opt/16688-mcp/.env`，权限 600）。

脚本放到服务器上的办法：把 `deploy/install.sh` 内容粘贴成 `/root/install-16688-mcp.sh`，然后 `bash /root/install-16688-mcp.sh`。以后更新重跑同一条命令即可。

容器只监听 `127.0.0.1:3320`，只读根文件系统、去掉全部 capability。前面用 Cloudflare Tunnel + Access 暴露给 MCP Portal。

## 开发

```bash
pip install -e '.[test]'
pytest -q          # 签名对拍需要本机有 php
```

接口目录 `src/open16688_mcp/endpoints.json` 由官方文档整理而来（2026-10-18 版，43 个接口）。

## 许可

MIT
