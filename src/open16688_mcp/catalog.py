"""The 43 Open API endpoints (generated from the official docs) and which ones move money or delete."""
import json
from importlib import resources

ENDPOINTS: dict[str, dict] = {
    e["path"]: e for e in json.loads(resources.files(__package__).joinpath("endpoints.json").read_text("utf-8"))
}

# Refused unless OPEN16688_ALLOW_DANGEROUS lists them (or is '*'): spend the wallet, refund buyers, or delete.
DANGEROUS = {
    "/openApi/purchase/create",
    "/openApi/order/refund",
    "/openApi/complaint/refund",
    "/openApi/goods/delete",
    "/openApi/stockSecret/invalidateAll",
}

READ_ONLY = {
    p for p in ENDPOINTS
    if p.rsplit("/", 1)[1] in {"list", "detail", "info", "query", "tree", "statistics", "balance",
                               "quotePrice", "complaintDetail", "complaintMessageList", "messageList"}
}


class Policy:
    def __init__(self, allow_dangerous: set[str], read_only: bool):
        self.allow_dangerous = allow_dangerous
        self.read_only = read_only

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "Policy":
        raw = env.get("OPEN16688_ALLOW_DANGEROUS", "").strip()
        allowed = set(DANGEROUS) if raw == "*" else {p.strip() for p in raw.split(",") if p.strip()}
        return cls(allowed, env.get("OPEN16688_READ_ONLY", "") in ("1", "true", "yes"))

    def refuse(self, path: str, params: dict) -> str | None:
        ep = ENDPOINTS.get(path)
        if ep is None:
            return f"unknown endpoint {path}; call list_endpoints to see the 43 valid paths"
        if self.read_only and path not in READ_ONLY:
            return f"{path} writes data and this server runs with OPEN16688_READ_ONLY=1"
        if path in DANGEROUS and path not in self.allow_dangerous:
            return (f"{path} ({ep['name']}) spends money, refunds or deletes; the server owner must add it "
                    "to OPEN16688_ALLOW_DANGEROUS first")
        missing = [p["name"] for p in ep["params"] if p["required"] and params.get(p["name"]) in (None, "")]
        if missing:
            return f"missing required parameter(s) for {path}: {', '.join(missing)}"
        return None
