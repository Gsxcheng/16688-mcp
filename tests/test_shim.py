import json
import time

import httpx
import pytest
from starlette.testclient import TestClient

from dujiao_shim import auth
from dujiao_shim.app import build_app
from dujiao_shim.config import Config, Product, Source
from dujiao_shim.fulfil import CALLBACK_MAX_ATTEMPTS

KEY, SECRET, ADMIN = "shimkey123", "s" * 40, "a" * 40
DUJIAO_CB = "https://shop.example.com/api/v1/upstream/callback"


class Upstream:
    """Fake 16688 + Dujiao callback + Feishu, routed by URL."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.quote = {"unit_price": 8, "total_amount": 8.22, "available_quantity": 9, "available_money": 50.0,
                      "balance_enough": True}
        self.create_status = 4
        self.create_exc: Exception | None = None
        self.query_status = 4
        self.callback_ok = True
        self.detail_stock = 9

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://www.16688.com.cn/openApi/"):
            path = req.url.path
            body = json.loads(req.content)
            self.calls.append((path, body))
            if path.endswith("goods/detail"):
                return httpx.Response(200, json={"code": 1, "data": {"goods_no": body["goods_no"], "unit_price": 8,
                                                                      "available_quantity": self.detail_stock}})
            if path.endswith("quotePrice"):
                return httpx.Response(200, json={"code": 1, "data": {**self.quote, "quantity": body["quantity"]}})
            if path.endswith("purchase/create"):
                if self.create_exc:
                    raise self.create_exc
                return httpx.Response(200, json={"code": 1, "data": {
                    "trade_no": "T1", "status": self.create_status, "total_amount": 8.22,
                    "cards": [{"code": "AAAA-BBBB"}] if self.create_status == 4 else [], "instruction": "use it"}})
            if path.endswith("purchase/query"):
                return httpx.Response(200, json={"code": 1, "data": {
                    "trade_no": body["trade_no"], "status": self.query_status,
                    "cards": [{"code": "CCCC"}] if self.query_status == 4 else []}})
        if url == DUJIAO_CB:
            self.calls.append(("callback", json.loads(req.content)))
            self.cb_headers = dict(req.headers)
            self.cb_body = req.content
            return httpx.Response(200 if self.callback_ok else 500, json={"ok": self.callback_ok})
        return httpx.Response(200, json={})

    def n(self, suffix: str) -> int:
        return sum(1 for p, _ in self.calls if p.endswith(suffix))


def make(tmp_path, up, live=True, sources=None, db="s.db", **over):
    prod = Product(id=1, sku_id=101, slug="g6", title="Gemini 6M", description="d", cost=8.22,
                   sources=sources if sources is not None else [Source("16688", goods_no="G1", max_unit_cost=9.0)])
    cfg = Config(api_key=KEY, api_secret=SECRET, admin_token=ADMIN, db_path=str(tmp_path / db),
                 products=[prod], app_id="app", secret="sec", live=live, callback_url=DUJIAO_CB, **over)
    app = build_app(cfg, transport=httpx.MockTransport(up), start_worker=False)
    return app, TestClient(app)


def call(client, method, path, body=b"", ts=None):
    if isinstance(body, dict):
        body = json.dumps(body, separators=(",", ":")).encode()
    headers = auth.signed_headers(KEY, SECRET, method, path, body, now=ts)
    return client.request(method, path, content=body, headers=headers)


def order(client, down="D1", qty=1):
    return call(client, "POST", "/api/v1/upstream/orders",
                {"sku_id": 101, "quantity": qty, "downstream_order_no": down}).json()


@pytest.fixture()
def up():
    return Upstream()


# ---- auth ---------------------------------------------------------------------------------------
def test_auth_rejects_bad_requests(tmp_path, up):
    _, c = make(tmp_path, up)
    assert c.post("/api/v1/upstream/ping").json()["error_code"] == "missing_auth_headers"
    bad = auth.signed_headers(KEY, "wrong-secret", "POST", "/api/v1/upstream/ping", b"")
    assert c.post("/api/v1/upstream/ping", headers=bad).json()["error_code"] == "invalid_signature"
    old = int(time.time()) - 300
    assert call(c, "POST", "/api/v1/upstream/ping", ts=old).json()["error_code"] == "timestamp_expired"
    wrong_key = auth.signed_headers("other", SECRET, "POST", "/api/v1/upstream/ping", b"")
    assert c.post("/api/v1/upstream/ping", headers=wrong_key).json()["error_code"] == "invalid_api_key"
    r = call(c, "POST", "/api/v1/upstream/ping").json()
    assert r["ok"] and r["protocol_version"] == "1.0"


def test_sign_string_matches_documented_format():
    assert auth.sign_string("post", "/x", "1", b"") == f"POST\n/x\n1\n{auth.EMPTY_MD5}"


# ---- catalog ------------------------------------------------------------------------------------
def test_products_stock_thresholds_and_detail(tmp_path, up):
    _, c = make(tmp_path, up)
    item = call(c, "GET", "/api/v1/upstream/products").json()["items"][0]
    assert item["skus"][0]["id"] == 101 and item["price_amount"] == "8.22"
    assert item["skus"][0]["stock_status"] == "low_stock" and item["skus"][0]["stock_quantity"] == 9
    up2 = Upstream()
    up2.detail_stock = 50
    _, c2 = make(tmp_path, up2, db="b.db")
    assert call(c2, "GET", "/api/v1/upstream/products").json()["items"][0]["skus"][0]["stock_status"] == "in_stock"
    up3 = Upstream()
    up3.detail_stock = 0
    _, c3 = make(tmp_path, up3, db="c.db")
    assert call(c3, "GET", "/api/v1/upstream/products").json()["items"][0]["skus"][0]["stock_status"] == "out_of_stock"
    assert call(c, "GET", "/api/v1/upstream/products/1").json()["product"]["id"] == 1
    assert call(c, "GET", "/api/v1/upstream/products/99").status_code == 404
    assert call(c, "GET", "/api/v1/upstream/categories").json()["categories"][0]["id"] == 1


def test_stock_falls_back_to_manual_source(tmp_path, up):
    up.detail_stock = 0
    _, c = make(tmp_path, up, sources=[Source("16688", goods_no="G1"), Source("manual", label="TG", stock=30)])
    sku = call(c, "GET", "/api/v1/upstream/products").json()["items"][0]["skus"][0]
    assert sku["stock_quantity"] == 30 and sku["stock_status"] == "in_stock"


# ---- 16688 orders -------------------------------------------------------------------------------
def test_live_order_delivers_and_calls_back(tmp_path, up):
    app, c = make(tmp_path, up)
    r = order(c)
    assert r["ok"] and r["status"] == "paid" and r["amount"] == "8.22"
    assert up.n("purchase/create") == 1
    create_body = [b for p, b in up.calls if p.endswith("purchase/create")][0]
    assert create_body["goods_no"] == "G1" and create_body["quantity"] == 1 and r["order_no"] in create_body["remark"]
    got = call(c, "GET", f"/api/v1/upstream/orders/{r['order_id']}").json()
    assert got["status"] == "delivered" and "AAAA-BBBB" in got["fulfillment"]["payload"]
    assert "use it" in got["fulfillment"]["payload"]
    app.state.fulfiller.tick()
    cb = [b for p, b in up.calls if p == "callback"][0]
    assert cb["downstream_order_no"] == "D1" and cb["status"] == "delivered"
    assert cb["fulfillment"]["payload"].startswith("AAAA-BBBB")
    ts = up.cb_headers["dujiao-next-timestamp"]
    assert up.cb_headers["dujiao-next-signature"] == auth.signature(SECRET, "POST", "/api/v1/upstream/callback", ts, up.cb_body)
    app.state.fulfiller.tick(time.time() + 1000)
    assert up.n("callback") == 1  # delivered once, not re-sent


def test_replay_is_idempotent(tmp_path, up):
    _, c = make(tmp_path, up)
    a, b = order(c), order(c)
    assert a["order_id"] == b["order_id"] and up.n("purchase/create") == 1


def test_dry_run_never_calls_create(tmp_path, up):
    app, c = make(tmp_path, up, live=False)
    r = order(c)
    assert r["ok"] and up.n("purchase/create") == 0 and up.n("quotePrice") == 1
    got = call(c, "GET", f"/api/v1/upstream/orders/{r['order_id']}").json()
    assert "DRY-RUN" in got["fulfillment"]["payload"]
    assert any("模拟模式" in m for m in app.state.notifier.sent)


def test_price_above_ceiling_is_refused_then_retry_allowed(tmp_path, up):
    app, c = make(tmp_path, up)
    up.quote["total_amount"] = 12.0
    r = order(c)
    assert r["ok"] is False and r["error_code"] == "price_exceeded" and up.n("purchase/create") == 0
    assert any("被拒绝" in m for m in app.state.notifier.sent)
    up.quote["total_amount"] = 8.22
    r2 = order(c)  # same downstream_order_no: Dujiao retry after the price came back down
    assert r2["ok"] and r2["order_id"] == r["order_id"] and up.n("purchase/create") == 1


def test_low_balance_refuses_and_floor_applies(tmp_path, up):
    app, c = make(tmp_path, up, min_balance=45.0)
    r = order(c)
    assert r["error_code"] == "insufficient_balance" and up.n("purchase/create") == 0
    up.quote.update(balance_enough=False, available_money=0)
    _, c2 = make(tmp_path, up, db="b.db")
    assert order(c2)["error_code"] == "insufficient_balance"


def test_out_of_stock_quote(tmp_path, up):
    _, c = make(tmp_path, up)
    up.quote["available_quantity"] = 0
    assert order(c)["error_code"] == "insufficient_stock"


def test_falls_back_to_manual_when_16688_refuses(tmp_path, up):
    up.quote["total_amount"] = 99
    app, c = make(tmp_path, up, sources=[Source("16688", goods_no="G1", max_unit_cost=9.0),
                                         Source("manual", label="TG-ProdSeller", stock=5)])
    r = order(c)
    assert r["ok"] and r["status"] == "paid" and up.n("purchase/create") == 0
    assert any("TG-ProdSeller" in m for m in app.state.notifier.sent)


def test_timeout_during_create_stops_and_never_retries(tmp_path, up):
    up.create_exc = httpx.ReadTimeout("boom")
    app, c = make(tmp_path, up, sources=[Source("16688", goods_no="G1", max_unit_cost=9.0),
                                         Source("manual", label="TG", stock=5)])
    r = order(c)
    assert r["ok"] and r["status"] == "paid"          # Dujiao keeps waiting, nothing is delivered
    row = app.state.store.get(r["order_id"])
    assert row["status"] == "needs_review" and row["supplier"] == "16688"
    assert any("不确定" in m for m in app.state.notifier.sent)
    up.create_exc = None
    assert order(c)["order_id"] == r["order_id"] and up.n("purchase/create") == 1   # replay does not re-buy
    assert up.n("purchase/query") == 0 and not app.state.store.polling_orders()


def test_pending_purchase_is_polled_until_shipped(tmp_path, up):
    up.create_status = 1
    app, c = make(tmp_path, up)
    r = order(c)
    assert app.state.store.get(r["order_id"])["status"] == "processing"
    up.query_status = 1
    app.state.fulfiller.tick()
    assert app.state.store.get(r["order_id"])["status"] == "processing"
    up.query_status = 4
    app.state.fulfiller.tick()
    assert "CCCC" in call(c, "GET", f"/api/v1/upstream/orders/{r['order_id']}").json()["fulfillment"]["payload"]


def test_closed_purchase_cancels_downstream(tmp_path, up):
    up.create_status = 1
    app, c = make(tmp_path, up)
    r = order(c)
    up.query_status = 2
    app.state.fulfiller.tick()
    assert call(c, "GET", f"/api/v1/upstream/orders/{r['order_id']}").json()["status"] == "canceled"


def test_daily_cap(tmp_path, up):
    app, c = make(tmp_path, up, max_orders_per_day=1)
    assert order(c, "D1")["ok"]
    assert order(c, "D2")["error_code"] == "rate_limited"


def test_recover_marks_interrupted_purchase(tmp_path, up):
    app, c = make(tmp_path, up)
    row = app.state.store.create(101, 1, 1, "DX", "")
    app.state.store.update(row["id"], supplier="16688")
    app.state.fulfiller.recover()
    assert app.state.store.get(row["id"])["status"] == "needs_review"


def test_bad_requests(tmp_path, up):
    _, c = make(tmp_path, up)
    assert call(c, "POST", "/api/v1/upstream/orders", b"not json").json()["error_code"] == "bad_request"
    assert call(c, "POST", "/api/v1/upstream/orders", {"sku_id": 999, "quantity": 1}).json()["error_code"] == "sku_unavailable"
    assert call(c, "POST", "/api/v1/upstream/orders", {"sku_id": 101, "quantity": 0}).json()["error_code"] == "bad_request"
    assert call(c, "GET", "/api/v1/upstream/orders/12345").status_code == 404


def test_cancel_rules(tmp_path, up):
    app, c = make(tmp_path, up)
    done = order(c)
    assert call(c, "POST", f"/api/v1/upstream/orders/{done['order_id']}/cancel").status_code == 409
    row = app.state.store.create(101, 1, 1, "M1", "")
    app.state.store.update(row["id"], status="awaiting_manual")
    assert call(c, "POST", f"/api/v1/upstream/orders/{row['id']}/cancel").json()["ok"]


# ---- callbacks ----------------------------------------------------------------------------------
def test_failed_callback_backs_off_then_gives_up(tmp_path, up):
    up.callback_ok = False
    app, c = make(tmp_path, up)
    r = order(c)
    now = time.time()
    for i in range(CALLBACK_MAX_ATTEMPTS):
        app.state.fulfiller.tick(now + i * 4000)
    row = app.state.store.get(r["order_id"])
    assert row["callback_state"] == "failed" and row["callback_attempts"] == CALLBACK_MAX_ATTEMPTS
    assert any("回调多次失败" in m for m in app.state.notifier.sent)


def test_no_downstream_no_callback(tmp_path, up):
    app, c = make(tmp_path, up)
    call(c, "POST", "/api/v1/upstream/orders", {"sku_id": 101, "quantity": 1})
    app.state.fulfiller.tick()
    assert up.n("callback") == 0


# ---- manual admin -------------------------------------------------------------------------------
def test_manual_order_admin_flow(tmp_path, up):
    app, c = make(tmp_path, up, sources=[Source("manual", label="TG-Paglu", stock=5)])
    r = order(c)
    assert r["ok"] and up.n("purchase/create") == 0
    h = {"x-admin-token": ADMIN}
    assert c.get("/admin/api/pending").status_code == 401
    pend = c.get("/admin/api/pending", headers=h).json()["orders"]
    assert pend[0]["order_no"] == r["order_no"] and pend[0]["source"] == "TG-Paglu"
    assert c.post("/admin/api/deliver", json={"order_no": r["order_no"], "payload": ""}, headers=h).status_code == 400
    assert c.post("/admin/api/deliver", json={"order_no": r["order_no"], "payload": "CARD-1"}, headers=h).json()["ok"]
    assert c.post("/admin/api/deliver", json={"order_no": r["order_no"], "payload": "X"}, headers=h).status_code == 404
    app.state.fulfiller.tick()
    cb = [b for p, b in up.calls if p == "callback"][0]
    assert cb["status"] == "delivered" and cb["fulfillment"]["payload"] == "CARD-1"
    assert c.get("/admin").status_code == 200


def test_manual_admin_cancel_and_needs_review(tmp_path, up):
    app, c = make(tmp_path, up, sources=[Source("manual", label="TG", stock=5)])
    r = order(c)
    h = {"x-admin-token": ADMIN}
    assert c.post("/admin/api/cancel", json={"order_no": r["order_no"], "reason": "no stock"}, headers=h).json()["ok"]
    assert call(c, "GET", f"/api/v1/upstream/orders/{r['order_id']}").json()["status"] == "canceled"


def test_manual_source_without_stock_is_refused(tmp_path, up):
    _, c = make(tmp_path, up, sources=[Source("manual", label="TG", stock=0)])
    assert order(c)["error_code"] == "sku_unavailable"


def test_health_and_catalog_loader(tmp_path, up):
    from dujiao_shim.config import load_products, from_env
    _, c = make(tmp_path, up)
    assert c.get("/health").json() == {"ok": True, "live": True, "supplier_configured": True}
    f = tmp_path / "catalog.json"
    f.write_text(json.dumps([{"id": 1, "sku_id": 101, "slug": "a", "title": "A", "description": "", "cost": 1.0,
                              "sources": [{"type": "16688", "goods_no": "G1", "max_unit_cost": 2}]}]))
    assert load_products(str(f))[0].sources[0].goods_no == "G1"
    env = {"SHIM_API_KEY": "k" * 8, "SHIM_API_SECRET": "s" * 32, "SHIM_ADMIN_TOKEN": "t" * 32,
           "SHIM_CATALOG": str(f), "SHIM_DB": str(tmp_path / "e.db"), "SHIM_LIVE_PURCHASE": "1"}
    assert from_env(env).live is True
    with pytest.raises(SystemExit):
        from_env({**env, "SHIM_API_SECRET": "short"})
    f.write_text(json.dumps([{"id": 1, "sku_id": 1, "slug": "a", "title": "", "description": "", "cost": 1},
                             {"id": 2, "sku_id": 1, "slug": "b", "title": "", "description": "", "cost": 1}]))
    with pytest.raises(ValueError):
        load_products(str(f))


def test_installer_default_api_key_passes_startup_validation(tmp_path):
    """Regression: the installer's default SHIM_API_KEY was 7 characters and the service refused to start."""
    import re
    from pathlib import Path
    from dujiao_shim.config import from_env
    script = (Path(__file__).parent.parent / "deploy" / "install-shim.sh").read_text()
    key = re.search(r'echo "SHIM_API_KEY=([^"]+)"', script).group(1)
    catalog = Path(__file__).parent.parent / "deploy" / "shim-catalog.example.json"
    env = {"SHIM_API_KEY": key, "SHIM_API_SECRET": "s" * 64, "SHIM_ADMIN_TOKEN": "t" * 64,
           "SHIM_CATALOG": str(catalog), "SHIM_DB": str(tmp_path / "x.db")}
    assert from_env(env).api_key == key
    assert key in script.split("API Key：")[1].split("\n")[0]


def test_products_advertise_currency(tmp_path, up):
    _, c = make(tmp_path, up)
    item = call(c, "GET", "/api/v1/upstream/products").json()["items"][0]
    assert item["currency"] == "CNY"
