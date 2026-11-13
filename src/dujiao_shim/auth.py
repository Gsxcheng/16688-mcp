"""Dujiao-Next upstream request signing (used both to verify incoming calls and to sign callbacks)."""
import hashlib
import hmac
import time

MAX_SKEW = 60  # seconds, matches the Dujiao documentation
EMPTY_MD5 = "d41d8cd98f00b204e9800998ecf8427e"


def sign_string(method: str, path: str, timestamp: str, body: bytes) -> str:
    return f"{method.upper()}\n{path}\n{timestamp}\n{hashlib.md5(body).hexdigest()}"


def signature(api_secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    msg = sign_string(method, path, timestamp, body).encode()
    return hmac.new(api_secret.encode(), msg, hashlib.sha256).hexdigest()


def signed_headers(api_key: str, api_secret: str, method: str, path: str, body: bytes,
                   now: int | None = None) -> dict[str, str]:
    ts = str(int(time.time()) if now is None else now)
    return {
        "Dujiao-Next-Api-Key": api_key,
        "Dujiao-Next-Timestamp": ts,
        "Dujiao-Next-Signature": signature(api_secret, method, path, ts, body),
    }


def verify(headers, method: str, path: str, body: bytes, api_key: str, api_secret: str,
           now: float | None = None) -> str | None:
    """Return None when the request is authentic, else a Dujiao error code."""
    key = headers.get("dujiao-next-api-key")
    ts = headers.get("dujiao-next-timestamp")
    sig = headers.get("dujiao-next-signature")
    if not key or not ts or not sig:
        return "missing_auth_headers"
    try:
        skew = abs((time.time() if now is None else now) - int(ts))
    except ValueError:
        return "invalid_timestamp"
    if skew > MAX_SKEW:
        return "timestamp_expired"
    if not hmac.compare_digest(key, api_key):
        return "invalid_api_key"
    if not hmac.compare_digest(sig.lower(), signature(api_secret, method, path, ts, body)):
        return "invalid_signature"
    return None
