#!/usr/bin/env python3
"""Re-runnable mock acceptance. Prints statuses only, never tokens or card codes.

The test mock keeps its wallet in memory. After one GPT purchase it can sit
near 51, which cannot fund another 110.51 quote. That must not open a purchase
proposal and must not call purchase/create. A cheaper sku can still be rejected
and then approved. Sku 103 has no sources.

    python3 accept-mock.py --from-container dj-shim

Counts purchase/create on mock16688 unless --mock-log is given.
"""
import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GPT_TOTAL = 110.51
NOTION_TOTAL = 6.34


def _json_or_text(raw: bytes):
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"_text": raw.decode("utf-8", "replace")}


def request(url, method="GET", body=None, headers=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if body is not None and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), _json_or_text(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), _json_or_text(e.read())


class Client:
    def __init__(self, shim, public):
        self.shim, self.public, self.cookie = shim.rstrip("/"), public.rstrip("/"), ""

    def call(self, base, path, method="GET", body=None, auth=False, headers=None):
        hdrs = dict(headers or {})
        if auth and self.cookie:
            hdrs["Cookie"] = self.cookie
        status, got, payload = request(base + path, method, body, hdrs)
        set_cookie = got.get("Set-Cookie") or got.get("set-cookie") or ""
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


def create_goods(log_path=None, container=None):
    """Goods numbers passed to purchase/create. Card text is never returned."""
    if log_path:
        text = open(log_path, encoding="utf-8", errors="replace").read()
    else:
        code = (
            "import json\nfrom pathlib import Path\n"
            "out=[]\n"
            "p=Path('/state/requests.log')\n"
            "if p.exists():\n"
            "  for line in p.read_text(errors='replace').splitlines():\n"
            "    if 'purchase/create' not in line: continue\n"
            "    i=line.find('{')\n"
            "    if i<0: continue\n"
            "    try: body=json.loads(line[i:])\n"
            "    except Exception: continue\n"
            "    g=body.get('goods_no')\n"
            "    if isinstance(g,str): out.append(g)\n"
            "print('\\n'.join(out))\n"
        )
        text = subprocess.check_output(["docker", "exec", container, "python", "-c", code], text=True)
    goods = []
    if log_path:
        for line in text.splitlines():
            if "purchase/create" not in line:
                continue
            i = line.find("{")
            if i < 0:
                continue
            try:
                body = json.loads(line[i:])
            except ValueError:
                continue
            g = body.get("goods_no")
            if isinstance(g, str):
                goods.append(g)
        return goods
    return [g for g in text.splitlines() if g.startswith("G")]


def sign(secret, method, path, body: bytes):
    ts = str(int(time.time()))
    msg = f"{method.upper()}\n{path}\n{ts}\n{hashlib.md5(body).hexdigest()}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return ts, sig


def upstream_order(c, api_key, api_secret, sku, down):
    path = "/api/v1/upstream/orders"
    body = json.dumps({"sku_id": sku, "quantity": 1, "downstream_order_no": down},
                      separators=(",", ":")).encode()
    ts, sig = sign(api_secret, "POST", path, body)
    headers = {"Dujiao-Next-Api-Key": api_key, "Dujiao-Next-Timestamp": ts, "Dujiao-Next-Signature": sig,
               "Content-Type": "application/json"}
    return c.call(c.shim, path, "POST", body, headers=headers)


def proposals(c):
    status, body = c.call(c.shim, "/ops/api/proposals", auth=True)
    check("list proposals", status == 200)
    return body.get("proposals") or []


