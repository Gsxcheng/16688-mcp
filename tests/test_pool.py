"""Source pools, own redeem codes, the public redeem app and the /ops console."""
import json
import time

import httpx
import pytest
from starlette.testclient import TestClient

from dujiao_shim import auth
from dujiao_shim.app import build_app
from dujiao_shim.config import Config, Product, Source, load_products

KEY, SECRET, ADMIN = "shimkey123", "s" * 40, "a" * 40
H = {"x-admin-token": ADMIN}


class Pool16688:
    """Fake 16688 with per-goods price and stock."""

    def __init__(self):
        self.goods = {"A": [10.0, 5], "B": [8.0, 5], "C": [9.0, 5]}   # goods_no -> [total, stock]
        self.calls: list[tuple[str, dict]] = []
        self.sent: list[str] = []
        self.create_exc: Exception | None = None
        self.refuse_create: set[str] = set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if "hook.example" in str(req.url):
            self.sent.append(req.content.decode())
            return httpx.Response(200, json={})
        path, body = req.url.path, json.loads(req.content or b"{}")
        self.calls.append((path, body))
        g = self.goods.get(body.get("goods_no"))
        if path.endswith("goods/detail") or path.endswith("quotePrice"):
            if g is None:
                return httpx.Response(200, json={"code": 0, "msg": "商品不存在或已下架"})
            return httpx.Response(200, json={"code": 1, "data": {
                "goods_no": body["goods_no"], "unit_price": g[0], "total_amount": g[0], "available_quantity": g[1],
                "available_money": 500.0, "balance_enough": True}})
        if path.endswith("purchase/create"):
            if self.create_exc:
                raise self.create_exc
            if body["goods_no"] in self.refuse_create:
                return httpx.Response(200, json={"code": 0, "msg": "库存不足"})
            return httpx.Response(200, json={"code": 1, "data": {
                "trade_no": "T-" + body["goods_no"], "status": 4, "total_amount": g[0],
                "cards": [{"code": "CARD-" + body["goods_no"]}], "instruction": "上游说明"}})
        if path.endswith("merchant/balance"):
            return httpx.Response(200, json={"code": 1, "data": {"operate_money": 100.0}})
        if path.endswith("goods/list"):
            items = [{"goods_no": k, "name": k, "unit_price": v[0], "available_quantity": v[1], "sales_count": 1,
                      "delivery_method": 1, "detail": "x" * 50} for k, v in self.goods.items()]
            return httpx.Response(200, json={"code": 1, "data": {"list": items if body["page_no"] == 1 else []}})
        return httpx.Response(200, json={"code": 1, "data": {}})

    def bought(self) -> list[str]:
        return [b["goods_no"] for p, b in self.calls if p.endswith("purchase/create")]


def make(tmp_path, up, mode="redeem", sources=None, live=True, **over):
    prod = Product(id=1, sku_id=101, slug="p1", title="GPT Plus", description="", cost=8.0, mode=mode,
                   max_unit_cost=12.0, instruction="自家说明",
                   sources=sources if sources is not None else [Source("16688", goods_no=g) for g in "ABC"])
    kw = {"feishu_webhook": "https://hook.example/x", **over}
    cfg = Config(api_key=KEY, api_secret=SECRET, admin_token=ADMIN, db_path=str(tmp_path / "s.db"),
                 products=[prod], app_id="app", secret="sec", live=live, **kw)
    app = build_app(cfg, transport=httpx.MockTransport(up), start_worker=False)
    return app, TestClient(app), TestClient(app.state.public)


def dujiao_order(client, down="D1", qty=1):
    body = json.dumps({"sku_id": 101, "quantity": qty, "downstream_order_no": down}, separators=(",", ":")).encode()
    path = "/api/v1/upstream/orders"
    return client.post(path, content=body, headers=auth.signed_headers(KEY, SECRET, "POST", path, body)).json()


def one_code(app) -> str:
    core = app.state.core
    return core.redeemer.issue(core.cfg.products[0], 1, "batch", "t")[0]


@pytest.fixture()
def up():
    return Pool16688()


# ---- pool selection -------------------------------------------------------------------------------
def test_direct_order_buys_cheapest_and_records_comparison(tmp_path, up):
    app, c, _ = make(tmp_path, up, mode="direct")
    r = dujiao_order(c)
    assert r["ok"] and up.bought() == ["B"]
    trace = json.loads(app.state.store.recent(1)[0]["trace"])
    assert {t["src"]: t["verdict"] for t in trace} == {"A": "candidate", "B": "chosen", "C": "candidate"}


