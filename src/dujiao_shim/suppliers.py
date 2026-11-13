"""Supplier backends: the 16688 Open API (automatic) and a manual queue (Telegram sellers, anything without an API)."""
import json
import time

import httpx

from open16688_mcp.sign import signed_body

# 16688 purchase order statuses.
PAID_PENDING, CLOSED, REFUNDED, SHIPPED, DONE = 1, 2, 3, 4, 5


class Ambiguous(Exception):
    """The purchase call may or may not have spent money (timeout, bad JSON, HTTP error). Never retry blindly."""


class Refused(Exception):
    """A definitive refusal before any money moved (out of stock, price too high, low balance, ...)."""

    def __init__(self, code: str, message: str, quote: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.quote = quote or {}


class Client16688:
    def __init__(self, app_id: str, secret: str, base_url: str, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0):
        self.app_id, self.secret, self.base = app_id, secret, base_url
        self.http = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": "dujiao-shim/0.1"})

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.secret)

    def call(self, path: str, params: dict) -> dict:
        body = signed_body(path, params, self.app_id, self.secret)
        r = self.http.post(self.base + path, content=json.dumps(body, ensure_ascii=False).encode(),
                           headers={"Content-Type": "application/json"})
        r.raise_for_status()
        return r.json()


class Supplier16688:
    """Quote -> guard rails -> purchase/create -> (poll purchase/query until shipped)."""

    def __init__(self, client: Client16688, live: bool, min_balance: float):
        self.client, self.live, self.min_balance = client, live, min_balance
        self._info_cache: dict[str, tuple[float, dict]] = {}
        self.last_quotes: dict[str, dict] = {}   # goods_no -> latest quote result, for the pool view

    def info(self, goods_no: str, ttl: float = 60.0) -> dict | None:
        """Cached goods/detail (unit_price, available_quantity). None when unavailable."""
        hit = self._info_cache.get(goods_no)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        if not self.client.configured:
            return None
        try:
            res = self.client.call("/openApi/goods/detail", {"goods_no": goods_no})
        except (httpx.HTTPError, ValueError):
            return hit[1] if hit else None
        data = res.get("data") if res.get("code") == 1 else None
        if data:
            self._info_cache[goods_no] = (time.time(), data)
        return data

    def peek(self, goods_no: str, quantity: int = 1) -> dict:
        """Raw quote, remembered in `last_quotes` for the pool view. Never raises."""
        rec = {"ts": time.time(), "ok": False, "total": 0.0, "stock": 0, "message": ""}
        try:
            q = self.client.call("/openApi/purchase/quotePrice", {"goods_no": goods_no, "quantity": quantity})
        except (httpx.HTTPError, ValueError) as e:
            rec.update(code="supplier_unreachable", message=f"quote failed: {type(e).__name__}")
        else:
            if q.get("code") != 1:
                rec.update(code="sku_unavailable", message=f"16688 quote refused: {q.get('msg', '')}")
            else:
                d = q["data"]
                rec.update(ok=True, code="", data=d, total=float(d["total_amount"]),
                           stock=int(d.get("available_quantity", 0)), wallet=float(d.get("available_money", 0)))
        self.last_quotes[goods_no] = rec
        return rec

    def quote(self, goods_no: str, quantity: int, max_unit_cost: float) -> dict:
        """Quote and apply the guard rails. Returns the 16688 quote data or raises Refused (no money moved)."""
        if not self.client.configured:
            raise Refused("supplier_unconfigured", "16688 credentials are not configured")
        rec = self.peek(goods_no, quantity)
        if not rec["ok"]:
            raise Refused(rec["code"], rec["message"])
        d, total = rec["data"], rec["total"]
        if rec["stock"] < quantity:
            raise Refused("insufficient_stock", "16688 stock is lower than the quantity", rec)
        if max_unit_cost and total > max_unit_cost * quantity + 1e-9:
            raise Refused("price_exceeded", f"quoted {total:.2f} exceeds ceiling {max_unit_cost * quantity:.2f}", rec)
        available = float(d.get("available_money", 0))
        if not d.get("balance_enough") or available - total < self.min_balance:
            raise Refused("insufficient_balance", f"wallet {available:.2f} cannot cover {total:.2f}"
                                                  f" with floor {self.min_balance:.2f}", rec)
        return d

    def buy(self, goods_no: str, quantity: int, remark: str, quoted: dict) -> dict:
        """Spend money for an accepted quote. Raises Refused (safe to try the next source) or Ambiguous."""
        total = float(quoted["total_amount"])
        if not self.live:
            return {"trade_no": f"DRYRUN-{int(time.time())}", "status": SHIPPED, "total_amount": total,
                    "cards": [{"code": "DRY-RUN-NOT-A-REAL-CARD"}], "dry_run": True}
        try:
            res = self.client.call("/openApi/purchase/create",
                                   {"goods_no": goods_no, "quantity": quantity, "remark": remark[:500]})
        except (httpx.HTTPError, ValueError) as e:
            raise Ambiguous(f"{type(e).__name__}: {e}") from e
        if res.get("code") != 1:
            # code 0 is a definitive business failure according to the API contract.
            raise Refused("sku_unavailable", f"16688 refused the purchase: {res.get('msg', '')}")
        return res["data"]

    def purchase(self, goods_no: str, quantity: int, max_unit_cost: float, remark: str) -> dict:
        return self.buy(goods_no, quantity, remark, self.quote(goods_no, quantity, max_unit_cost))

    def balance(self) -> float | None:
        try:
            res = self.client.call("/openApi/merchant/balance", {})
        except (httpx.HTTPError, ValueError):
            return None
        d = res.get("data") if res.get("code") == 1 else None
        return float(d.get("operate_money", 0)) if d else None

    def goods(self, max_pages: int = 10) -> list[dict]:
        """Every linked supply source (name, price, stock, sales) without the long HTML descriptions."""
        out = []
        for page in range(1, max_pages + 1):
            try:
                res = self.client.call("/openApi/goods/list", {"page_no": page, "page_size": 10})
            except (httpx.HTTPError, ValueError):
                break
            items = (res.get("data") or {}).get("list") or [] if res.get("code") == 1 else []
            if not items:
                break
            out += [{k: g.get(k) for k in ("goods_no", "name", "unit_price", "available_quantity", "sales_count",
                                           "delivery_method")} for g in items]
        return out

    def query(self, trade_no: str) -> dict | None:
        try:
            res = self.client.call("/openApi/purchase/query", {"trade_no": trade_no})
        except (httpx.HTTPError, ValueError):
            return None
        return res.get("data") if res.get("code") == 1 else None


def cards_text(data: dict, with_instruction: bool = True) -> str:
    """Join 16688 cards/content into the plain-text payload shown to the buyer."""
    parts = [c["code"] for c in data.get("cards") or []]
    if data.get("content"):
        parts.append(data["content"])
    if data.get("manual_instruction"):
        parts.append(data["manual_instruction"])
    text = "\n".join(parts)
    if with_instruction and data.get("instruction"):
        text += "\n\n" + data["instruction"]
    return text
