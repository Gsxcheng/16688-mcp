"""Request signing for the 16688 Open API (HMAC-SHA256, see the official docs' PHP sample)."""
import hashlib
import hmac
import json
import secrets
import time


def _normalize(value):
    """Mirror PHP: lists keep order, maps are key-sorted recursively, {} decodes to []."""
    if isinstance(value, dict):
        if not value:
            return []
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def canonical_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        # PHP json_encode with JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES still escapes U+2028/9.
        s = json.dumps(_normalize(value), ensure_ascii=False, separators=(",", ":"))
        return s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    if isinstance(value, float):
        # PHP (string)$float uses precision=14.
        return format(value, ".14G")
    return str(value)


def controller_action(path: str) -> tuple[str, str]:
    parts = [p for p in path.split("/") if p]
    if len(parts) != 3 or parts[0] != "openApi":
        raise ValueError(f"not an /openApi/<controller>/<action> path: {path}")
    return parts[1].lower(), parts[2].lower()


def plain_text(method: str, path: str, params: dict) -> str:
    controller, action = controller_action(path)
    pairs = "&".join(f"{k}={canonical_value(params[k])}" for k in sorted(params) if k != "sign")
    return f"{method.upper()}\n{controller}\n{action}\n{pairs}"


def sign(method: str, path: str, params: dict, secret: str) -> str:
    msg = plain_text(method, path, params).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def signed_body(path: str, params: dict, app_id: str, secret: str,
                now: int | None = None, nonce: str | None = None) -> dict:
    """Return params plus app_id, timestamp, nonce and sign, ready to POST as JSON."""
    body = {k: v for k, v in params.items() if k not in ("app_id", "timestamp", "nonce", "sign")}
    body.update(app_id=app_id, timestamp=str(int(time.time()) if now is None else now),
                nonce=nonce or secrets.token_hex(16))
    body["sign"] = sign("POST", path, body, secret)
    return body
