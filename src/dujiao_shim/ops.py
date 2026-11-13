"""/ops: the owner's decision console (proposals, pools, channels, exceptions, redemption log).

Everything under /ops/api needs the admin token. The page itself is static and holds no data."""
import hmac
import json
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .config import Product, Source, save_products
from .store import OPEN_STATES

FEE_HINT = "总价含 16688 手续费"


def _ts(t: float | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(t)) if t else ""


def ops_routes(core) -> list[Route]:
    cfg, store, sup, ful = core.cfg, core.store, core.sup, core.ful
    cache: dict[str, tuple[float, object]] = {}

    def fail(status: int, code: str, message: str = "") -> JSONResponse:
        return JSONResponse({"ok": False, "error_code": code, "error_message": message or code}, status_code=status)

    def guard(fn):
        async def endpoint(request: Request):
            if not hmac.compare_digest(request.headers.get("x-admin-token", ""), cfg.admin_token):
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
        return HTMLResponse(OPS_HTML, headers={"Cache-Control": "no-store"})

    @guard
    async def overview(request: Request):
        hit = cache.get("balance")
        if not hit or time.time() - hit[0] > 60:
            hit = (time.time(), sup.balance() if sup.client.configured else None)
            cache["balance"] = hit
        day_start = time.time() - time.time() % 86400
        return JSONResponse({"ok": True, "live": cfg.live, "wallet": hit[1], "min_balance": cfg.min_balance,
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
                if s.get("type") not in ("16688", "manual"):
                    raise ValueError("source type must be 16688 or manual")
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
                             "goods": [{**g, "in_pool": used.get(g["goods_no"], "")} for g in hit[1]]})

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
        if action == "deliver":
            payload = str(req.get("payload", "")).strip()
            if not payload:
                return fail(400, "bad_request", "payload is empty")
            store.finish(row["id"], "delivered", payload=payload)
        elif action == "cancel":
            store.finish(row["id"], "canceled", error=str(req.get("reason", "canceled by admin"))[:200])
        elif action == "retry":
            # The owner checked 16688 and confirmed nothing was charged: safe to let the pool try again.
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

    @guard
    async def proposal_decide(request: Request):
        pid, action = int(request.path_params["pid"]), request.path_params["action"]
        row = store.proposal(pid)
        if row is None or row["status"] != "open" or action not in ("approve", "reject"):
            return fail(409, "not_open")
        d = json.loads(row["body"])
        if action == "approve":
            try:
                if d["type"] == "new_product":
                    new_product({"title": d["title"], "mode": d.get("mode", "direct"), "cost": d.get("cost", 0),
                                 "max_unit_cost": d.get("max_unit_cost", 0), "instruction": d.get("instruction", ""),
                                 "sources": d.get("sources", [])})
                else:
                    gone = set(d.get("goods_nos", []))
                    for p in cfg.products:
                        p.sources = [s for s in p.sources if s.goods_no not in gone]
            except (ValueError, KeyError, TypeError) as e:
                return fail(400, "bad_proposal", str(e))
            persist()
        store.decide_proposal(pid, "approved" if action == "approve" else "rejected")
        return JSONResponse({"ok": True})

    return [
        Route("/ops", page), Route("/ops/", page),
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
