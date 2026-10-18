"""16688-mcp: expose the 16688 Open API (https://www.16688.com.cn/openApi) as MCP tools."""
import hmac
import json
import os

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .catalog import DANGEROUS, ENDPOINTS, READ_ONLY, Policy
from .sign import signed_body

INSTRUCTIONS = (
    "Manages a 16688 (www.16688.com.cn) digital-goods shop through its Open API: goods, store "
    "categories, card-code stock, supply purchases, shop orders, complaints and wallet balance. "
    "Call list_endpoints first to see each endpoint's parameters, then call_api with the path and "
    "business parameters only; app_id, timestamp, nonce and sign are added by the server. "
    "Responses are the platform's JSON: code 1 = success, 0 = failure with the reason in msg. "
    "Endpoints that spend the wallet, refund or delete are refused unless the server owner enabled them."
)


def _summary(ep: dict, policy: Policy) -> dict:
    path = ep["path"]
    access = "read" if path in READ_ONLY else "write"
    if path in DANGEROUS:
        access = "dangerous (enabled)" if path in policy.allow_dangerous else "dangerous (refused)"
    if policy.read_only and path not in READ_ONLY:
        access = "write (refused: read-only server)"
    return {
        "path": path, "group": ep["group"], "name": ep["name"], "note": ep["note"], "access": access,
        "params": [f"{p['name']}{'*' if p['required'] else ''} ({p['type']}): {p['desc']}" for p in ep["params"]],
    }


def build_server(env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None) -> MCPServer:
    env = dict(os.environ if env is None else env)
    app_id = env.get("OPEN16688_APP_ID", "")
    secret = env.get("OPEN16688_SECRET", "")
    base = env.get("OPEN16688_BASE_URL", "https://www.16688.com.cn").rstrip("/")
    timeout = float(env.get("OPEN16688_TIMEOUT", "30"))
    max_out = int(env.get("OPEN16688_MAX_OUTPUT", "100000"))
    policy = Policy.from_env(env)
    client = httpx.Client(timeout=timeout, transport=transport,
                          headers={"User-Agent": "16688-mcp/0.1"})

    mcp = MCPServer(name="16688", instructions=INSTRUCTIONS)

    @mcp.tool()
    def list_endpoints(group: str | None = None) -> str:
        """List the Open API endpoints with their parameters (* = required) and whether this server allows them.

        group: optional filter, one of 商品, 商品分类, 平台货源分类, 卡密库存, 采购, 订单管理, 订单投诉, 账户, 通用功能.
        """
        eps = [e for e in ENDPOINTS.values() if not group or e["group"] == group]
        return json.dumps([_summary(e, policy) for e in eps], ensure_ascii=False, indent=1)

    @mcp.tool()
    def call_api(path: str, params: dict | None = None) -> str:
        """POST one signed request to the 16688 Open API and return the JSON response.

        path: endpoint path such as /openApi/order/list (see list_endpoints).
        params: business parameters only, e.g. {"page_no": 1, "status": 1}.
        """
        params = params or {}
        why = policy.refuse(path, params)
        if why:
            return f"REFUSED: {why}"
        if not app_id or not secret:
            return "ERROR: OPEN16688_APP_ID and OPEN16688_SECRET are not configured on the server"
        body = signed_body(path, params, app_id, secret)
        try:
            r = client.post(base + path, content=json.dumps(body, ensure_ascii=False).encode(),
                            headers={"Content-Type": "application/json"})
        except httpx.HTTPError as e:
            return f"ERROR: request failed: {type(e).__name__}: {e}"
        out = f"http={r.status_code}\n{r.text}"
        if len(out) > max_out:
            out = out[:max_out] + f"\n[truncated {len(out) - max_out} chars]"
        return out

    return mcp


def bearer_guard(app, token: str):
    """Reject HTTP requests whose Authorization header is not `Bearer <token>`."""
    want = f"Bearer {token}".encode()

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, want):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    return guarded


def build_app(env: dict[str, str] | None = None):
    env = dict(os.environ if env is None else env)
    token = env.get("MCP_BEARER_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("MCP_BEARER_TOKEN must be set to at least 32 characters")
    hosts = [h.strip() for h in env.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(
        # Behind a reverse proxy the Host header is the public name; set MCP_ALLOWED_HOSTS to pin it.
        enable_dns_rebinding_protection=bool(hosts),
        allowed_hosts=hosts,
    )
    http_app = build_server(env).streamable_http_app(
        streamable_http_path=env.get("MCP_PATH", "/mcp"),
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host="0.0.0.0",
    )
    return bearer_guard(http_app, token)


def main() -> None:
    import uvicorn

    uvicorn.run(build_app(), host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "3000")), log_level="info")


if __name__ == "__main__":
    main()
