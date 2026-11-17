"""Persistent wallet and old rows, matching the test machine after one mock purchase."""
import json
import time

import httpx
import pytest
from starlette.testclient import TestClient

from dujiao_shim import auth
from dujiao_shim.app import build_app
from dujiao_shim.config import Config, Product, Source

KEY, SECRET, ADMIN = "shimkey123", "s" * 40, "a" * 40
H = {"x-admin-token": ADMIN}


class Wallet:
    """Fake 16688 whose wallet is remembered across calls, like the long-lived mock."""

    def __init__(self, money):
        self.money = money
        self.goods = {"G24324524": [110.51, 27], "G74884541": [110.51, 0], "G61972301": [123.36, 20],
                      "G75807103": [123.36, 7], "G71485656": [6.34, 19]}
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        path = req.url.path
        self.calls.append((path, body.get("goods_no")))
        if path.endswith("merchant/balance"):
            return httpx.Response(200, json={"code": 1, "data": {"operate_money": self.money}})
        g = self.goods.get(body.get("goods_no"))
        if g is None:
            return httpx.Response(200, json={"code": 0, "msg": "商品不存在或已下架"})
        total, stock = g
        if path.endswith("purchase/create"):
            self.money = round(self.money - total, 2)
            return httpx.Response(200, json={"code": 1, "data": {
                "trade_no": "T-" + body["goods_no"], "status": 4, "total_amount": total,
                "cards": [{"code": "MOCK-NOT-A-REAL-CARD"}]}})
        return httpx.Response(200, json={"code": 1, "data": {
            "goods_no": body.get("goods_no"), "unit_price": total, "total_amount": total,
            "available_quantity": stock, "available_money": self.money, "balance_enough": self.money >= total}})

    def creates(self):
        return [g for p, g in self.calls if p.endswith("purchase/create")]


def catalog():
    gpt = Product(id=1, sku_id=101, slug="gpt", title="GPT Plus 月卡 直充", description="", cost=110.51,
                  mode="redeem", max_unit_cost=125.0,
                  sources=[Source("16688", goods_no="G24324524"), Source("16688", goods_no="G74884541"),
                           Source("16688", goods_no="G61972301"), Source("16688", goods_no="G75807103"),
                           Source("manual", label="TG 人工采购", stock=0)])
    notion = Product(id=2, sku_id=102, slug="notion", title="Notion Business 3个月", description="", cost=6.34,
                     mode="direct", max_unit_cost=6.8, sources=[Source("16688", goods_no="G71485656")])
    empty = Product(id=3, sku_id=103, slug="gemini", title="Gemini Pro 6个月 激活卡密", description="", cost=8.22,
                    mode="redeem", max_unit_cost=9.0, sources=[])
    return [gpt, notion, empty]


def make(tmp_path, up, money=162.13):
    up.money = money
    cfg = Config(api_key=KEY, api_secret=SECRET, admin_token=ADMIN, db_path=str(tmp_path / "s.db"),
                 products=catalog(), app_id="app", secret="sec", base_url="http://mock16688:9000", live=True,
                 max_orders_per_day=20)
    app = build_app(cfg, transport=httpx.MockTransport(up), start_worker=False)
    return app, TestClient(app), TestClient(app.state.public)


def approve_order(c, order_no):
    props = c.get("/ops/api/proposals", headers=H).json()["proposals"]
    match = [p for p in props if p["status"] == "open" and p["type"] == "purchase" and p.get("order_no") == order_no]
    assert len(match) == 1, [p.get("order_no") for p in props if p["status"] == "open"]
    r = c.post(f"/ops/api/proposals/{match[0]['id']}/approve", headers=H)
    assert r.status_code == 200, r.text
    return match[0]


def order(c, sku, down):
    body = json.dumps({"sku_id": sku, "quantity": 1, "downstream_order_no": down}, separators=(",", ":")).encode()
    path = "/api/v1/upstream/orders"
    return c.post(path, content=body, headers=auth.signed_headers(KEY, SECRET, "POST", path, body)).json()


@pytest.fixture()
def up():
    return Wallet(162.13)


