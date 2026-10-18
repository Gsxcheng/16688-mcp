import json

import anyio
import httpx
import pytest

from open16688_mcp.catalog import DANGEROUS, ENDPOINTS
from open16688_mcp.server import build_app, build_server
from open16688_mcp.sign import sign

ENV = {"OPEN16688_APP_ID": "2088000000000000", "OPEN16688_SECRET": "s3cret"}


def call(server, tool, payload):
    async def run():
        result = await server.call_tool(tool, payload)
        return result.content[0].text if hasattr(result, "content") else result[0][0].text
    return anyio.run(run)


@pytest.fixture()
def captured():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"code": 1, "msg": "success", "time": 1, "data": {"total": 0}})

    return seen, httpx.MockTransport(handler)


def test_catalog_has_all_43_endpoints():
    assert len(ENDPOINTS) == 43
    assert DANGEROUS <= set(ENDPOINTS)


def test_call_api_signs_and_posts(captured):
    seen, transport = captured
    server = build_server(ENV, transport=transport)
    out = call(server, "call_api", {"path": "/openApi/order/list", "params": {"page_no": 1, "status": 1}})
    assert out.startswith("http=200") and json.loads(out.split("\n", 1)[1])["code"] == 1
    req = seen[0]
    assert str(req.url) == "https://www.16688.com.cn/openApi/order/list"
    body = json.loads(req.content)
    assert body["app_id"] == ENV["OPEN16688_APP_ID"] and body["page_no"] == 1
    assert body["sign"] == sign("POST", "/openApi/order/list", body, ENV["OPEN16688_SECRET"])
    assert ENV["OPEN16688_SECRET"] not in req.content.decode()


def test_dangerous_refused_by_default(captured):
    seen, transport = captured
    server = build_server(ENV, transport=transport)
    out = call(server, "call_api", {"path": "/openApi/purchase/create", "params": {"goods_no": "SG1"}})
    assert out.startswith("REFUSED:") and not seen


def test_dangerous_allowed_when_listed(captured):
    seen, transport = captured
    server = build_server({**ENV, "OPEN16688_ALLOW_DANGEROUS": "/openApi/purchase/create"}, transport=transport)
    assert call(server, "call_api", {"path": "/openApi/purchase/create", "params": {"goods_no": "SG1"}}).startswith("http=200")
    assert call(server, "call_api", {"path": "/openApi/order/refund", "params": {"trade_no": "T1"}}).startswith("REFUSED:")


def test_unknown_path_and_missing_params_refused(captured):
    seen, transport = captured
    server = build_server(ENV, transport=transport)
    assert "unknown endpoint" in call(server, "call_api", {"path": "/openApi/x/y", "params": {}})
    assert "goods_no" in call(server, "call_api", {"path": "/openApi/goods/detail", "params": {}})
    assert not seen


def test_read_only_mode(captured):
    seen, transport = captured
    server = build_server({**ENV, "OPEN16688_READ_ONLY": "1"}, transport=transport)
    assert call(server, "call_api", {"path": "/openApi/merchant/balance"}).startswith("http=200")
    assert "READ_ONLY" in call(server, "call_api", {"path": "/openApi/order/deliver", "params": {"trade_no": "T1"}})


def test_missing_credentials_reported(captured):
    seen, transport = captured
    server = build_server({}, transport=transport)
    assert call(server, "call_api", {"path": "/openApi/merchant/balance"}).startswith("ERROR:")


def test_list_endpoints_marks_access(captured):
    _, transport = captured
    server = build_server(ENV, transport=transport)
    items = json.loads(call(server, "list_endpoints", {"group": "采购"}))
    assert len(items) == 10
    by_path = {i["path"]: i for i in items}
    assert by_path["/openApi/purchase/create"]["access"] == "dangerous (refused)"
    assert by_path["/openApi/purchase/query"]["access"] == "read"
    assert "goods_no* (string)" in by_path["/openApi/purchase/create"]["params"][0]


def test_app_requires_long_token():
    with pytest.raises(SystemExit):
        build_app({"MCP_BEARER_TOKEN": "short"})


def test_bearer_guard_rejects_missing_token():
    app = build_app({"MCP_BEARER_TOKEN": "x" * 40, **ENV})
    sent = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        await app({"type": "http", "method": "POST", "path": "/mcp", "headers": []}, receive, send)

    anyio.run(run)
    assert sent[0]["status"] == 401