def issue(c, sku):
    status, made = c.call(c.shim, "/ops/api/codes/generate", "POST", {"sku_id": sku, "n": 1}, auth=True)
    code = (made.get("codes") or [""])[0]
    check(f"issue sku {sku}", status == 200 and code.startswith("GG-"))
    return code


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--shim", default="http://127.0.0.1:13360")
    p.add_argument("--public", default="http://127.0.0.1:13361")
    p.add_argument("--token", default="")
    p.add_argument("--from-container", default="")
    p.add_argument("--mock-log", default="")
    p.add_argument("--mock-container", default="mock16688")
    args = p.parse_args()
    api_key = os.environ.get("ACCEPT_API_KEY", "")
    api_secret = os.environ.get("ACCEPT_API_SECRET", "")
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
        api_key = env.get("SHIM_API_KEY", "")
        api_secret = env.get("SHIM_API_SECRET", "")
    if len(args.token) < 32 or len(api_key) < 8 or len(api_secret) < 32:
        sys.exit("停止：缺少管理令牌或上游签名材料")

    def goods():
        if args.mock_log:
            return create_goods(log_path=args.mock_log)
        return create_goods(container=args.mock_container)

    c = Client(args.shim, args.public)
    with urllib.request.urlopen(c.shim + "/ops", timeout=20) as wall:
        wall_body = wall.read().decode("utf-8", "replace")
        wall_status = wall.status
    check("login wall", wall_status == 200 and "管理令牌" in wall_body and "货源池" not in wall_body)
    status, _ = c.call(c.shim, "/ops/api/overview")
    check("overview locked", status == 401)
    status, _ = c.call(c.shim, "/ops/login", "POST", {"token": "x" * 40})
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

    base = goods()
    wallet = overview.get("wallet")
    check("wallet readable", isinstance(wallet, (int, float)))
    if wallet >= GPT_TOTAL:
        code = issue(c, 101)
        status, redeemed = c.call(c.public, "/api/redeem", "POST", {"code": code})
        check("gpt waits", status == 200 and redeemed.get("state") == "processing" and "payload" not in redeemed)
        mine = [x for x in proposals(c) if x.get("status") == "open" and x.get("type") == "purchase"
                and x.get("sku_id") == 101]
        check("gpt proposal", len(mine) == 1 and mine[0].get("simulated") is True)
        rec = mine[0].get("recommended") or {}
        check("cheapest in-stock source", rec.get("goods_no") == "G24324524" and rec.get("total") == GPT_TOTAL)
        notes = {s.get("goods_no"): s.get("note") for s in mine[0].get("sources") or []}
        check("out of stock skipped", notes.get("G74884541") == "缺货")
        check("no create before approval", goods() == base)
        status, _ = c.call(c.shim, f"/ops/api/proposals/{mine[0]['id']}/approve", "POST", {}, auth=True)
        check("approve gpt", status == 200)
        status, _ = c.call(c.shim, f"/ops/api/proposals/{mine[0]['id']}/approve", "POST", {}, auth=True)
        check("second approve", status == 409)
        _, seen = c.call(c.public, "/api/redeem", "POST", {"code": code})
        payload = seen.get("payload") or ""
        check("simulated delivery", seen.get("state") == "delivered" and "【模拟数据】" in payload and "MOCK-" in payload)
        _, repeat = c.call(c.public, "/api/redeem", "POST", {"code": code})
        check("same code same result", repeat.get("payload") == payload)
        check("gpt created once", goods() == base + ["G24324524"])
        _, overview = c.call(c.shim, "/ops/api/overview", auth=True)
        wallet = overview.get("wallet")
    else:
        print("ok  gpt purchase skipped, wallet below 110.51")

    before = goods()
    other = issue(c, 101)
    _, second = c.call(c.public, "/api/redeem", "POST", {"code": other})
    check("second gpt not delivered", second.get("state") == "processing" and "payload" not in second)
    opened = [x for x in proposals(c) if x.get("status") == "open" and x.get("type") == "purchase"
              and x.get("sku_id") == 101]
    if wallet >= GPT_TOTAL:
        check("second gpt proposal", len(opened) == 1)
        status, _ = c.call(c.shim, f"/ops/api/proposals/{opened[0]['id']}/reject", "POST", {}, auth=True)
        check("reject gpt", status == 200)
        _, rejected = c.call(c.public, "/api/redeem/" + urllib.parse.quote(other))
        check("rejected gpt code", rejected.get("state") == "voided")
        check("reject gpt creates nothing", goods() == before)
    else:
        check("no proposal when wallet is short", opened == [])
        check("short wallet creates nothing", goods() == before)
        status, exc = c.call(c.shim, "/ops/api/exceptions", auth=True)
        rows = [o for o in exc.get("orders") or [] if o.get("status") == "waiting_stock" and o.get("product")]
        check("short wallet is waiting", status == 200 and any("110.51" in (o.get("error") or "") for o in rows))

    stamp = str(int(time.time()))
    before = goods()
    status, first = upstream_order(c, api_key, api_secret, 102, "ACC-REJ-" + stamp)
    check("notion order waits", status == 200 and first.get("ok") is True and first.get("status") == "paid")
    rej = [x for x in proposals(c) if x.get("status") == "open" and x.get("order_no") == first.get("order_no")]
    check("notion reject proposal", len(rej) == 1 and (rej[0].get("recommended") or {}).get("total") == NOTION_TOTAL)
    status, _ = c.call(c.shim, f"/ops/api/proposals/{rej[0]['id']}/reject", "POST", {}, auth=True)
    check("reject notion", status == 200)
    status, _ = c.call(c.shim, f"/ops/api/proposals/{rej[0]['id']}/reject", "POST", {}, auth=True)
    check("reject notion twice", status == 409)
    check("reject notion creates nothing", goods() == before)

    status, nxt = upstream_order(c, api_key, api_secret, 102, "ACC-OK-" + stamp)
    check("second notion order", status == 200 and nxt.get("ok") is True)
    okp = [x for x in proposals(c) if x.get("status") == "open" and x.get("order_no") == nxt.get("order_no")]
    check("notion approve proposal", len(okp) == 1)
    status, _ = c.call(c.shim, f"/ops/api/proposals/{okp[0]['id']}/approve", "POST", {}, auth=True)
    check("approve notion", status == 200)
    status, _ = c.call(c.shim, f"/ops/api/proposals/{okp[0]['id']}/approve", "POST", {}, auth=True)
    check("approve notion twice", status == 409)
    check("notion created once", goods() == before + ["G71485656"])

    before = goods()
    gem = issue(c, 103)
    _, gem_body = c.call(c.public, "/api/redeem", "POST", {"code": gem})
    check("empty source waits", gem_body.get("state") == "processing" and "payload" not in gem_body)
    gem_props = [x for x in proposals(c) if x.get("type") == "purchase" and x.get("sku_id") == 103
                 and x.get("status") == "open"]
    check("empty source has no proposal", gem_props == [])
    check("empty source creates nothing", goods() == before)
    print("accept-mock: ok")


def check(name, ok):
    print(f"{'ok' if ok else 'FAIL'}  {name}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
