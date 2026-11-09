"""Phone setup: a small web server, running only while the Phone Setup screen is open, so the app can be set up
from a phone on the same network. Protected by a random token that is carried in the QR code."""
import hmac
import json
import secrets
import socket
import sys
import threading
import time
import zoneinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import db
from version import __version__
from xtream import XtreamClient

PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Xtream TV</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;font:16px -apple-system,Segoe UI,Roboto,sans-serif;background:#14161f;color:#e6e9f5}
header{padding:18px 18px 8px;font-size:22px;font-weight:600}header small{color:#8b92a8;font-size:13px;margin-left:8px;font-weight:400}
nav{display:flex;gap:6px;padding:0 12px 10px;overflow-x:auto;position:sticky;top:0;background:#14161f;z-index:2}
nav button{flex:none;background:#232840;color:#cfd5ea;border:0;border-radius:20px;padding:9px 16px;font-size:15px}
nav button.on{background:#2d6cdf;color:#fff}
main{padding:6px 16px 90px;max-width:720px;margin:auto}
section{display:none}section.on{display:block}
label{display:block;margin:14px 0 6px;font-size:12px;letter-spacing:.06em;color:#aab1c8;font-weight:600;text-transform:uppercase}
input,select{width:100%;background:#1b1e2b;border:1px solid #2b3047;border-radius:10px;color:#fff;padding:12px;font-size:16px}
button.act{background:#2d6cdf;color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:16px;margin:14px 8px 0 0}
button.sec{background:#232840}
.row{display:flex;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid #232840}
.row .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row small{color:#8b92a8;display:block}.pill{background:#232840;border:0;color:#fff;border-radius:8px;padding:8px 12px;font-size:15px}
.msg{margin-top:12px;font-size:14px;color:#8b92a8}.ok{color:#4cd08a}.bad{color:#ff6b6b}
.chk{display:flex;gap:12px;align-items:center;margin:16px 0}.chk input{width:22px;height:22px}.chk span{flex:1}
pre{background:#0e1017;padding:12px;border-radius:10px;overflow:auto;font-size:12px;white-space:pre-wrap}
</style></head><body>
<header>Xtream TV <small id="ver"></small></header>
<nav id="tabs"><button data-t="account" class="on">Account</button><button data-t="channels">Channels</button>
<button data-t="prefs">Settings</button><button data-t="info">Info</button></nav>
<main>
<section id="s-account" class="on">
<label>Server URL</label><input id="server" autocapitalize="off" autocorrect="off" placeholder="http://provider.example.com:8080">
<label>Username</label><input id="username" autocapitalize="off" autocorrect="off">
<label>Password</label><input id="password" type="password" autocapitalize="off" placeholder="password">
<button class="act sec" onclick="testLogin()">Test connection</button><button class="act" onclick="saveAccount()">Save</button>
<div id="m-account" class="msg"></div></section>
<section id="s-channels"><div id="m-channels" class="msg"></div>
<label>Category</label><select id="cat" onchange="search()"></select>
<label>Search</label><input id="q" placeholder="Search channels" oninput="debounce()">
<button class="act sec" onclick="addCat()">Add the whole category</button>
<div id="found"></div><label>My channels (order = channel number)</label><div id="mine"></div></section>
<section id="s-prefs">
<label>Stream format</label><select id="stream_ext"><option value="m3u8">HLS (.m3u8)</option><option value="ts">MPEG-TS (.ts)</option></select>
<div class="chk"><input type="checkbox" id="confirm_switch"><span>Browse channels first, switch only on confirm</span></div>
<label>Channel info display time (seconds)</label><input id="banner_secs" type="number" min="2" max="30">
<label>Video decoder</label><select id="decode_mode"><option value="auto">Automatic</option><option value="hardware">Hardware preferred</option><option value="software">Software only</option></select>
<div class="chk"><input type="checkbox" id="fourk_mode"><span>4K / high-bitrate mode</span></div>
<label>Quality badge corner</label><select id="corner_pos"><option value="tl">Top left</option><option value="tr">Top right</option><option value="bl">Bottom left</option><option value="br">Bottom right</option></select>
<div class="chk"><input type="checkbox" id="minimalistic"><span>Minimalistic (only 4K / HD / SD)</span></div>
<label>Time zone</label><input id="timezone" list="tzs" placeholder="e.g. Australia/Melbourne (empty = system)"><datalist id="tzs"></datalist>
<div class="chk"><input type="checkbox" id="clock24"><span>24-hour clock</span></div>
<button class="act" onclick="savePrefs()">Save</button><div id="m-prefs" class="msg"></div></section>
<section id="s-info"><button class="act sec" onclick="loadDiag()">Refresh</button><pre id="diag">...</pre></section>
</main>
<script>
const T="%TOKEN%";const $=id=>document.getElementById(id);let S={},mine=[],timer=null,catsLoaded=false;
function el(tag,props,...kids){const e=document.createElement(tag);Object.assign(e,props||{});kids.flat().forEach(k=>e.append(k));return e}
async function api(path,body){const r=await fetch(path,{method:body?"POST":"GET",headers:{"X-Token":T,"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||("Error "+r.status));return j}
function say(id,t,c){const m=$(id);m.textContent=t;m.className="msg "+(c||"")}
document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>{
document.querySelectorAll("#tabs button").forEach(x=>x.classList.toggle("on",x===b));
document.querySelectorAll("section").forEach(s=>s.classList.toggle("on",s.id==="s-"+b.dataset.t));
if(b.dataset.t==="channels")openChannels();if(b.dataset.t==="info")loadDiag()});
const TEXT=["server","username","stream_ext","decode_mode","corner_pos","banner_secs","timezone"],CHK=["confirm_switch","fourk_mode","minimalistic","clock24"];
async function load(){S=await api("/api/state");$("ver").textContent="v"+S.version;const s=S.settings;
TEXT.forEach(k=>$(k).value=s[k]||"");CHK.forEach(k=>$(k).checked=s[k]==="1");
$("password").placeholder=S.has_password?"unchanged (type to replace)":"password"}
async function saveAccount(){try{await api("/api/settings",{server:$("server").value,username:$("username").value,password:$("password").value});
$("password").value="";say("m-account","Saved. Your channel list is loading - open the Channels tab.","ok");await load()}catch(e){say("m-account",e.message,"bad")}}
async function testLogin(){say("m-account","Testing...");try{const r=await api("/api/test_login",{server:$("server").value,username:$("username").value,password:$("password").value});
say("m-account",r.ok?`Connected - ${r.status}, expires ${r.expires}, ${r.connections} connections in use`:r.error,r.ok?"ok":"bad")}catch(e){say("m-account",e.message,"bad")}}
async function savePrefs(){const b={};TEXT.slice(2).forEach(k=>b[k]=$(k).value);CHK.forEach(k=>b[k]=$(k).checked?"1":"0");
try{await api("/api/settings",b);say("m-prefs","Saved","ok")}catch(e){say("m-prefs",e.message,"bad")}}
async function loadDiag(){try{$("diag").textContent=(await api("/api/diag")).text}catch(e){$("diag").textContent=e.message}}
async function openChannels(){await load();if(!S.catalog_ready){say("m-channels","Your channel list is still loading - this page will fill in by itself.");
setTimeout(()=>{if($("s-channels").classList.contains("on"))openChannels()},3000);return}
say("m-channels","");if(!catsLoaded){const cs=await api("/api/categories");const sel=$("cat");sel.append(el("option",{value:"",textContent:"All categories"}));
cs.forEach(c=>sel.append(el("option",{value:c.id,textContent:c.name})));catsLoaded=true}
mine=(await api("/api/my")).channels;drawMine();search()}
function debounce(){clearTimeout(timer);timer=setTimeout(search,300)}
async function search(){const r=await api("/api/channels?cat="+encodeURIComponent($("cat").value)+"&q="+encodeURIComponent($("q").value));const box=$("found");box.replaceChildren();
r.channels.slice(0,150).forEach(c=>{const added=mine.some(m=>m.id===c.id);
box.append(el("div",{className:"row"},el("div",{className:"n"},c.name,el("small",{textContent:c.cat||""})),
el("button",{className:"pill",textContent:added?"Added":"+ Add",disabled:added,onclick:()=>{mine.push({id:c.id,name:c.name});pushMine().then(search)}})))});
if(!r.channels.length)box.append(el("div",{className:"msg",textContent:"No channels found."}))}
async function pushMine(){await api("/api/my",{ids:mine.map(m=>m.id)});drawMine()}
function drawMine(){const box=$("mine");box.replaceChildren();
mine.forEach((m,i)=>box.append(el("div",{className:"row"},el("div",{className:"n"},(i+1)+"  "+m.name+(m.missing?" (missing)":"")),
el("button",{className:"pill",textContent:"\u25B2",onclick:()=>mv(i,-1)}),el("button",{className:"pill",textContent:"\u25BC",onclick:()=>mv(i,1)}),
el("button",{className:"pill",textContent:"\u2715",onclick:()=>{mine.splice(i,1);pushMine().then(search)}}))));
if(!mine.length)box.append(el("div",{className:"msg",textContent:"Nothing selected yet."}))}
function mv(i,d){const j=i+d;if(j<0||j>=mine.length)return;[mine[i],mine[j]]=[mine[j],mine[i]];pushMine()}
async function addCat(){const c=$("cat").value;if(!c){say("m-channels","Pick a category first.","bad");return}
const r=await api("/api/add_category",{cat:c});say("m-channels",`Added ${r.added} channels.`,"ok");mine=(await api("/api/my")).channels;drawMine();search()}
load().then(async()=>{const z=await api("/api/timezones");const dl=$("tzs");z.zones.forEach(n=>dl.append(el("option",{value:n})))}).catch(e=>say("m-account",e.message,"bad"));
</script></body></html>'''

DEFAULTS = {"server": "", "username": "", "stream_ext": "m3u8", "confirm_switch": "1", "banner_secs": "5",
            "corner_pos": "br", "minimalistic": "0", "decode_mode": "auto",
            "fourk_mode": "1" if sys.platform.startswith("linux") else "0", "timezone": "", "clock24": "1"}
CREDS = {"server", "username", "password"}


def _text(limit):
    def f(v):
        v = str(v).strip()
        if len(v) > limit:
            raise ValueError("That value is too long.")
        return v
    return f


def _bool(v):
    return "1" if str(v).strip().lower() in ("1", "true", "yes", "on") else "0"


def _choice(*options):
    def f(v):
        if str(v) not in options:
            raise ValueError("Unexpected value.")
        return str(v)
    return f


def _int(lo, hi):
    def f(v):
        n = int(str(v).strip())
        if not lo <= n <= hi:
            raise ValueError(f"Must be between {lo} and {hi}.")
        return str(n)
    return f


def _tz(v):
    v = str(v).strip()
    if v and v not in zoneinfo.available_timezones():
        raise ValueError("Unknown time zone. Pick one from the list, e.g. Australia/Melbourne.")
    return v


SETTERS = {"server": _text(300), "username": _text(200), "password": _text(200),
           "stream_ext": _choice("m3u8", "ts"), "confirm_switch": _bool, "banner_secs": _int(2, 30),
           "corner_pos": _choice("tl", "tr", "bl", "br"), "minimalistic": _bool,
           "decode_mode": _choice("auto", "hardware", "software"), "fourk_mode": _bool, "timezone": _tz,
           "clock24": _bool}


def lan_ip():
    for target in ("8.8.8.8", "10.255.255.255"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 1))  # UDP: nothing is actually sent
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            continue
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def apply_settings(body, on_change=None):
    """Validate and store a {key: value} dict. Returns the list of keys that changed."""
    clean = {}
    for key, value in body.items():
        if key not in SETTERS:
            raise ValueError(f"Unknown setting: {key}")
        if key == "password" and not str(value).strip():
            continue  # blank = keep the current password
        try:
            clean[key] = SETTERS[key](value)
        except (ValueError, TypeError) as e:
            raise ValueError(f"{key}: {e}")
    changed = [k for k, v in clean.items() if db.get(k, DEFAULTS.get(k, "")) != v]
    for k in changed:
        db.put(k, clean[k])
    kinds = set()
    if any(k in CREDS for k in changed):
        kinds.add("creds")
    if any(k not in CREDS for k in changed):
        kinds.add("prefs")
    if kinds and on_change:
        on_change(kinds)
    return changed


def _state():
    settings = {k: db.get(k, d) for k, d in DEFAULTS.items()}
    return {"version": __version__, "settings": settings, "has_password": bool(db.get("password")),
            "catalog_ready": bool(db.categories()), "channel_count": len(db.my_channels())}


def _mine_json():
    return [{"id": c["stream_id"], "name": c["name"], "missing": bool(c.get("missing"))}
            for c in db.my_channels(include_missing=True)]


class PhoneServer:
    def __init__(self, on_change=None):
        self.token = secrets.token_urlsafe(16)
        self.on_change = on_change
        self.httpd, self.port, self.last_seen, self._timer = None, None, 0.0, None

    @property
    def running(self):
        return self.httpd is not None

    @property
    def connected(self):
        return time.time() - self.last_seen < 30

    def info(self):
        ip = lan_ip()
        return {"url": f"http://{ip}:{self.port}/?t={self.token}", "ip": ip, "port": self.port}

    def start(self, lifetime=1200):
        if self.httpd:
            return self.info()
        handler = _make_handler(self)
        for port in range(8080, 8100):
            try:
                self.httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
                self.port = port
                break
            except OSError:
                continue
        else:
            raise OSError("No free network port for the phone setup page.")
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self._timer = threading.Timer(lifetime, self.stop)  # never leave it running forever
        self._timer.daemon = True
        self._timer.start()
        return self.info()

    def stop(self):
        httpd, self.httpd = self.httpd, None
        if self._timer:
            self._timer.cancel()
        if httpd:
            threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()


def _make_handler(srv):
    class Handler(BaseHTTPRequestHandler):
        server_version = "XtreamTV"

        def log_message(self, *args):
            pass

        def _authorised(self):
            q = parse_qs(urlparse(self.path).query)
            t = self.headers.get("X-Token") or (q.get("t") or [""])[0]
            return hmac.compare_digest(t.encode(), srv.token.encode())

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj))

        def do_GET(self):
            if not self._authorised():
                return self._send(403, "Open this page by scanning the code shown on the TV.", "text/plain")
            srv.last_seen = time.time()
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, PAGE.replace("%TOKEN%", srv.token), "text/html")
                if u.path == "/api/state":
                    return self._json(_state())
                if u.path == "/api/categories":
                    return self._json([{"id": c["id"], "name": c["name"]} for c in db.categories()])
                if u.path == "/api/channels":
                    chosen = {c["stream_id"] for c in db.my_channels(include_missing=True)}
                    rows = db.find_channels((q.get("cat") or [""])[0] or None, (q.get("q") or [""])[0].strip(), limit=300)
                    return self._json({"channels": [{"id": r["stream_id"], "name": r["name"], "cat": r.get("cat_name") or "",
                                                     "selected": r["stream_id"] in chosen} for r in rows]})
                if u.path == "/api/my":
                    return self._json({"channels": _mine_json()})
                if u.path == "/api/timezones":
                    return self._json({"zones": sorted(zoneinfo.available_timezones())})
                if u.path == "/api/diag":
                    import diagnostics
                    return self._json({"text": diagnostics.to_text(diagnostics.collect())})
                self._json({"error": "Not found"}, 404)
            except Exception as e:  # noqa
                self._json({"error": str(e)}, 400)

        def do_POST(self):
            if not self._authorised():
                return self._send(403, "Forbidden", "text/plain")
            srv.last_seen = time.time()
            path = urlparse(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1_000_000:
                    raise ValueError("Request too large.")
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Bad request.")
                if path == "/api/settings":
                    return self._json({"ok": True, "changed": apply_settings(body, srv.on_change)})
                if path == "/api/test_login":
                    c = XtreamClient(str(body.get("server") or db.get("server", "")),
                                     str(body.get("username") or db.get("username", "")),
                                     str(body.get("password") or db.get("password", "")))
                    try:
                        ui = c.account_info().get("user_info", {})
                    except Exception as e:  # noqa
                        return self._json({"ok": False, "error": f"Could not connect: {e}"})
                    finally:
                        c.close()
                    exp = ui.get("exp_date")
                    expires = time.strftime("%d %b %Y", time.localtime(int(exp))) if str(exp or "").isdigit() else "never"
                    return self._json({"ok": True, "status": ui.get("status", "?"), "expires": expires,
                                       "connections": f"{ui.get('active_cons', '?')}/{ui.get('max_connections', '?')}"})
                if path == "/api/my":
                    ids = body.get("ids")
                    if not isinstance(ids, list) or len(ids) > 5000 or not all(isinstance(i, int) for i in ids):
                        raise ValueError("Bad channel list.")
                    db.set_my_channels(list(dict.fromkeys(ids)))
                    if srv.on_change:
                        srv.on_change({"channels"})
                    return self._json({"ok": True})
                if path == "/api/add_category":
                    have = [c["stream_id"] for c in db.my_channels(include_missing=True)]
                    new = [r["stream_id"] for r in db.find_channels(str(body.get("cat") or "") or None, "", limit=2000)
                           if r["stream_id"] not in set(have)]
                    db.set_my_channels(have + new)
                    if srv.on_change:
                        srv.on_change({"channels"})
                    return self._json({"ok": True, "added": len(new)})
                self._json({"error": "Not found"}, 404)
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa
                self._json({"error": f"Something went wrong: {e}"}, 500)

    return Handler
