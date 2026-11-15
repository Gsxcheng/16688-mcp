"""Our own redeem codes: issued for free when something is sold, turned into a real purchase when redeemed."""
import re
import secrets
import time

from .config import Config, Product
from .fulfil import Fulfiller
from .store import Store

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"     # Crockford base32: no I, L, O, U
CODE_RE = re.compile(r"^GG(-[0-9A-HJKMNP-TV-Z]{5}){4}$")


def new_code() -> str:
    return "GG-" + "-".join("".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(4))


def normalize(raw: str) -> str:
    return raw.strip().upper().replace(" ", "")


class Redeemer:
    def __init__(self, cfg: Config, store: Store, ful: Fulfiller):
        self.cfg, self.store, self.ful = cfg, store, ful

    def issue(self, product: Product, n: int, channel: str, ref: str) -> list[str]:
        """Create n codes. For channel "dujiao" the same ref always returns the same codes (idempotent)."""
        if channel == "dujiao":
            have = [r["code"] for r in self.store.codes_by_ref(channel, ref)]
            if have:
                return have
        expires = time.time() + product.valid_days * 86400
        out = []
        for _ in range(n):
            code = new_code()
            self.store.add_code(code, product.sku_id, channel, ref, expires)
            out.append(code)
        return out

    def delivery_text(self, product: Product, codes: list[str]) -> str:
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() + product.valid_days * 86400))
        return ("\n".join(codes) + f"\n\n兑换地址：{self.cfg.redeem_url}\n请在 {day} 前兑换。"
                + (f"\n{product.instruction}" if product.instruction else ""))

    def view(self, row) -> dict:
        """What the buyer may see for a code. Never leaks supplier names or internal errors."""
        product = self.cfg.product_by_sku(row["sku_id"])
        base = {"code": row["code"], "product": product.title if product else ""}
        if row["status"] == "voided":
            return {**base, "state": "voided", "message": "这个卡密已作废。如有疑问请联系售后。"}
        if row["order_id"] is None:
            if row["status"] == "redeeming":
                return {**base, "state": "processing", "message": "正在处理，请稍后用同一个卡密回来查看。"}
            if row["expires_at"] < time.time():
                day = time.strftime("%Y-%m-%d", time.localtime(row["expires_at"]))
                return {**base, "state": "expired", "message": f"这个卡密已过有效期（{day}）。请联系售后处理。"}
            return {**base, "state": "ready"}
        order = self.store.get(row["order_id"])
        if order["status"] == "delivered":
            return {**base, "state": "delivered", "payload": order["payload"],
                    "instruction": product.instruction if product else "",
                    "redeemed_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(row["redeemed_at"] or 0))}
        if order["status"] == "canceled":
            return {**base, "state": "voided", "message": "这一单已取消，请联系售后退款。"}
        return {**base, "state": "processing",
                "message": "这个商品暂时需要一点时间处理。到货后会自动发放，请稍后用同一个卡密回来查看，不用重新购买。"}

    def lookup(self, raw: str) -> dict:
        code = normalize(raw)
        row = self.store.code(code) if CODE_RE.match(code) else None
        if row is None:
            return {"state": "not_found", "message": "没有找到这个卡密，请检查是否完整复制。"}
        return self.view(row)

    def redeem(self, raw: str, confirm: bool = False) -> dict:
        code = normalize(raw)
        if not CODE_RE.match(code):
            return {"state": "not_found", "message": "卡密格式不对，请检查是否完整复制。"}
        row = self.store.code(code)
        if row is None:
            return {"state": "not_found", "message": "没有找到这个卡密，请检查是否完整复制。"}
        v = self.view(row)
        if v["state"] != "ready":
            return v
        product = self.cfg.product_by_sku(row["sku_id"])
        if product is None:
            self.ful.notify.send(f"卡密 {code[:8]}… 对应的商品已不在目录里，买家无法兑换，请处理。")
            return {**v, "state": "processing", "message": "这个商品正在调整，请联系售后处理。"}
        if product.warn_minutes and not confirm:
            return {**v, "state": "confirm",
                    "message": f"兑换后请在 {product.warn_minutes} 分钟内使用，过时上游不保证有效。确定现在兑换吗？"}
        if not self.store.claim_code(code):          # someone else (double click, second tab) got there first
            return self.view(self.store.code(code))
        order = self.store.create(product.sku_id, product.id, 1, None, "")
        self.store.update_code(code, order_id=order["id"])
        self.ful.process(order, product, on_fail="waiting_stock")
        return self.view(self.store.code(code))
