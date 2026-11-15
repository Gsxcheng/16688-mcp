"""The buyer-facing redeem page. Runs on its own port: nothing but redemption is reachable from here."""
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route


def build_public_app(core) -> Starlette:
    hits: dict[str, list[float]] = {}

    def limited(request: Request) -> bool:
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
        now = time.time()
        recent = [t for t in hits.get(ip, []) if now - t < 60]
        recent.append(now)
        hits[ip] = recent
        if len(hits) > 5000:
            hits.clear()
        return len(recent) > core.cfg.redeem_per_minute

    async def page(request: Request):
        return HTMLResponse(REDEEM_HTML, headers={"Cache-Control": "no-store"})

    async def redeem(request: Request):
        if limited(request):
            return JSONResponse({"state": "rate_limited", "message": "操作太频繁，请一分钟后再试。"}, status_code=429)
        try:
            req = await request.json()
            code, confirm = str(req.get("code", "")), bool(req.get("confirm"))
        except (ValueError, AttributeError):
            return JSONResponse({"state": "not_found", "message": "请求格式不对。"}, status_code=400)
        return JSONResponse(core.redeemer.redeem(code, confirm), headers={"Cache-Control": "no-store"})

    async def status(request: Request):
        if limited(request):
            return JSONResponse({"state": "rate_limited", "message": "操作太频繁，请一分钟后再试。"}, status_code=429)
        return JSONResponse(core.redeemer.lookup(request.path_params["code"]), headers={"Cache-Control": "no-store"})

    async def health(request: Request):
        return JSONResponse({"ok": True})

    routes = [Route("/health", health)]
    for base in ("", "/redeem"):          # served at the root, or under /redeem/ behind the shop's domain
        routes += [Route(base + "/", page), Route(base + "/api/redeem", redeem, methods=["POST"]),
                   Route(base + "/api/redeem/{code:str}", status)]

    async def slash(request: Request):   # the page uses relative API paths, so it needs the trailing slash
        return RedirectResponse("/redeem/", status_code=308)

    routes.append(Route("/redeem", slash))
    return Starlette(routes=routes)


REDEEM_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>卡密兑换</title><style>
:root{--bg:#f3f5f4;--surface:#fff;--sunk:#e9edeb;--line:#d5dbd8;--fg:#17231f;--muted:#5b6964;--accent:#0c6f5e;--on:#fff;
--ok:#dff1e3;--warn:#fbeed3;--bad:#fbe1de}
@media (prefers-color-scheme:dark){:root{--bg:#111816;--surface:#1a2320;--sunk:#232e2a;--line:#34413c;--fg:#e6edea;
--muted:#9aa9a3;--accent:#4fc3a9;--on:#06211b;--ok:#1c3324;--warn:#3a2e15;--bad:#40201d;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 "PingFang SC","Microsoft YaHei",system-ui,sans-serif;padding:24px 16px}
main{max-width:440px;margin:0 auto;background:var(--surface);border:1px solid var(--line);border-radius:16px;
padding:22px 18px;display:flex;flex-direction:column;gap:14px}h1{font-size:20px;margin:0}p{margin:0}
.m{color:var(--muted);font-size:13px}input{width:100%;font:15px ui-monospace,Menlo,Consolas,monospace;padding:11px 12px;
border-radius:9px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
button{font:inherit;cursor:pointer;border-radius:9px;border:1px solid var(--line);background:var(--surface);
color:var(--fg);padding:10px 14px}button.p{background:var(--accent);color:var(--on);border-color:var(--accent);font-weight:600}
button:disabled{opacity:.5}.r{border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:8px}
.ok{background:var(--ok)}.warn{background:var(--warn)}.bad{background:var(--bad)}
pre{margin:0;white-space:pre-wrap;word-break:break-all;font:14px ui-monospace,Menlo,Consolas,monospace;
background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:10px}.row{display:flex;gap:8px;flex-wrap:wrap}
</style></head><body><main><h1>卡密兑换</h1>
<p class="m">输入你收到的卡密。兑换成功后，同一个卡密可以随时回来再看结果。</p>
<label class="m" for="c">卡密</label><input id="c" autocomplete="off" spellcheck="false" placeholder="GG-XXXXX-XXXXX-XXXXX-XXXXX">
<button class="p" id="go">兑换</button><div id="out"></div></main><script>
const $=s=>document.querySelector(s),out=$('#out'),inp=$('#c'),go=$('#go');let timer=null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function show(d){clearTimeout(timer);
 if(d.state==='delivered'){out.innerHTML='<div class="r ok"><b>兑换成功</b>'+(d.product?'<span class="m">'+esc(d.product)+'</span>':'')+
  '<pre id="pl">'+esc(d.payload)+'</pre><div class="row"><button id="cp">复制</button></div>'+
  (d.instruction?'<p>'+esc(d.instruction).replace(/\\n/g,'<br>')+'</p>':'')+'</div>';
  $('#cp').onclick=()=>{navigator.clipboard.writeText(d.payload).then(()=>{$('#cp').textContent='已复制'},()=>{})};}
 else if(d.state==='confirm'){out.innerHTML='<div class="r warn"><b>兑换前请确认</b><p>'+esc(d.message)+
  '</p><div class="row"><button class="p" id="y">现在兑换</button><button id="n">先不兑换</button></div></div>';
  $('#y').onclick=()=>run(true);$('#n').onclick=()=>{out.innerHTML='<p class="m">已取消，卡密仍然有效。</p>'};}
 else if(d.state==='processing'){out.innerHTML='<div class="r warn"><b>处理中</b><p>'+esc(d.message)+'</p></div>';
  timer=setTimeout(poll,8000);}
 else if(d.state==='ready'){out.innerHTML='';}
 else{out.innerHTML='<div class="r bad"><p>'+esc(d.message||'出错了，请稍后再试。')+'</p></div>';}}
async function run(confirm){go.disabled=true;out.innerHTML='<p class="m">正在为你准备，请稍等…</p>';
 try{const r=await fetch('api/redeem',{method:'POST',headers:{'content-type':'application/json'},
  body:JSON.stringify({code:inp.value,confirm:!!confirm})});show(await r.json());}
 catch(e){out.innerHTML='<div class="r bad"><p>网络出错，请稍后再试。卡密不会因此失效。</p></div>';}go.disabled=false;}
async function poll(){try{const r=await fetch('api/redeem/'+encodeURIComponent(inp.value.trim()));show(await r.json());}catch(e){timer=setTimeout(poll,8000)}}
go.onclick=()=>run(false);inp.addEventListener('keydown',e=>{if(e.key==='Enter')run(false)});
</script></body></html>"""