def test_rule_order_keeps_list_order(tmp_path, up):
    app, c, _ = make(tmp_path, up, mode="direct")
    app.state.core.cfg.products[0].rule = "order"
    dujiao_order(c)
    assert up.bought() == ["A"]


def test_skips_out_of_stock_over_cap_disabled_and_delisted(tmp_path, up):
    up.goods = {"A": [7.0, 0], "B": [13.0, 5], "C": [9.0, 5], "D": [1.0, 5]}
    srcs = [Source("16688", goods_no="A"), Source("16688", goods_no="B"), Source("16688", goods_no="GONE"),
            Source("16688", goods_no="D", enabled=False), Source("16688", goods_no="C")]
    app, c, _ = make(tmp_path, up, mode="direct", sources=srcs)
    assert dujiao_order(c)["ok"] and up.bought() == ["C"]
    verdicts = {t["src"]: t["verdict"] for t in json.loads(app.state.store.recent(1)[0]["trace"])}
    assert verdicts == {"A": "insufficient_stock", "B": "price_exceeded", "GONE": "sku_unavailable", "C": "chosen"}


def test_refused_purchase_falls_through_to_next_cheapest(tmp_path, up):
    up.refuse_create = {"B"}
    _, c, _ = make(tmp_path, up, mode="direct")
    assert dujiao_order(c)["ok"] and up.bought() == ["B", "C"]


def test_ambiguous_purchase_stops_and_is_not_retried(tmp_path, up):
    up.create_exc = httpx.ReadTimeout("boom")
    app, _, pub = make(tmp_path, up)
    code = one_code(app)
    assert pub.post("/api/redeem", json={"code": code}).json()["state"] == "processing"
    row = app.state.store.recent(1)[0]
    assert row["status"] == "needs_review" and up.bought() == ["B"]
    up.create_exc = None
    app.state.core.ful.tick(time.time() + 10_000)
    assert up.bought() == ["B"] and app.state.store.recent(1)[0]["status"] == "needs_review"


# ---- own codes ------------------------------------------------------------------------------------
def test_redeem_mode_issues_codes_without_buying_and_is_idempotent(tmp_path, up):
    app, c, _ = make(tmp_path, up)
    r1, r2 = dujiao_order(c, qty=2), dujiao_order(c, qty=2)
    assert r1["ok"] and r1 == r2 and up.bought() == []
    rows = app.state.store.list_codes(10)
    assert len(rows) == 2 and all(x["status"] == "issued" for x in rows)
    payload = app.state.store.recent(1)[0]["payload"]
    assert all(x["code"] in payload for x in rows) and "/redeem/" in payload and "自家说明" in payload


def test_redeem_buys_once_and_same_code_shows_same_result(tmp_path, up):
    app, _, pub = make(tmp_path, up)
    code = one_code(app)
    assert pub.get(f"/api/redeem/{code}").json()["state"] == "ready"
    r = pub.post("/redeem/api/redeem", json={"code": code.lower()}).json()
    assert r["state"] == "delivered" and "CARD-B" in r["payload"]
    again = pub.post("/api/redeem", json={"code": code}).json()
    assert again["payload"] == r["payload"] and up.bought() == ["B"]
    assert pub.get(f"/api/redeem/{code}").json()["state"] == "delivered"
    assert set(again) == {"code", "product", "state", "payload", "instruction", "redeemed_at"}


def test_claim_is_atomic(tmp_path, up):
    app, _, _ = make(tmp_path, up)
    code = one_code(app)
    assert app.state.store.claim_code(code) is True
    assert app.state.store.claim_code(code) is False


def test_orphan_claim_is_released_on_restart(tmp_path, up):
    app, _, _ = make(tmp_path, up)
    code = one_code(app)
    app.state.store.claim_code(code)
    app2, _, pub2 = make(tmp_path, up)
    assert pub2.get(f"/api/redeem/{code}").json()["state"] == "ready"


def test_waiting_stock_then_auto_delivery(tmp_path, up):
    up.goods = {"A": [10.0, 0], "B": [8.0, 0], "C": [9.0, 0]}
    app, _, pub = make(tmp_path, up)
    code = one_code(app)
    r = pub.post("/api/redeem", json={"code": code}).json()
    assert r["state"] == "processing" and up.bought() == []
    assert app.state.store.recent(1)[0]["status"] == "waiting_stock"
    assert any("等待" in s for s in up.sent)
    n = len(up.sent)
    app.state.core.ful.tick(time.time() + 10_000)          # still nothing: no second alert
    assert len([s for s in up.sent[n:] if "等待" in s]) == 0
    up.goods["C"][1] = 3
    app.state.core.ful.tick(time.time() + 20_000)
    assert up.bought() == ["C"]
    assert pub.get(f"/api/redeem/{code}").json()["state"] == "delivered"


