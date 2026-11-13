"""Environment + product catalog for the shim."""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Source:
    """One place a product can be bought from. Sources are tried in list order."""
    type: str                      # "16688" or "manual"
    goods_no: str = ""             # 16688 goods number (type "16688")
    max_unit_cost: float = 0.0     # refuse to buy when the 16688 total per unit exceeds this (fee included)
    label: str = ""                # human label shown in notifications (type "manual")
    stock: int = 0                 # advertised stock for a manual source (0 = not available)
    note: str = ""                 # our own usage note shown to the buyer instead of the supplier's text
    enabled: bool = True           # disabled pool members are kept in the catalog but never bought from


@dataclass
class Product:
    id: int
    sku_id: int
    slug: str
    title: str
    description: str
    cost: float                    # advertised upstream price; Dujiao adds its own markup on top
    category_id: int = 1
    images: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)   # the pool
    mode: str = "direct"           # "direct" = buy when Dujiao orders; "redeem" = issue our own code, buy on redemption
    rule: str = "cheapest"         # "cheapest" = quote every 16688 pool member, buy the cheapest; "order" = list order
    max_unit_cost: float = 0.0     # pool-wide ceiling (fee included) for members without their own
    valid_days: int = 7            # lifetime of an issued code
    warn_minutes: int = 0          # >0: the redeem page asks the buyer to confirm they will use it within N minutes
    instruction: str = ""          # our own instruction shown on the redeem page
    hide_upstream_note: bool = False   # do not pass the supplier's instruction text on to the buyer


@dataclass
class Config:
    api_key: str
    api_secret: str
    admin_token: str
    db_path: str
    products: list[Product]
    app_id: str = ""
    secret: str = ""
    base_url: str = "https://www.16688.com.cn"
    live: bool = False             # False = never call purchase/create; deliver a clearly fake card instead
    max_orders_per_day: int = 20
    min_balance: float = 0.0       # keep at least this much in the 16688 operating wallet
    callback_url: str = ""         # default Dujiao callback endpoint (public URL)
    site_name: str = "GG Upstream Shim"
    feishu_webhook: str = ""
    notify_prefix: str = "GG Shop 采购提醒"
    poll_seconds: float = 5.0
    catalog_path: str = ""         # when set, pool edits made in /ops are written back here
    redeem_url: str = "https://shop.gsxcheng.ltd/redeem/"
    sweep_seconds: float = 1800.0  # pool health check interval
    retry_seconds: float = 300.0   # how often redemptions waiting for stock are retried
    redeem_per_minute: int = 10    # per-IP limit on the public redeem API

    def product_by_sku(self, sku_id: int) -> Product | None:
        return next((p for p in self.products if p.sku_id == sku_id), None)

    def product_by_id(self, product_id: int) -> Product | None:
        return next((p for p in self.products if p.id == product_id), None)


def load_products(path: str) -> list[Product]:
    out = []
    for raw in json.loads(Path(path).read_text("utf-8")):
        srcs = [Source(**s) for s in raw.pop("sources", [])]
        out.append(Product(sources=srcs, **raw))
    seen = set()
    for p in out:
        if p.sku_id in seen:
            raise ValueError(f"duplicate sku_id {p.sku_id}")
        seen.add(p.sku_id)
    return out


def save_products(path: str, products: list[Product]) -> None:
    """Atomically write the catalog back (pool edits from /ops)."""
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(p) for p in products], ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def from_env(env: dict[str, str]) -> Config:
    def need(name: str, min_len: int = 1) -> str:
        v = env.get(name, "").strip()
        if len(v) < min_len:
            raise SystemExit(f"{name} must be set (at least {min_len} characters)")
        return v

    return Config(
        api_key=need("SHIM_API_KEY", 8),
        api_secret=need("SHIM_API_SECRET", 32),
        admin_token=need("SHIM_ADMIN_TOKEN", 32),
        db_path=env.get("SHIM_DB", "/data/shim.db"),
        products=load_products(env.get("SHIM_CATALOG", "/data/catalog.json")),
        catalog_path=env.get("SHIM_CATALOG", "/data/catalog.json"),
        redeem_url=env.get("SHIM_REDEEM_URL", "https://shop.gsxcheng.ltd/redeem/"),
        app_id=env.get("OPEN16688_APP_ID", ""),
        secret=env.get("OPEN16688_SECRET", ""),
        base_url=env.get("OPEN16688_BASE_URL", "https://www.16688.com.cn").rstrip("/"),
        live=env.get("SHIM_LIVE_PURCHASE", "") in ("1", "true", "yes"),
        max_orders_per_day=int(env.get("SHIM_MAX_ORDERS_PER_DAY", "20")),
        min_balance=float(env.get("SHIM_MIN_BALANCE", "0")),
        callback_url=env.get("SHIM_CALLBACK_URL", ""),
        site_name=env.get("SHIM_SITE_NAME", "GG Upstream Shim"),
        feishu_webhook=env.get("SHIM_FEISHU_WEBHOOK", ""),
        notify_prefix=env.get("SHIM_NOTIFY_PREFIX", "GG Shop 采购提醒"),
    )
