"""/ops: the owner's decision console (proposals, pools, channels, exceptions, redemption log).

The console HTML is served only after a session cookie. /ops/api accepts that cookie or the admin token
or a Dujiao admin bearer. Nothing here calls purchase/create; approving a purchase proposal does."""
import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .config import Product, Source, save_products
from .store import OPEN_STATES

FEE_HINT = "总价含 16688 手续费。同意采购提案后才会执行；拒绝或未拍板都不会付款。"
COOKIE = "ops_session"
_SESSION_TAG = b"dujiao-shim-ops-v1"


def ops_session(admin_token: str) -> str:
    return hmac.new(admin_token.encode(), _SESSION_TAG, hashlib.sha256).hexdigest()


def has_ops_session(request: Request, admin_token: str) -> bool:
    got = request.cookies.get(COOKIE, "")
    expect = ops_session(admin_token)
    return len(got) == len(expect) and hmac.compare_digest(got, expect)


def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>采购决策登录</title><style>
:root{--bg:#f3f5f4;--surface:#fff;--line:#d5dbd8;--fg:#17231f;--muted:#5b6964;--accent:#0c6f5e;--on:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#111816;--surface:#1a2320;--line:#34413c;--fg:#e6edea;--muted:#9aa9a3;
--accent:#4fc3a9;--on:#06211b;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 "PingFang SC","Microsoft YaHei",system-ui,sans-serif}
main{max-width:420px;margin:64px auto;padding:0 16px}form{background:var(--surface);border:1px solid var(--line);
border-radius:12px;padding:20px;display:flex;flex-direction:column;gap:12px}
h1{font-size:18px;margin:0}p{margin:0;color:var(--muted);font-size:13px}
input{font:15px inherit;padding:10px 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button{font:inherit;font-weight:600;cursor:pointer;border:0;border-radius:8px;background:var(--accent);color:var(--on);padding:10px}
.err{color:#b3261e;min-height:1.2em}
</style></head><body><main><form id="f"><h1>采购决策</h1>
<p>管理入口。请输入本机已配置的管理令牌。令牌不会写进页面，登录后只留下一个 HttpOnly 会话。</p>
<label for="tok">管理令牌</label><input id="tok" type="password" autocomplete="current-password" required>
<button type="submit">进入</button><p class="err" id="err"></p></form></main><script>
document.getElementById('f').onsubmit=async(e)=>{e.preventDefault();const err=document.getElementById('err');err.textContent='';
 const r=await fetch('/ops/login',{method:'POST',headers:{'content-type':'application/json'},
  body:JSON.stringify({token:document.getElementById('tok').value})});
 if(r.ok){location.reload();return}err.textContent=r.status===429?'尝试太频繁，请稍后再来。':'令牌不对。'};
</script></body></html>"""


def _ts(t: float | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(t)) if t else ""


def ops_routes(core) -> list[Route]:
    cfg, store, sup, ful = core.cfg, core.store, core.sup, core.ful
    cache: dict[str, tuple[float, object]] = {}

    def fail(status: int, code: str, message: str = "") -> JSONResponse:
        return JSONResponse({"ok": False, "error_code": code, "error_message": message or code}, status_code=status)

    admins: dict[str, float] = {}         # sha256(Authorization) -> time Dujiao last accepted it
    dj = httpx.Client(transport=core.transport, timeout=5) if cfg.dujiao_url else None

    def dujiao_admin(auth: str) -> bool:
        """A token the shop admin already holds opens the console: Dujiao itself says whether it is valid and
        allowed to manage integrations. Accepted tokens are remembered for a minute."""
        if dj is None or not auth.startswith("Bearer ") or len(auth) > 4096:
            return False
        key = hashlib.sha256(auth.encode()).hexdigest()
        if time.time() - admins.get(key, 0) < 60:
            return True
        try:
            r = dj.get(cfg.dujiao_url.rstrip("/") + "/api/v1/admin/site-connections",
                       params={"page": 1, "page_size": 1}, headers={"Authorization": auth})
            ok = r.status_code == 200 and r.json().get("status_code") == 0
        except (httpx.HTTPError, ValueError):
            ok = False
        if ok:
            if len(admins) > 1000:
                admins.clear()
            admins[key] = time.time()
        else:
            admins.pop(key, None)
        return ok

    login_fails: dict[str, list[float]] = {}

    def token_ok(request: Request) -> bool:
        got = request.headers.get("x-admin-token", "")
        return len(got) == len(cfg.admin_token) and hmac.compare_digest(got, cfg.admin_token)

    def guard(fn):
        async def endpoint(request: Request):
            if not (token_ok(request) or has_ops_session(request, cfg.admin_token)
                    or dujiao_admin(request.headers.get("authorization", ""))):
                return fail(401, "unauthorized")
            return await fn(request)
        return endpoint

    def persist() -> None:
        if cfg.catalog_path:
            save_products(cfg.catalog_path, cfg.products)

    def product_json(p: Product) -> dict:
        srcs = []
        for s in p.sources:
            q = sup.last_quotes.get(s.goods_no) if s.type == "16688" else None
            srcs.append({"type": s.type, "goods_no": s.goods_no, "label": s.label, "stock": s.stock, "note": s.note,
                         "enabled": s.enabled, "max_unit_cost": s.max_unit_cost,
                         "quote": None if q is None else {"ok": q["ok"], "total": q["total"], "stock": q["stock"],
                                                          "message": q["message"], "at": _ts(q["ts"])}})
        return {"id": p.id, "sku_id": p.sku_id, "title": p.title, "description": p.description, "cost": p.cost,
                "mode": p.mode, "rule": p.rule, "max_unit_cost": p.max_unit_cost, "valid_days": p.valid_days,
                "warn_minutes": p.warn_minutes, "instruction": p.instruction,
                "hide_upstream_note": p.hide_upstream_note, "health": ful.health.get(p.sku_id, "unknown"),
                "sources": srcs}

    def order_json(r) -> dict:
        p = cfg.product_by_id(r["product_id"])
        code = store.code_by_order(r["id"])
        return {"order_no": r["order_no"], "status": r["status"], "product": p.title if p else "?",
                "quantity": r["quantity"], "supplier": r["supplier"], "supplier_ref": r["supplier_ref"],
                "amount": r["amount"], "error": r["error"], "created_at": _ts(r["created_at"]),
                "from": "兑换" if code else "卡网", "code": code["code"] if code else "",
                "trace": json.loads(r["trace"]) if r["trace"] else []}

    async def page(request: Request):
        if not has_ops_session(request, cfg.admin_token):
            return login_page()
        return HTMLResponse(OPS_HTML, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    async def login(request: Request):
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
        now = time.time()
        recent = [t for t in login_fails.get(ip, []) if now - t < 60]
        if len(recent) >= 8:
            return fail(429, "rate_limited", "尝试太频繁，请稍后再来")
        try:
            token = str((await request.json()).get("token", ""))
        except (ValueError, AttributeError):
            token = ""
        if len(token) != len(cfg.admin_token) or not hmac.compare_digest(token, cfg.admin_token):
            recent.append(now)
            login_fails[ip] = recent
            if len(login_fails) > 5000:
                login_fails.clear()
            return fail(401, "unauthorized")
        login_fails.pop(ip, None)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, ops_session(cfg.admin_token), httponly=True, samesite="strict", path="/",
                        max_age=12 * 3600)
        return resp

    @guard
    async def overview(request: Request):
        hit = cache.get("balance")
        if not hit or time.time() - hit[0] > 60:
            hit = (time.time(), sup.balance() if sup.client.configured else None)
            cache["balance"] = hit
        day_start = time.time() - time.time() % 86400
        return JSONResponse({"ok": True, "live": cfg.live, "purchase_mode": sup.purchase_mode(),
                             "formal_authorized": cfg.authorize_formal,
                             "wallet": hit[1], "min_balance": cfg.min_balance,
                             "orders_today": store.orders_today(day_start), "max_orders": cfg.max_orders_per_day,
                             "unredeemed": store.count_unredeemed(), "open_proposals": len(store.proposals("open")),
                             "exceptions": len(store.pending_manual()), "redeem_url": cfg.redeem_url})

    @guard
    async def pools(request: Request):
        return JSONResponse({"ok": True, "products": [product_json(p) for p in cfg.products], "hint": FEE_HINT})

    def apply_product(p: Product, d: dict) -> None:
        for k, cast in (("title", str), ("description", str), ("mode", str), ("rule", str), ("instruction", str),
                        ("cost", float), ("max_unit_cost", float), ("valid_days", int), ("warn_minutes", int),
                        ("hide_upstream_note", bool)):
            if k in d:
                setattr(p, k, cast(d[k]))
        if p.mode not in ("direct", "redeem") or p.rule not in ("cheapest", "order"):
            raise ValueError("mode must be direct|redeem and rule cheapest|order")
        if "sources" in d:
            srcs = []
            for s in d["sources"]:
                if s.get("type") not in ("16688", "manual", "liandong"):
                    raise ValueError("source type must be 16688, manual or liandong")
                srcs.append(Source(type=s["type"], goods_no=str(s.get("goods_no", "")),
                                   max_unit_cost=float(s.get("max_unit_cost") or 0), label=str(s.get("label", "")),
                                   stock=int(s.get("stock") or 0), note=str(s.get("note", "")),
                                   enabled=bool(s.get("enabled", True))))
            p.sources = srcs

    def new_product(d: dict) -> Product:
        pid = max([p.id for p in cfg.products], default=0) + 1
        sku = max([p.sku_id for p in cfg.products], default=100) + 1
        p = Product(id=pid, sku_id=sku, slug=f"p{pid}", title=str(d.get("title", "")), description="", cost=0.0)
        apply_product(p, d)
        if not p.title:
            raise ValueError("title is required")
        cfg.products.append(p)
        return p

    @guard
    async def pool_save(request: Request):
        try:
            d = await request.json()
            p = cfg.product_by_sku(int(d["sku_id"])) if d.get("sku_id") else None
            if p is None:
                p = new_product(d)
            else:
                apply_product(p, d)
        except (ValueError, KeyError, TypeError) as e:
            return fail(400, "bad_request", str(e))
        persist()
        return JSONResponse({"ok": True, "product": product_json(p)})

    @guard
    async def sweep(request: Request):
        ful.sweep()
        return JSONResponse({"ok": True, "products": [product_json(p) for p in cfg.products]})

    @guard
    async def channel(request: Request):
        hit = cache.get("goods")
        if request.query_params.get("refresh") or not hit or time.time() - hit[0] > 600:
            hit = (time.time(), sup.goods() if sup.client.configured else [])
            cache["goods"] = hit
        used = {s.goods_no: p.title for p in cfg.products for s in p.sources if s.type == "16688"}
        return JSONResponse({"ok": True, "configured": sup.client.configured, "at": _ts(hit[0]),
                             "goods": [{**g, "in_pool": used.get(g["goods_no"], "")} for g in hit[1]],
                             "liandong": {"configured": False,
                                          "reason": "未提供 API 文档，不会询价，也不会付款"},
                             "manual": {"configured": True, "pays_automatically": False}})

    @guard
    async def exceptions(request: Request):
        return JSONResponse({"ok": True, "orders": [order_json(r) for r in store.pending_manual()]})

    @guard
    async def order_act(request: Request):
        req = await request.json()
        row = store.get_by_no(str(req.get("order_no", "")))
        if row is None or row["status"] not in OPEN_STATES:
            return fail(404, "order_not_open")
        action = request.path_params["action"]
        if row["status"] == "awaiting_approval" and action == "deliver":
            return fail(409, "awaiting_approval", "请先在待拍板里同意或拒绝，未同意不会交付")
        if action == "deliver":
            payload = str(req.get("payload", "")).strip()
            if not payload:
                return fail(400, "bad_request", "payload is empty")
            store.finish(row["id"], "delivered", payload=payload)
        elif action == "cancel":
            if row["status"] == "awaiting_approval":
                open_row = store.open_purchase_for(row["order_no"])
                if open_row is not None:
                    store.decide_proposal(open_row["id"], "rejected")
                store.update(row["id"], supplier="")
            store.finish(row["id"], "canceled", error=str(req.get("reason", "canceled by admin"))[:200])
        elif action == "retry":
            # The owner checked the upstream and confirmed nothing was charged. Rebuild a proposal; do not pay here.
            if row["status"] != "needs_review":
                return fail(409, "not_needs_review")
            p = cfg.product_by_id(row["product_id"])
            if p is None:
                return fail(404, "product_not_found")
            store.update(row["id"], status="waiting_stock", supplier="", supplier_ref="", error="")
            ful.process(store.get(row["id"]), p, on_fail="waiting_stock", quiet=True)
        else:
            return fail(404, "unknown_action")
        return JSONResponse({"ok": True, "order": order_json(store.get(row["id"]))})

    @guard
    async def log(request: Request):
        return JSONResponse({"ok": True, "orders": [order_json(r) for r in store.recent(50)]})

    def code_json(c) -> dict:
        p = cfg.product_by_sku(c["sku_id"])
        return {"code": c["code"], "product": p.title if p else "?", "channel": c["channel"],
                "state": core.redeemer.view(c)["state"], "issued_at": _ts(c["issued_at"]),
                "expires_at": _ts(c["expires_at"])}

    @guard
    async def codes(request: Request):
        return JSONResponse({"ok": True, "codes": [code_json(c) for c in store.list_codes(100)]})

    @guard
    async def codes_generate(request: Request):
        req = await request.json()
        p = cfg.product_by_sku(int(req.get("sku_id", 0)))
        n = int(req.get("n", 1))
        if p is None or not 1 <= n <= 200:
            return fail(400, "bad_request", "unknown product or n not in 1..200")
        batch = f"B{int(time.time())}"
        return JSONResponse({"ok": True, "batch": batch, "codes": core.redeemer.issue(p, n, "batch", batch)})

    @guard
    async def codes_void(request: Request):
        req = await request.json()
        c = store.code(str(req.get("code", "")))
        if c is None or c["status"] != "issued":
            return fail(409, "not_voidable", "only codes that were never redeemed can be voided")
        store.update_code(c["code"], status="voided")
        return JSONResponse({"ok": True})

    def proposal_json(r) -> dict:
        d = json.loads(r["body"])
        if r["status"] == "open" and sup.client.configured:      # show today's price, not the one in the proposal
            live = []
            for s in d.get("sources") or []:
                if s.get("type") == "16688" and s.get("goods_no"):
                    q = sup.last_quotes.get(s["goods_no"])
                    if q is None or time.time() - q["ts"] > 600:
                        q = sup.peek(s["goods_no"])
                    s = {**s, "total": q["total"] if q["ok"] else None, "stock": q["stock"] if q["ok"] else 0,
                         "note": s.get("note") or ("" if q["ok"] else "已下架或查不到")}
                    if q["ok"] and q["stock"] > 0:
                        live.append(q["total"])
                    d.setdefault("_src", []).append(s)
            if "_src" in d:
                d["sources"] = d.pop("_src") + [s for s in d["sources"] if s.get("type") != "16688"]
                d["cost"] = min(live) if live else None
        return {"id": r["id"], "status": r["status"], "created_at": _ts(r["created_at"]), **d}

    @guard
    async def proposals(request: Request):
        if request.method == "POST":      # written by the scheduled sourcing job
            try:
                d = await request.json()
                if d.get("type") not in ("new_product", "remove_source") or not d.get("title"):
                    raise ValueError("type must be new_product|remove_source and title is required")
            except ValueError as e:
                return fail(400, "bad_request", str(e))
            return JSONResponse({"ok": True, "id": store.add_proposal(json.dumps(d, ensure_ascii=False))})
        return JSONResponse({"ok": True, "proposals": [proposal_json(r) for r in store.proposals()]})

    def settle_purchase(pid: int, action: str, body: dict):
        """Record the decision once, then execute. A second submit finds the proposal already decided."""
        if not store.decide_proposal(pid, "approved" if action == "approve" else "rejected"):
            return fail(409, "not_open")
        order = store.get_by_no(str(body.get("order_no", "")))
        if action == "reject":
            if order is not None and order["status"] == "awaiting_approval":
                store.update(order["id"], supplier="")
                store.finish(order["id"], "canceled", error="采购提案已拒绝，未付款")
            return JSONResponse({"ok": True, "order": None if order is None else order_json(store.get(order["id"]))})
        if order is None:
            return fail(404, "order_not_open")
        product = cfg.product_by_id(order["product_id"])
        if product is None:
            return fail(404, "product_not_found")
        ful.execute(order, product, body.get("recommended") or {})
        return JSONResponse({"ok": True, "order": order_json(store.get(order["id"]))})

    @guard
    async def proposal_decide(request: Request):
        pid, action = int(request.path_params["pid"]), request.path_params["action"]
        row = store.proposal(pid)
        if row is None or row["status"] != "open" or action not in ("approve", "reject"):
            return fail(409, "not_open")
        d = json.loads(row["body"])
        if d.get("type") == "purchase":
            return settle_purchase(pid, action, d)
        if action == "approve":
            try:
                if d["type"] == "new_product":
                    new_product({"title": d["title"], "mode": d.get("mode", "direct"), "cost": d.get("cost", 0),
                                 "max_unit_cost": d.get("max_unit_cost", 0), "instruction": d.get("instruction", ""),
                                 "sources": d.get("sources", [])})
                elif d["type"] == "remove_source":
                    gone = set(d.get("goods_nos", []))
                    for p in cfg.products:
                        p.sources = [s for s in p.sources if s.goods_no not in gone]
                else:
                    return fail(400, "bad_proposal", "unknown proposal type")
            except (ValueError, KeyError, TypeError) as e:
                return fail(400, "bad_proposal", str(e))
            persist()
        if not store.decide_proposal(pid, "approved" if action == "approve" else "rejected"):
            return fail(409, "not_open")
        return JSONResponse({"ok": True})

    return [
        Route("/ops", page), Route("/ops/", page),
        Route("/ops/login", login, methods=["POST"]),
        Route("/ops/api/overview", overview),
        Route("/ops/api/pools", pools),
        Route("/ops/api/pools/save", pool_save, methods=["POST"]),
        Route("/ops/api/sweep", sweep, methods=["POST"]),
        Route("/ops/api/channel/16688", channel),
        Route("/ops/api/exceptions", exceptions),
        Route("/ops/api/orders/{action:str}", order_act, methods=["POST"]),
        Route("/ops/api/log", log),
        Route("/ops/api/codes", codes),
        Route("/ops/api/codes/generate", codes_generate, methods=["POST"]),
        Route("/ops/api/codes/void", codes_void, methods=["POST"]),
        Route("/ops/api/proposals", proposals, methods=["GET", "POST"]),
        Route("/ops/api/proposals/{pid:int}/{action:str}", proposal_decide, methods=["POST"]),
    ]


OPS_HTML = (Path(__file__).parent / "ops.html").read_text("utf-8")