def test_expired_voided_unknown_and_confirm(tmp_path, up):
    app, c, pub = make(tmp_path, up)
    store = app.state.store
    old = one_code(app)
    store.update_code(old, expires_at=time.time() - 10)
    assert pub.post("/api/redeem", json={"code": old}).json()["state"] == "expired"
    dead = one_code(app)
    assert c.post("/ops/api/codes/void", json={"code": dead}, headers=H).json()["ok"]
    assert pub.post("/api/redeem", json={"code": dead}).json()["state"] == "voided"
    assert pub.post("/api/redeem", json={"code": "GG-00000-00000-00000-00000"}).json()["state"] == "not_found"
    assert pub.post("/api/redeem", json={"code": "nonsense"}).json()["state"] == "not_found"
    app.state.core.cfg.products[0].warn_minutes = 5
    warm = one_code(app)
    assert pub.post("/api/redeem", json={"code": warm}).json()["state"] == "confirm"
    assert up.bought() == []
    assert pub.post("/api/redeem", json={"code": warm, "confirm": True}).json()["state"] == "delivered"
    assert c.post("/ops/api/codes/void", json={"code": warm}, headers=H).status_code == 409


def test_hide_upstream_note(tmp_path, up):
    app, _, pub = make(tmp_path, up)
    app.state.core.cfg.products[0].hide_upstream_note = True
    r = pub.post("/api/redeem", json={"code": one_code(app)}).json()
    assert "上游说明" not in r["payload"] and r["instruction"] == "自家说明"


def test_dry_run_never_calls_purchase(tmp_path, up):
    app, _, pub = make(tmp_path, up, live=False)
    r = pub.post("/api/redeem", json={"code": one_code(app)}).json()
    assert r["state"] == "delivered" and "DRY-RUN" in r["payload"] and up.bought() == []


# ---- public app -----------------------------------------------------------------------------------
def test_public_app_exposes_only_redemption(tmp_path, up):
    _, _, pub = make(tmp_path, up)
    assert pub.get("/").status_code == 200 and pub.get("/redeem/").status_code == 200
    assert pub.get("/redeem", follow_redirects=False).status_code == 308
    for path in ("/ops", "/ops/api/pools", "/admin", "/admin/api/pending", "/api/v1/upstream/products"):
        assert pub.get(path, headers=H).status_code == 404


