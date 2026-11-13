"""Starlette app: the Dujiao-Next upstream protocol (/api/v1/upstream/*) plus a small admin page for manual orders."""
import hmac
import json
import os
import time

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from . import auth
from .config import Config, Product, from_env
from .fulfil import Fulfiller, Notifier, dujiao_status, fulfillment
from .ops import ops_routes
from .public import build_public_app
from .redeem import Redeemer
from .store import OPEN_STATES, Store
from .suppliers import Client16688, Supplier16688

PREFIX = "/api/v1/upstream"
LANG = "zh-CN"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Core:
    """Everything the internal app (Dujiao upstream + /ops) and the public redeem app share."""

    def __init__(self, cfg: Config, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self.store = Store(cfg.db_path)
        self.sup = Supplier16688(Client16688(cfg.app_id, cfg.secret, cfg.base_url, transport), cfg.live,
                                 cfg.min_balance)
        self.notify = Notifier(cfg.feishu_webhook, cfg.notify_prefix, transport)
        self.ful = Fulfiller(cfg, self.store, self.sup, self.notify, transport)
        self.redeemer = Redeemer(cfg, self.store, self.ful)
        self.ful.recover()


def build_app(cfg: Config, transport: httpx.BaseTransport | None = None, start_worker: bool = True) -> Starlette:
    core = Core(cfg, transport)
    store, sup, notify, ful = core.store, core.sup, core.notify, core.ful

    def fail(status: int, code: str, message: str = "") -> JSONResponse:
        return JSONResponse({"ok": False, "error_code": code, "error_message": message or code}, status_code=status)

    def stock_of(p: Product) -> int:
        """Advertised stock: the first enabled pool member that can sell right now."""
        for s in p.sources:
            if not s.enabled:
                continue
            if s.type == "16688":
                info = sup.info(s.goods_no)
                n = int(info["available_quantity"]) if info else s.stock
            else:
                n = s.stock
            if n > 0:
                return n
        return 0

    def product_json(p: Product) -> dict:
        n = stock_of(p)
        status = "out_of_stock" if n <= 0 else "low_stock" if n <= 20 else "in_stock"
        price = f"{p.cost:.2f}"
        now = _iso(time.time())
        return {
            "id": p.id, "slug": p.slug, "title": {LANG: p.title}, "description": {LANG: p.description},
            "content": {}, "seo_meta": {}, "images": p.images, "tags": [], "price_amount": price, "currency": "CNY",
            "fulfillment_type": "auto", "manual_form_schema": None, "is_active": True,
            "category_id": p.category_id,
            "skus": [{"id": p.sku_id, "sku_code": "DEFAULT", "spec_values": {}, "price_amount": price,
                      "stock_status": status, "stock_quantity": n, "is_active": True}],
            "created_at": now, "updated_at": now,
        }

    async def guarded(request: Request, handler):
        body = await request.body()
        err = auth.verify(request.headers, request.method, request.url.path, body, cfg.api_key, cfg.api_secret)
        if err:
            return fail(401, err)
        return await handler(request, body)

    def route(fn):
        async def endpoint(request: Request):
            return await guarded(request, fn)
        return endpoint

    @route
    async def ping(request, body):
        return JSONResponse({"ok": True, "site_name": cfg.site_name, "protocol_version": "1.0", "user_id": 1,
                             "balance": "0.00", "currency": "CNY", "member_level": None})

    @route
    async def categories(request, body):
        return JSONResponse({"ok": True, "categories": [
            {"id": 1, "parent_id": 0, "slug": "digital", "name": {LANG: "数字商品"}, "icon": "", "sort_order": 0}]})

    @route
    async def products(request, body):
        page = max(int(request.query_params.get("page", 1)), 1)
        size = min(max(int(request.query_params.get("page_size", 20)), 1), 100)
        items = [product_json(p) for p in cfg.products][(page - 1) * size: page * size]
        return JSONResponse({"ok": True, "items": items, "total": len(cfg.products), "page": page, "page_size": size})

    @route
    async def product_detail(request, body):
        p = cfg.product_by_id(int(request.path_params["pid"]))
        if p is None:
            return fail(404, "product_not_found")
        return JSONResponse({"ok": True, "product": product_json(p)})

    def order_json(row) -> dict:
        # The documented create response is "paid"; the delivery itself always arrives later (callback / poll).
        status = "canceled" if dujiao_status(row) == "canceled" else "paid"
        return {"ok": True, "order_id": row["id"], "order_no": row["order_no"], "status": status,
                "amount": row["amount"], "currency": "CNY"}

    @route
    async def create_order(request, body):
        try:
            req = json.loads(body or b"{}")
            sku_id, qty = int(req["sku_id"]), int(req["quantity"])
        except (ValueError, KeyError, TypeError):
            return fail(400, "bad_request", "sku_id and quantity are required")
        p = cfg.product_by_sku(sku_id)
        if p is None or qty < 1:
            return fail(400, "sku_unavailable" if p is None else "bad_request")
        downstream = req.get("downstream_order_no") or None
        cb = req.get("callback_url") or ""
        row = store.get_by_downstream(downstream) if downstream else None
        if row is not None and row["status"] != "rejected":
            return JSONResponse(order_json(row))          # idempotent replay: never buy twice
        if row is not None:
            store.reopen(row["id"], cb)                    # refused earlier with no money moved: retry allowed
        else:
            row = store.create(sku_id, p.id, qty, downstream, cb)
        if p.mode == "redeem":
            # Sell now, buy later: hand Dujiao our own codes. No money moves until the buyer redeems.
            codes = core.redeemer.issue(p, qty, "dujiao", row["order_no"])
            store.finish(row["id"], "delivered", payload=core.redeemer.delivery_text(p, codes))
            return JSONResponse(order_json(store.get(row["id"])))
        code = ful.process(store.get(row["id"]), p)
        row = store.get(row["id"])
        if code:
            return JSONResponse({"ok": False, "order_id": row["id"], "order_no": row["order_no"],
                                 "status": "canceled", "error_code": code, "error_message": row["error"]})
        return JSONResponse(order_json(row))

    @route
    async def get_order(request, body):
        row = store.get(int(request.path_params["oid"]))
        if row is None:
            return fail(404, "order_not_found")
        out = {"ok": True, "order_id": row["id"], "order_no": row["order_no"], "status": dujiao_status(row),
               "amount": row["amount"], "currency": "CNY", "items": [{
                   "product_id": row["product_id"], "sku_id": row["sku_id"], "quantity": row["quantity"],
                   "fulfillment_type": "auto"}]}
        f = fulfillment(row)
        if f:
            out["fulfillment"] = f
        return JSONResponse(out)

    @route
    async def cancel_order(request, body):
        row = store.get(int(request.path_params["oid"]))
        if row is None:
            return fail(404, "order_not_found")
        if row["status"] in ("canceled", "rejected"):
            return JSONResponse({"ok": True, "status": "canceled"})
        if row["status"] != "awaiting_manual":
            return fail(409, "cancel_not_allowed", "already purchased or in progress at the supplier")
        store.update(row["id"], status="canceled", error="canceled by Dujiao")
        return JSONResponse({"ok": True, "status": "canceled"})

    # ---- admin: manual orders -------------------------------------------------------------------
    def admin_ok(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("x-admin-token", ""), cfg.admin_token)

    async def admin_page(request: Request):
        return HTMLResponse(ADMIN_HTML)

    async def admin_pending(request: Request):
        if not admin_ok(request):
            return fail(401, "unauthorized")
        rows = store.pending_manual()
        return JSONResponse({"orders": [{
            "order_no": r["order_no"], "status": r["status"], "quantity": r["quantity"],
            "product": (cfg.product_by_id(r["product_id"]) or Product(0, 0, "", "?", "", 0)).title,
            "source": r["supplier_ref"], "error": r["error"], "created_at": _iso(r["created_at"])} for r in rows]})

    async def admin_act(request: Request):
        if not admin_ok(request):
            return fail(401, "unauthorized")
        req = await request.json()
        row = store.get_by_no(str(req.get("order_no", "")))
        if row is None or row["status"] not in OPEN_STATES:
            return fail(404, "order_not_open")
        if request.path_params["action"] == "deliver":
            payload = str(req.get("payload", "")).strip()
            if not payload:
                return fail(400, "bad_request", "payload is empty")
            store.finish(row["id"], "delivered", payload=payload)
        else:
            store.finish(row["id"], "canceled", error=str(req.get("reason", "canceled by admin"))[:200])
        return JSONResponse({"ok": True})

    async def health(request: Request):
        return JSONResponse({"ok": True, "live": cfg.live, "supplier_configured": sup.client.configured})

    routes = [
        Route(f"{PREFIX}/ping", ping, methods=["POST"]),
        Route(f"{PREFIX}/categories", categories, methods=["GET"]),
        Route(f"{PREFIX}/products", products, methods=["GET"]),
        Route(f"{PREFIX}/products/{{pid:int}}", product_detail, methods=["GET"]),
        Route(f"{PREFIX}/orders", create_order, methods=["POST"]),
        Route(f"{PREFIX}/orders/{{oid:int}}", get_order, methods=["GET"]),
        Route(f"{PREFIX}/orders/{{oid:int}}/cancel", cancel_order, methods=["POST"]),
        Route("/admin", admin_page, methods=["GET"]),
        Route("/admin/api/pending", admin_pending, methods=["GET"]),
        Route("/admin/api/{action:str}", admin_act, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
    ] + ops_routes(core)
    app = Starlette(routes=routes)
    app.state.core = core
    app.state.public = build_public_app(core)
    app.state.fulfiller = ful
    app.state.store = store
    app.state.notifier = notify
    if start_worker:
        ful.start()
    return app


ADMIN_HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>人工采购队列</title><style>body{font:15px system-ui;max-width:720px;margin:16px auto;padding:0 16px}
.o{border:1px solid #ccc;border-radius:8px;padding:12px;margin:12px 0}textarea{width:100%;height:80px}
button{margin-right:8px;padding:6px 12px}</style>
<h2>人工采购队列</h2><input id=t type=password placeholder="管理令牌" size=40> <button onclick=load()>刷新</button>
<div id=l></div><script>
const q=s=>document.querySelector(s);q('#t').value=sessionStorage.getItem('t')||'';
async function api(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:{'x-admin-token':q('#t').value,'content-type':'application/json'},body:b?JSON.stringify(b):undefined});return r.json()}
async function load(){sessionStorage.setItem('t',q('#t').value);const d=await api('/admin/api/pending');
q('#l').innerHTML=d.orders?d.orders.map(o=>`<div class=o><b>${o.order_no}</b> ${o.product} ×${o.quantity}<br>来源：${o.source||'-'} ｜ 状态：${o.status} ｜ ${o.created_at}
<br>${o.error||''}<textarea id="p-${o.order_no}" placeholder="粘贴交付内容（卡密等）"></textarea>
<button onclick="act('deliver','${o.order_no}')">交付</button><button onclick="act('cancel','${o.order_no}')">取消</button></div>`).join('')||'没有待处理订单':'令牌错误'}
async function act(a,n){const p=q('#p-'+n).value;if(a=='cancel'&&!confirm('取消订单？'))return;
const r=await api('/admin/api/'+a,{order_no:n,payload:p});alert(r.ok?'完成':(r.error_message||'失败'));load()}
</script>"""


def build_from_env():
    return build_app(from_env(dict(os.environ)))


def main() -> None:
    import asyncio

    import uvicorn

    app = build_from_env()
    app.state.core.ful.sweep_gap = 1.0
    host = os.environ.get("HOST", "0.0.0.0")
    servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=int(os.environ.get("PORT", "3000")),
                                             log_level="info")),
               uvicorn.Server(uvicorn.Config(app.state.public, host=host,
                                             port=int(os.environ.get("PUBLIC_PORT", "3001")), log_level="info",
                                             proxy_headers=False))]

    async def run():
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(run())
