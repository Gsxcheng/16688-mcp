#!/usr/bin/env python3
"""Minimal mock acceptance for dujiao-shim V0.1.

Talks only to the local shim and, if asked, counts purchase/create lines in the
mock log. Prints statuses and booleans. Does not print tokens, GG codes, or card
payloads.

Against the test sandbox, after dj-shim has been replaced:

    python3 accept-mock.py --from-container dj-shim

Locally it also accepts --shim, --public, --token and --mock-log.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request


def _json_or_text(raw: bytes):
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"_text": raw.decode("utf-8", "replace")}


def request(url, method="GET", body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), _json_or_text(raw)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), _json_or_text(e.read())


class Client:
    def __init__(self, shim, public):
        self.shim, self.public, self.cookie = shim.rstrip("/"), public.rstrip("/"), ""

    def call(self, base, path, method="GET", body=None, auth=False):
        headers = {}
        if auth and self.cookie:
            headers["Cookie"] = self.cookie
        status, hdrs, payload = request(base + path, method, body, headers)
        set_cookie = hdrs.get("Set-Cookie") or hdrs.get("set-cookie") or ""
        if "ops_session=" in set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        return status, payload


def container_env(name):
    raw = subprocess.check_output(["docker", "inspect", name], text=True)
    info = json.loads(raw)[0]
    env = {}
    for item in info["Config"].get("Env") or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    return env


def creates(log_path):
    n = 0
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "purchase/create" in line:
                n += 1
    return n


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--shim", default="http://127.0.0.1:13360")
    p.add_argument("--public", default="http://127.0.0.1:13361")
    p.add_argument("--token", default="")
    p.add_argument("--from-container", default="")
    p.add_argument("--mock-log", default="")
    p.add_argument("--sku", type=int, default=101)
    args = p.parse_args()
    if args.from_container:
        env = container_env(args.from_container)
        from urllib.parse import urlparse
        host = (urlparse(env.get("OPEN16688_BASE_URL", "")).hostname or "").lower().rstrip(".")
        official = host == "16688.com.cn" or host.endswith(".16688.com.cn")
        if official or host != "mock16688":
            sys.exit("停止：上游主机不是 mock16688")
        if env.get("SHIM_AUTHORIZE_FORMAL_PURCHASE", "").lower() in {"1", "true", "yes"}:
            sys.exit("停止：正式采购开关开着")
        args.token = env.get("SHIM_ADMIN_TOKEN", "")
    if len(args.token) < 32:
        sys.exit("停止：没有管理令牌")

    c = Client(args.shim, args.public)
    with urllib.request.urlopen(c.shim + "/ops", timeout=20) as wall:
        wall_body = wall.read().decode("utf-8", "replace")
        wall_status = wall.status
    check("login wall", wall_status == 200 and "管理令牌" in wall_body and "货源池" not in wall_body)
    status, _ = c.call(c.shim, "/ops/api/overview")
    check("overview locked", status == 401)
    status, body = c.call(c.shim, "/ops/login", "POST", {"token": "x" * 40})
    check("bad token", status == 401 and not c.cookie)
    status, body = c.call(c.shim, "/ops/login", "POST", {"token": args.token})
    check("login", status == 200 and body.get("ok") is True and c.cookie.startswith("ops_session="))

    status, overview = c.call(c.shim, "/ops/api/overview", auth=True)
    mode = overview.get("purchase_mode")
    check("purchase mode", status == 200 and mode == "mock" and overview.get("formal_authorized") is False)
    status, channel = c.call(c.shim, "/ops/api/channel/16688", auth=True)
    check("liandong unconfigured", status == 200 and channel.get("liandong", {}).get("configured") is False
          and channel.get("manual", {}).get("pays_automatically") is False)
    pub = urllib.request.urlopen(c.public + "/", timeout=20).read().decode("utf-8", "replace")
    check("redeem page", "卡密兑换" in pub and "管理令牌" not in pub and "/ops" not in pub)

    before = creates(args.mock_log) if args.mock_log else None
    status, made = c.call(c.shim, "/ops/api/codes/generate", "POST", {"sku_id": args.sku, "n": 1}, auth=True)
    code = (made.get("codes") or [""])[0]
    check("issued one code", status == 200 and len(made.get("codes") or []) == 1 and code.startswith("GG-"))
    status, redeemed = c.call(c.public, "/api/redeem", "POST", {"code": code})
    check("redeem waits", status == 200 and redeemed.get("state") == "processing" and "payload" not in redeemed)
    status, props = c.call(c.shim, "/ops/api/proposals", auth=True)
    opens = [x for x in props.get("proposals") or [] if x.get("status") == "open" and x.get("type") == "purchase"]
    mine = [x for x in opens if x.get("sku_id") == args.sku]
    check("one proposal", len(mine) == 1 and mine[0].get("simulated") is True)
    rec = (mine[0].get("recommended") or {})
    check("cheapest in-stock source", rec.get("goods_no") == "G24324524" and rec.get("total") == 110.51)
    notes = {s.get("goods_no"): s.get("note") for s in mine[0].get("sources") or []}
    check("out of stock skipped", notes.get("G74884541") == "缺货")
    if before is not None:
        check("no create before approval", creates(args.mock_log) == before)

    status, _ = c.call(c.shim, f"/ops/api/proposals/{mine[0]['id']}/approve", "POST", {}, auth=True)
    check("approve", status == 200)
    status, again = c.call(c.shim, f"/ops/api/proposals/{mine[0]['id']}/approve", "POST", {}, auth=True)
    check("second approve", status == 409)
    status, seen = c.call(c.public, "/api/redeem", "POST", {"code": code})
    payload = seen.get("payload") or ""
    check("simulated delivery", seen.get("state") == "delivered" and "【模拟数据】" in payload and "MOCK-" in payload)
    check("payload not leaked here", True)
    if before is not None:
        check("one mock create", creates(args.mock_log) == before + 1)
    status, repeat = c.call(c.public, "/api/redeem", "POST", {"code": code})
    check("same code same result", repeat.get("state") == "delivered" and repeat.get("payload") == payload)
    if before is not None:
        check("repeat does not create", creates(args.mock_log) == before + 1)

    status, other = c.call(c.shim, "/ops/api/codes/generate", "POST", {"sku_id": args.sku, "n": 1}, auth=True)
    other_code = (other.get("codes") or [""])[0]
    c.call(c.public, "/api/redeem", "POST", {"code": other_code})
    status, props = c.call(c.shim, "/ops/api/proposals", auth=True)
    nxt = [x for x in props.get("proposals") or [] if x.get("status") == "open" and x.get("type") == "purchase"]
    check("second proposal", len(nxt) == 1)
    mid = creates(args.mock_log) if args.mock_log else None
    status, _ = c.call(c.shim, f"/ops/api/proposals/{nxt[0]['id']}/reject", "POST", {}, auth=True)
    check("reject", status == 200)
    status, rejected = c.call(c.public, f"/api/redeem/{urllib.parse.quote(other_code)}")
    check("rejected code", rejected.get("state") == "voided")
    if mid is not None:
        check("reject does not create", creates(args.mock_log) == mid)
    print("accept-mock: ok")


def check(name, ok):
    print(f"{'ok' if ok else 'FAIL'}  {name}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    import urllib.parse
    main()