def test_public_rate_limit(tmp_path, up):
    _, _, pub = make(tmp_path, up, redeem_per_minute=3)
    codes = [pub.post("/api/redeem", json={"code": "x"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


# ---- sweep ----------------------------------------------------------------------------------------
def test_sweep_notifies_once_per_change(tmp_path, up):
    app, c, _ = make(tmp_path, up, mode="direct")
    ful = app.state.core.ful
    ful.sweep_gap = 0
    ful.sweep()
    assert ful.health[101] == "ok" and up.sent == []
    for g in up.goods.values():
        g[1] = 0
    ful.sweep()
    ful.sweep()
    assert ful.health[101] == "down" and len(up.sent) == 1 and "没有可买的货源" in up.sent[0]
    up.goods["A"] = [11.5, 4]                                   # 96% of the 12.0 ceiling
    ful.sweep()
    assert ful.health[101] == "near_cap" and len(up.sent) == 2
    up.goods["A"] = [6.0, 4]
    ful.sweep()
    assert ful.health[101] == "ok" and len(up.sent) == 2


# ---- /ops -----------------------------------------------------------------------------------------
def test_ops_needs_token(tmp_path, up):
    _, c, _ = make(tmp_path, up)
    assert c.get("/ops").status_code == 200 and ADMIN not in c.get("/ops").text
    for path in ("overview", "pools", "channel/16688", "exceptions", "log", "codes", "proposals"):
        assert c.get(f"/ops/api/{path}").status_code == 401
        assert c.get(f"/ops/api/{path}", headers={"x-admin-token": "nope"}).status_code == 401
        assert c.get(f"/ops/api/{path}", headers=H).json()["ok"]


def test_ops_pool_save_persists_to_catalog(tmp_path, up):
    cat = tmp_path / "catalog.json"
    app, c, _ = make(tmp_path, up, catalog_path=str(cat))
    body = {"sku_id": 101, "max_unit_cost": 15, "rule": "order", "valid_days": 3,
            "sources": [{"type": "16688", "goods_no": "C"}, {"type": "manual", "label": "TG", "stock": 2}]}
    r = c.post("/ops/api/pools/save", json=body, headers=H).json()
    assert r["ok"] and [s["goods_no"] for s in r["product"]["sources"]] == ["C", ""]
    saved = load_products(str(cat))[0]
    assert saved.rule == "order" and saved.max_unit_cost == 15 and saved.sources[1].label == "TG"
    assert c.post("/ops/api/pools/save", json={"sku_id": 101, "mode": "weird"}, headers=H).status_code == 400
    new = c.post("/ops/api/pools/save", json={"title": "Notion", "mode": "redeem"}, headers=H).json()["product"]
    assert new["sku_id"] == 102 and len(load_products(str(cat))) == 2


def test_ops_overview_channel_and_codes(tmp_path, up):
    app, c, pub = make(tmp_path, up)
    made = c.post("/ops/api/codes/generate", json={"sku_id": 101, "n": 3}, headers=H).json()["codes"]
    assert len(made) == 3
    pub.post("/api/redeem", json={"code": made[0]})
    o = c.get("/ops/api/overview", headers=H).json()
    assert o["wallet"] == 100.0 and o["unredeemed"] == 2 and o["orders_today"] == 1
    ch = c.get("/ops/api/channel/16688", headers=H).json()
    assert {g["goods_no"]: g["in_pool"] for g in ch["goods"]} == {"A": "GPT Plus", "B": "GPT Plus", "C": "GPT Plus"}
    assert "detail" not in ch["goods"][0]
    log = c.get("/ops/api/log", headers=H).json()["orders"][0]
    assert log["from"] == "兑换" and log["code"] == made[0] and log["trace"]
    states = [x["state"] for x in c.get("/ops/api/codes", headers=H).json()["codes"]]
    assert sorted(states) == ["delivered", "ready", "ready"]


def test_ops_retry_and_manual_delivery_of_exceptions(tmp_path, up):
    up.create_exc = httpx.ReadTimeout("boom")
    app, c, pub = make(tmp_path, up)
    code = one_code(app)
    pub.post("/api/redeem", json={"code": code})
    no = c.get("/ops/api/exceptions", headers=H).json()["orders"][0]["order_no"]
    up.create_exc = None
    r = c.post("/ops/api/orders/retry", json={"order_no": no}, headers=H).json()
    assert r["order"]["status"] == "delivered"
    assert pub.get(f"/api/redeem/{code}").json()["state"] == "delivered"
    assert c.post("/ops/api/orders/deliver", json={"order_no": no, "payload": "x"}, headers=H).status_code == 404


def test_proposals_approve_and_reject(tmp_path, up):
    cat = tmp_path / "catalog.json"
    app, c, _ = make(tmp_path, up, catalog_path=str(cat))
    assert c.post("/ops/api/proposals", json={"type": "nope", "title": "x"}, headers=H).status_code == 400
    p1 = c.post("/ops/api/proposals", headers=H, json={
        "type": "new_product", "title": "Notion 3M", "mode": "redeem", "max_unit_cost": 8, "price": 12.8,
        "sources": [{"type": "16688", "goods_no": "C"}], "reason": "销量高"}).json()["id"]
    p2 = c.post("/ops/api/proposals", headers=H, json={
        "type": "remove_source", "title": "移除已下架货源", "goods_nos": ["A"]}).json()["id"]
    p3 = c.post("/ops/api/proposals", headers=H, json={"type": "new_product", "title": "不要的"}).json()["id"]
    assert c.get("/ops/api/overview", headers=H).json()["open_proposals"] == 3
    assert c.post(f"/ops/api/proposals/{p1}/approve", headers=H).json()["ok"]
    assert c.post(f"/ops/api/proposals/{p2}/approve", headers=H).json()["ok"]
    assert c.post(f"/ops/api/proposals/{p3}/reject", headers=H).json()["ok"]
    assert c.post(f"/ops/api/proposals/{p1}/approve", headers=H).status_code == 409
    prods = load_products(str(cat))
    assert [p.title for p in prods] == ["GPT Plus", "Notion 3M"]
    assert [s.goods_no for s in prods[0].sources] == ["B", "C"] and prods[1].mode == "redeem"


def test_open_proposal_shows_live_price(tmp_path, up):
    _, c, _ = make(tmp_path, up)
    c.post("/ops/api/proposals", headers=H, json={"type": "new_product", "title": "N", "price": 12.0, "sources": [
        {"type": "16688", "goods_no": "B"}, {"type": "16688", "goods_no": "GONE"}, {"type": "manual", "label": "TG"}]})
    p = c.get("/ops/api/proposals", headers=H).json()["proposals"][0]
    assert p["cost"] == 8.0 and [s.get("total") for s in p["sources"]] == [8.0, None, None]
    assert p["sources"][1]["note"] == "已下架或查不到"
