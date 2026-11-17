"""Order orchestration: pick a source, buy, record, notify, and talk back to Dujiao."""
import json
import threading
import time
from urllib.parse import urlparse

import httpx

from . import auth
from .config import Config, Product
from .store import Store
from .suppliers import (CLOSED, DONE, REFUNDED, SHIPPED, SIMULATED_MARK, Ambiguous, Refused, Supplier16688,
                        cards_text)

CALLBACK_MAX_ATTEMPTS = 12

_VERDICT_NOTE = {
    "recommended": "建议执行", "candidate": "可买，没轮到", "chosen": "选它",
    "insufficient_stock": "缺货", "price_exceeded": "超过上限价", "insufficient_balance": "钱包不够",
    "sku_unavailable": "下架或不可买", "supplier_unreachable": "查价失败", "rate_limited": "今天单数已到上限",
    "manual_fallback": "转人工", "ambiguous": "下单结果不明", "supplier_unconfigured": "渠道未接通",
    "unconfigured": "未配置，不会调用", "formal_not_authorized": "正式采购未授权",
}


def dujiao_status(row) -> str:
    return {"delivered": "delivered", "canceled": "canceled", "rejected": "canceled"}.get(row["status"], "paid")


def fulfillment(row) -> dict | None:
    if row["status"] != "delivered":
        return None
    return {"type": "auto", "status": "delivered", "payload": row["payload"], "delivery_data": None,
            "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row["delivered_at"] or time.time()))}


class Notifier:
    """Feishu group-bot text messages. Never put card codes or secrets in here."""

    def __init__(self, webhook: str, prefix: str, transport: httpx.BaseTransport | None = None):
        self.webhook, self.prefix = webhook, prefix
        self.http = httpx.Client(timeout=10, transport=transport)
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        msg = f"{self.prefix}\n{text}"
        self.sent.append(msg)
        if not self.webhook:
            return
        try:
            self.http.post(self.webhook, json={"msg_type": "text", "content": {"text": msg}})
        except httpx.HTTPError:
            pass  # a notification failure must never break order processing


class Fulfiller:
    def __init__(self, cfg: Config, store: Store, sup: Supplier16688, notify: Notifier,
                 transport: httpx.BaseTransport | None = None):
        self.cfg, self.store, self.sup, self.notify = cfg, store, sup, notify
        self.http = httpx.Client(timeout=15, transport=transport)
        self._order_lock = threading.Lock()   # serialises purchases: at most one 16688 buy in flight
        self.health: dict[int, str] = {}      # sku_id -> ok | near_cap | down (last sweep)
        self.sweep_gap = 0.0                  # seconds between sweep quotes; main() sets 1s to be gentle on 16688
        self._next_sweep = time.time() + 30   # first sweep shortly after start, then every cfg.sweep_seconds
        self._next_retry = 0.0

    def recover(self) -> None:
        """After a crash a 16688 order with no trade_no may or may not have been bought: stop and ask a human."""
        for row in self.store.interrupted_purchases():
            self.store.update(row["id"], status="needs_review",
                              error="interrupted during purchase; check 16688 purchase records before retrying")
            self.notify.send(f"订单 {row['order_no']} 采购过程中服务中断，结果不确定，已停止自动重试。请到 16688 采购记录核对。")
        for c in self.store.orphan_claims():   # claimed but no order row yet: nothing was bought, safe to release
            self.store.update_code(c["code"], status="issued", redeemed_at=None)

    def process(self, row, product: Product, on_fail: str = "rejected", quiet: bool = False) -> str | None:
        """Compare the pool and open one purchase proposal. Never calls purchase/create.

        Returns an error code when nothing can be proposed and no money moved. on_fail="rejected" (Dujiao may
        retry the same downstream order) or "waiting_stock" (a redemption stays open until a source appears)."""
        with self._order_lock:
            return self._propose(row, product, on_fail, quiet)

    def _propose(self, row, product: Product, on_fail: str, quiet: bool) -> str | None:
        oid, qty = row["id"], row["quantity"]
        if self.store.open_purchase_for(row["order_no"]):
            self.store.update(oid, status="awaiting_approval")
            return None
        last = Refused("sku_unavailable", "no source available")
        trace: list[dict] = []
        pool = [s for s in product.sources if s.enabled]
        day_start = time.time() - time.time() % 86400
        capped = self.store.orders_today(day_start) >= self.cfg.max_orders_per_day
        cands = []
        for idx, src in enumerate(pool):
            if src.type == "liandong":
                trace.append({"src": src.label or "链动小铺", "verdict": "unconfigured", "type": "liandong"})
                continue
            if src.type != "16688":
                continue
            if capped:
                last = Refused("rate_limited", "daily purchase cap reached")
                trace.append({"src": src.goods_no, "verdict": "rate_limited", "type": "16688"})
                continue
            try:
                q = self.sup.quote(src.goods_no, qty, src.max_unit_cost or product.max_unit_cost)
            except Refused as e:
                last = e
                trace.append({"src": src.goods_no, "verdict": e.code, "type": "16688",
                              "total": e.quote.get("total"), "stock": e.quote.get("stock")})
                continue
            entry = {"src": src.goods_no, "verdict": "candidate", "type": "16688",
                     "total": float(q["total_amount"]), "stock": int(q.get("available_quantity", 0))}
            trace.append(entry)
            cands.append((entry["total"], idx, src, entry))
        if product.rule != "order":
            cands.sort(key=lambda c: (c[0], c[1]))      # cheapest first; list order breaks ties
        recommended = None
        if cands:
            best = cands[0][3]
            best["verdict"] = "recommended"
            recommended = {"type": "16688", "goods_no": best["src"], "total": best["total"], "stock": best["stock"]}
        else:
            for src in pool:
                if src.type == "manual" and src.stock > 0:
                    trace.append({"src": src.label, "verdict": "recommended", "type": "manual", "stock": src.stock})
                    recommended = {"type": "manual", "label": src.label, "stock": src.stock}
                    break
        self.store.update(oid, trace=json.dumps(trace, ensure_ascii=False))
        if recommended is None:
            self.store.update(oid, status=on_fail, error=last.message, supplier="", supplier_ref="")
            if not quiet and (on_fail == "waiting_stock"
                              or last.code in ("insufficient_balance", "price_exceeded", "rate_limited")):
                what = "暂时没有可执行的货源，已转为等待" if on_fail == "waiting_stock" else "被拒绝"
                self.notify.send(f"订单 {row['order_no']}（{product.title}）{what}：{last.message}。")
            return last.code
        self._open_proposal(row, product, recommended, trace)
        supplier = "16688" if recommended["type"] == "16688" else ""
        self.store.update(oid, status="awaiting_approval", supplier=supplier, supplier_ref="", error="")
        return None

    def _open_proposal(self, row, product: Product, recommended: dict, trace: list[dict]) -> None:
        simulated = self.sup.purchase_mode() != "formal"
        sources = []
        for t in trace:
            sources.append({"type": t.get("type") or "", "goods_no": t["src"] if t.get("type") == "16688" else "",
                            "label": t["src"] if t.get("type") != "16688" else "", "name": t["src"],
                            "total": t.get("total"), "stock": t.get("stock"),
                            "note": _VERDICT_NOTE.get(t["verdict"], t["verdict"])})
        body = {
            "type": "purchase", "title": f"采购「{product.title}」×{row['quantity']}",
            "order_no": row["order_no"], "sku_id": product.sku_id, "quantity": row["quantity"],
            "mode": product.mode, "recommended": recommended, "sources": sources, "simulated": simulated,
            "reason": "货源比较后的候选。同意后才执行；拒绝则不付款。",
            "risk": ("【模拟数据】不会向正式 16688 付款。" if simulated else "将向已单独授权的正式 16688 付款。"),
        }
        self.store.add_proposal(json.dumps(body, ensure_ascii=False))
        where = recommended.get("goods_no") or recommended.get("label") or recommended.get("type")
        self.notify.send(f"订单 {row['order_no']}（{product.title} ×{row['quantity']}）已生成采购提案，"
                         f"建议 {where}。尚未付款，请到采购决策里拍板。")

    def execute(self, row, product: Product, recommended: dict) -> None:
        """The owner approved this proposal. This is the only path that may call purchase/create."""
        with self._order_lock:
            self._execute(row, product, recommended)

    def _execute(self, row, product: Product, recommended: dict) -> None:
        row = self.store.get(row["id"])
        if row is None or row["status"] != "awaiting_approval":
            return
        oid, qty = row["id"], row["quantity"]
        if recommended.get("type") == "manual":
            label = str(recommended.get("label") or "人工")
            trace = json.loads(row["trace"] or "[]")
            trace.append({"src": label, "verdict": "manual_fallback", "type": "manual"})
            self.store.update(oid, status="awaiting_manual", supplier="manual", supplier_ref=label, error="",
                              trace=json.dumps(trace, ensure_ascii=False))
            self.notify.send(f"订单 {row['order_no']}（{product.title} ×{qty}）已转入人工队列，来源：{label}。"
                             "不会自动付款。采购后到后台粘贴交付内容。")
            return
        if recommended.get("type") == "liandong":
            self.store.update(oid, status="needs_review", supplier="", supplier_ref="",
                              error="链动小铺未配置，没有可调用的接口，未付款")
            self.notify.send(f"订单 {row['order_no']} 的提案指向链动小铺，但接口文档未提供，已拒绝执行。")
            return
        on_fail = "waiting_stock" if self.store.code_by_order(oid) else "rejected"
        self._buy_16688(row, product, on_fail)

    def _buy_16688(self, row, product: Product, on_fail: str) -> None:
        """Quote again and buy, falling through when a source refuses before any money moves."""
        oid, qty = row["id"], row["quantity"]
        last = Refused("sku_unavailable", "no source available")
        trace: list[dict] = []

        def save_trace():
            self.store.update(oid, trace=json.dumps(trace, ensure_ascii=False))

        pool = [s for s in product.sources if s.enabled and s.type == "16688"]
        cands = []
        for idx, src in enumerate(pool):
            try:
                q = self.sup.quote(src.goods_no, qty, src.max_unit_cost or product.max_unit_cost)
            except Refused as e:
                last = e
                trace.append({"src": src.goods_no, "verdict": e.code, "type": "16688",
                              "total": e.quote.get("total"), "stock": e.quote.get("stock")})
                continue
            entry = {"src": src.goods_no, "verdict": "candidate", "type": "16688",
                     "total": float(q["total_amount"]), "stock": int(q.get("available_quantity", 0))}
            trace.append(entry)
            cands.append((entry["total"], idx, src, q, entry))
        if product.rule != "order":
            cands.sort(key=lambda c: (c[0], c[1]))
        for _, _, src, q, entry in cands:
            # status=processing + empty supplier_ref is what recover() looks for after a crash mid-purchase
            self.store.update(oid, status="processing", supplier="16688", supplier_ref="")
            try:
                data = self.sup.buy(src.goods_no, qty, f"GG Shop {row['order_no']}", q)
            except Refused as e:
                self.store.update(oid, supplier="16688", supplier_ref="")
                last = e
                entry["verdict"] = e.code
                if e.code == "formal_not_authorized":
                    save_trace()
                    self.store.update(oid, status="needs_review", error=e.message)
                    self.notify.send(f"订单 {row['order_no']} 正式采购未授权，已拒绝付款。")
                    return
                continue
            except Ambiguous as e:
                entry["verdict"] = "ambiguous"
                save_trace()
                self.store.update(oid, status="needs_review", error=f"purchase result unknown: {e}")
                self.notify.send(f"订单 {row['order_no']} 向 16688 下单时出现超时或异常，是否扣款不确定，已停止自动重试。"
                                 "请到采购记录核对，再在后台手工交付或重新生成提案。")
                return
            entry["verdict"] = "chosen"
            save_trace()
            self.store.update(oid, status="processing", supplier_ref=data["trade_no"],
                              amount=f"{float(data.get('total_amount', 0)):.2f}")
            self._apply_16688(oid, row["order_no"], data, product)
            return
        for src in product.sources:
            if src.enabled and src.type == "manual" and src.stock > 0:
                trace.append({"src": src.label, "verdict": "manual_fallback", "type": "manual"})
                save_trace()
                self.store.update(oid, status="awaiting_manual", supplier="manual", supplier_ref=src.label, error="")
                self.notify.send(f"订单 {row['order_no']}（{product.title} ×{qty}）16688 未能成交且未付款，"
                                 f"已转入人工队列：{src.label}。")
                return
        save_trace()
        self.store.update(oid, status=on_fail, error=last.message, supplier="", supplier_ref="")
        self.notify.send(f"订单 {row['order_no']}（{product.title}）同意后仍未能采购，未付款：{last.message}。")

    def _apply_16688(self, oid: int, order_no: str, data: dict, product: Product | None = None) -> None:
        status = int(data.get("status", 0))
        if status in (SHIPPED, DONE):
            text = cards_text(data, with_instruction=not (product and product.hide_upstream_note))
            if data.get("dry_run") or data.get("simulated") or self.sup.purchase_mode() != "formal":
                if SIMULATED_MARK not in text:
                    text = SIMULATED_MARK + "\n" + text
            if data.get("dry_run"):
                self.notify.send(f"订单 {order_no} 为模拟模式，未调用下单接口，交付的是假卡密。")
            elif data.get("simulated"):
                self.notify.send(f"订单 {order_no} 走的是模拟上游，未向正式 16688 付款。")
            self.store.finish(oid, "delivered", payload=text)
        elif status in (CLOSED, REFUNDED):
            self.store.finish(oid, "canceled", error="16688 closed or refunded the purchase")
            self.notify.send(f"订单 {order_no} 在 16688 被关闭或退款，已通知 Dujiao 取消。")
        # status 0/1: still processing; the poller picks it up.

    def sweep(self) -> None:
        """Pool health check: quote every 16688 pool member and tell the owner once when a product's
        availability changes or its cheapest price gets close to the ceiling."""
        for p in self.cfg.products:
            best, any_ok = None, False
            for src in p.sources:
                if not src.enabled:
                    continue
                if src.type == "manual":
                    any_ok = any_ok or src.stock > 0
                    continue
                if src.type != "16688":
                    continue   # 链动小铺 has no API; never quote it
                if not self.sup.client.configured:
                    continue
                rec = self.sup.peek(src.goods_no)
                cap = src.max_unit_cost or p.max_unit_cost
                if rec["ok"] and rec["stock"] > 0 and (not cap or rec["total"] <= cap + 1e-9):
                    any_ok = True
                    ratio = rec["total"] / cap if cap else 0
                    best = ratio if best is None else min(best, ratio)
                if self.sweep_gap:
                    time.sleep(self.sweep_gap)
            state = "ok" if any_ok else "down"
            if any_ok and best is not None and best >= 0.9:
                state = "near_cap"
            prev = self.health.get(p.sku_id)
            self.health[p.sku_id] = state
            if prev is None and state == "ok" or prev == state:
                continue
            if state == "down":
                self.notify.send(f"商品「{p.title}」货源池里没有可买的货源，卡网已显示缺货。闲鱼如在售请留意。")
            elif state == "near_cap":
                self.notify.send(f"商品「{p.title}」最便宜的货源已到上限价的 {best * 100:.0f}%，再涨就买不了，建议调价。")
            elif prev == "down":
                self.notify.send(f"商品「{p.title}」货源已恢复。")

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for row in self.store.polling_orders():
            data = self.sup.query(row["supplier_ref"])
            if data:
                self._apply_16688(row["id"], row["order_no"], data, self.cfg.product_by_id(row["product_id"]))
        if now >= self._next_retry:
            self._next_retry = now + self.cfg.retry_seconds
            for row in self.store.waiting_stock():
                product = self.cfg.product_by_id(row["product_id"])
                if product:
                    self.process(row, product, on_fail="waiting_stock", quiet=True)
        if now >= self._next_sweep:
            self._next_sweep = now + self.cfg.sweep_seconds
            self.sweep()
        for row in self.store.due_callbacks(now):
            self._callback(row, now)

    def _callback(self, row, now: float) -> None:
        url = row["callback_url"] or self.cfg.callback_url
        if not url or not row["downstream_order_no"]:
            self.store.update(row["id"], callback_state="none")
            return
        body = json.dumps({"event": "order.status", "order_id": row["id"], "order_no": row["order_no"],
                           "downstream_order_no": row["downstream_order_no"], "status": dujiao_status(row),
                           "fulfillment": fulfillment(row), "timestamp": int(now)},
                          ensure_ascii=False, separators=(",", ":")).encode()
        headers = auth.signed_headers(self.cfg.api_key, self.cfg.api_secret, "POST", urlparse(url).path, body)
        headers["Content-Type"] = "application/json"
        ok = False
        try:
            r = self.http.post(url, content=body, headers=headers)
            ok = r.status_code == 200 and r.json().get("ok") is True
        except (httpx.HTTPError, ValueError):
            ok = False
        attempts = row["callback_attempts"] + 1
        if ok:
            self.store.update(row["id"], callback_state="done", callback_attempts=attempts)
        elif attempts >= CALLBACK_MAX_ATTEMPTS:
            self.store.update(row["id"], callback_state="failed", callback_attempts=attempts)
            self.notify.send(f"订单 {row['order_no']} 的回调多次失败。Dujiao 仍可通过轮询拿到结果，请在后台采购单里手动重试。")
        else:
            self.store.update(row["id"], callback_attempts=attempts,
                              next_callback_at=now + min(30 * 2 ** attempts, 3600))

    def start(self) -> threading.Event:
        stop = threading.Event()

        def loop():
            while not stop.wait(self.cfg.poll_seconds):
                try:
                    self.tick()
                except Exception:  # keep the worker alive; next tick retries
                    pass

        threading.Thread(target=loop, name="shim-worker", daemon=True).start()
        return stop