def test_second_gpt_redeem_does_not_propose_when_wallet_cannot_cover(tmp_path, up):
    app, c, pub = make(tmp_path, up)
    # Old rows from earlier days must not be bought again, and must not hide today's cap.
    old = app.state.store.create(101, 1, 1, "OLD", "")
    app.state.store.update(old["id"], status="delivered", supplier="16688", supplier_ref="T-old",
                           created_at=time.time() - 3 * 86400)
    code = app.state.core.redeemer.issue(app.state.core.cfg.products[0], 1, "batch", "t1")[0]
    assert pub.post("/api/redeem", json={"code": code}).json()["state"] == "processing"
    opened = [p for p in c.get("/ops/api/proposals", headers=H).json()["proposals"]
              if p["status"] == "open" and p["type"] == "purchase"]
    assert len(opened) == 1 and opened[0]["recommended"]["goods_no"] == "G24324524"
    assert opened[0]["recommended"]["total"] == 110.51
    approve_order(c, opened[0]["order_no"])
    assert up.creates() == ["G24324524"] and round(up.money, 2) == 51.62

    again = app.state.core.redeemer.issue(app.state.core.cfg.products[0], 1, "batch", "t2")[0]
    second = pub.post("/api/redeem", json={"code": again}).json()
    assert second["state"] == "processing" and "payload" not in second
    assert up.creates() == ["G24324524"]
    still = [p for p in c.get("/ops/api/proposals", headers=H).json()["proposals"]
             if p["status"] == "open" and p["type"] == "purchase"]
    assert still == []
    row = app.state.store.recent(1)[0]
    assert row["status"] == "waiting_stock" and row["supplier"] == ""
    assert "110.51" in row["error"] and "123.36" not in row["error"]
    trace = {t["src"]: t["verdict"] for t in json.loads(row["trace"])}
    assert trace["G24324524"] == "insufficient_balance" and trace["G74884541"] == "insufficient_stock"
    pub.post("/api/redeem", json={"code": again})
    assert up.creates() == ["G24324524"]


def test_cheaper_sku_can_still_be_rejected_then_approved_once(tmp_path, up):
    app, c, pub = make(tmp_path, up, money=51.62)
    app.state.store.add_proposal(json.dumps({"type": "new_product", "title": "旧提案"}))
    blocked = app.state.core.redeemer.issue(catalog()[0], 1, "batch", "x")[0]
    assert pub.post("/api/redeem", json={"code": blocked}).json()["state"] == "processing"
    assert up.creates() == []
    empty = app.state.core.redeemer.issue(catalog()[2], 1, "batch", "g")[0]
    assert pub.post("/api/redeem", json={"code": empty}).json()["state"] == "processing"
    assert app.state.store.recent(1)[0]["status"] == "waiting_stock"
    assert up.creates() == []

    first = order(c, 102, "N-reject")
    assert first["ok"] is True and up.creates() == []
    props = [p for p in c.get("/ops/api/proposals", headers=H).json()["proposals"]
             if p["status"] == "open" and p["type"] == "purchase" and p.get("order_no") == first["order_no"]]
    assert len(props) == 1 and props[0]["recommended"]["total"] == 6.34
    assert c.post(f"/ops/api/proposals/{props[0]['id']}/reject", headers=H).status_code == 200
    assert c.post(f"/ops/api/proposals/{props[0]['id']}/reject", headers=H).status_code == 409
    assert up.creates() == []
    path = f"/api/v1/upstream/orders/{first['order_id']}"
    assert c.get(path, headers=auth.signed_headers(KEY, SECRET, "GET", path, b"")).json()["status"] == "canceled"

    second = order(c, 102, "N-approve")
    bought = [p for p in c.get("/ops/api/proposals", headers=H).json()["proposals"]
              if p.get("order_no") == second["order_no"] and p["status"] == "open"]
    assert c.post(f"/ops/api/proposals/{bought[0]['id']}/approve", headers=H).status_code == 200
    assert c.post(f"/ops/api/proposals/{bought[0]['id']}/approve", headers=H).status_code == 409
    assert up.creates() == ["G71485656"]
    stale = [p for p in c.get("/ops/api/proposals", headers=H).json()["proposals"] if p["type"] == "new_product"]
    assert stale and stale[0]["status"] == "open"


def test_todays_old_16688_rows_count_toward_the_cap(tmp_path, up):
    app, c, _ = make(tmp_path, up, money=500)
    now = time.time()
    day = now - now % 86400
    for i in range(19):
        row = app.state.store.create(102, 2, 1, f"OLD{i}", "")
        app.state.store.update(row["id"], status="delivered", supplier="16688", supplier_ref=f"T{i}",
                               created_at=day + 10)
    yesterday = app.state.store.create(102, 2, 1, "YDAY", "")
    app.state.store.update(yesterday["id"], status="delivered", supplier="16688", supplier_ref="Ty",
                           created_at=day - 10)
    assert order(c, 102, "CAP-OK")["ok"] is True
    refused = order(c, 102, "CAP-NO")
    assert refused["ok"] is False and refused["error_code"] == "rate_limited"
    assert up.creates() == []
