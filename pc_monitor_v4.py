"""
Self-hosted PC monitor + remote control (v2).
Install:  pip install psutil dxcam pillow pydirectinput pyautogui numpy
GPU:      NVIDIA only, via nvidia-smi (ships with the driver)
Run:      python pc_monitor_v2.py
Open:     http://<WIREGUARD_IP>:8765/?token=YOUR_TOKEN
"""
import hmac, io, json, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import psutil, dxcam, pyautogui, pydirectinput
from PIL import Image

# ---- CONFIG ----
BIND_IP = "10.0.0.1"   # your WireGuard interface IP (NOT 0.0.0.0)
PORT = 8765
TOKEN = "change-me-to-a-long-random-string"
GAME_PROCESS = "game.exe"
# ----------------

pyautogui.FAILSAFE = False
pydirectinput.FAILSAFE = False
pydirectinput.PAUSE = 0.02
cam = dxcam.create(output_color="RGB")
cam_lock = threading.Lock()
last_frame = [None]

def gpu():
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"], text=True, timeout=3).strip().splitlines()[0]
        n, u, mu, mt, t, pw = [x.strip() for x in out.split(",")]
        return {"name": n, "util": float(u), "vram_used_mb": float(mu),
                "vram_total_mb": float(mt), "temp_c": float(t), "power_w": pw}
    except Exception:
        return None

def stats():
    procs = [p.info for p in psutil.process_iter(["name", "cpu_percent", "memory_percent"])]
    top = sorted(procs, key=lambda x: x["cpu_percent"] or 0, reverse=True)[:5]
    freq = psutil.cpu_freq()
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "cpu_mhz": round(freq.current) if freq else None,
        "ram_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent,
        "gpu": gpu(),
        "game_running": any((p["name"] or "").lower() == GAME_PROCESS.lower() for p in procs),
        "top_processes": top,
    }

def screenshot(quality=60, width=1280):
    with cam_lock:
        f = cam.grab()          # None if the screen hasn't changed
        if f is not None:
            last_frame[0] = f
        f = last_frame[0]
    if f is None:
        return b""
    img = Image.fromarray(f)
    img.thumbnail((width, width))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()

def do_input(d):
    t = d.get("type")
    W, H = pydirectinput.size()
    if t in ("click", "move", "double"):
        x, y = int(float(d["x"]) * W), int(float(d["y"]) * H)
        btn = d.get("button", "left")
        pydirectinput.moveTo(x, y)
        time.sleep(0.03)
        if t == "move":
            pass
        else:
            for _ in range(2 if t == "double" else 1):
                pydirectinput.mouseDown(button=btn)
                time.sleep(0.05)      # games often need a held click
                pydirectinput.mouseUp(button=btn)
                time.sleep(0.05)
    elif t == "key":
        keys = str(d["key"]).lower().split("+")
        for k in keys: pydirectinput.keyDown(k)
        time.sleep(0.05)
        for k in reversed(keys): pydirectinput.keyUp(k)
    elif t == "type":
        for ch in str(d["text"])[:200]:
            pydirectinput.press(ch if ch != " " else "space")
    elif t == "scroll":
        pyautogui.scroll(int(d["amount"]))
    else:
        return False
    return True

PAGE = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<body style="font-family:sans-serif;background:#111;color:#eee;max-width:1000px;margin:auto;padding:10px">
<h3 id=g>...</h3>
<div id=bars style="font-size:14px;line-height:1.6"></div>
<label><input type=checkbox id=arm> Arm control (clicks/keys go to the PC)</label>
<select id=qual onchange="setQ(this.value)">
  <option value="960,50,10">Low (960px, fast)</option>
  <option value="1280,65,8" selected>Balanced (1280px)</option>
  <option value="1920,80,8">Sharp (1920px)</option>
  <option value="3840,90,5">Max (native, 5fps)</option>
</select>
<div style="position:relative;margin:8px 0">
  <img id=i style="width:100%;display:block;cursor:crosshair">
  <div id=dot style="position:absolute;width:14px;height:14px;border-radius:50%;background:red;display:none;transform:translate(-50%,-50%);pointer-events:none"></div>
</div>
<div>
  <button onclick="k('esc')">Esc</button> <button onclick="k('enter')">Enter</button>
  <button onclick="k('space')">Space</button> <button onclick="k('alt+tab')">Alt+Tab</button>
  <button onclick="k('win')">Win</button> <button onclick="sc(-5)">Scroll ↓</button>
  <button onclick="sc(5)">Scroll ↑</button>
</div>
<div style="margin-top:6px"><input id=txt placeholder="type text"> <button onclick="ty()">Send</button>
 <input id=key placeholder="key e.g. ctrl+shift+esc" size=18> <button onclick="k(key.value)">Press</button></div>
<script>
const T=new URLSearchParams(location.search).get('token');
const $=id=>document.getElementById(id);
async function post(o){
  if(!$('arm').checked){alert('Control not armed');return}
  await fetch('/input?token='+T,{method:'POST',body:JSON.stringify(o)});
}
const k=key=>post({type:'key',key}), sc=amount=>post({type:'scroll',amount});
const ty=()=>{post({type:'type',text:$('txt').value});$('txt').value=''};
function pos(e){const r=$('i').getBoundingClientRect();
  return {x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height,px:e.clientX-r.left,py:e.clientY-r.top}}
function mark(p){const d=$('dot');d.style.left=p.px+'px';d.style.top=p.py+'px';d.style.display='block';setTimeout(()=>d.style.display='none',700)}
$('i').onclick=e=>{const p=pos(e);mark(p);post({type:'click',x:p.x,y:p.y})};
$('i').ondblclick=e=>{const p=pos(e);post({type:'double',x:p.x,y:p.y})};
$('i').oncontextmenu=e=>{e.preventDefault();const p=pos(e);mark(p);post({type:'click',x:p.x,y:p.y,button:'right'})};
function bar(n,v){return n+': '+v+'%<div style="background:#333;height:6px"><div style="background:'+(v>90?'#e55':'#4c8')+';height:6px;width:'+v+'%"></div></div>'}
async function tick(){
  try{
    const d=await (await fetch('/stats?token='+T)).json();
    $('g').textContent=(d.game_running?'🟢 Game running':'🔴 GAME NOT RUNNING')+' · up '+d.uptime_h+'h';
    let h=bar('CPU',d.cpu_percent)+bar('RAM',d.ram_percent);
    if(d.gpu){const vp=Math.round(100*d.gpu.vram_used_mb/d.gpu.vram_total_mb);
      h+=bar('GPU',d.gpu.util)+bar('VRAM',vp)+'GPU temp: '+d.gpu.temp_c+'°C · '+d.gpu.power_w+' W';}
    $('bars').innerHTML=h;
  }catch(e){$('g').textContent='⚠️ PC unreachable'}
}
function setQ(v){const [w,q,f]=v.split(',');$('i').src='';
  $('i').src='/stream.mjpg?token='+T+'&w='+w+'&q='+q+'&fps='+f+'&_='+Date.now()}
tick();setInterval(tick,5000);setQ($("qual").value);
</script>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
    def authed(self):
        tok = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        if hmac.compare_digest(tok, TOKEN):
            return True
        time.sleep(1)  # slow down guessing
        self.send(403, b"forbidden")
        return False
    def stream(self, qs):
        w = max(320, min(3840, int(qs.get("w", ["1280"])[0])))
        q = max(20, min(95, int(qs.get("q", ["65"])[0])))
        fps = max(1, min(15, int(qs.get("fps", ["8"])[0])))
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        prev, last_sent = b"", 0
        try:
            while True:
                t0 = time.time()
                jpg = screenshot(q, w)
                # skip identical frames, but send at least one per second
                if jpg and (jpg != prev or t0 - last_sent > 1):
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
                    prev, last_sent = jpg, t0
                time.sleep(max(0.0, 1 / fps - (time.time() - t0)))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):
        if not self.authed(): return
        u = urlparse(self.path)
        p = u.path
        if p == "/stream.mjpg":
            return self.stream(parse_qs(u.query))
        if p == "/stats":
            self.send(200, json.dumps(stats()).encode(), "application/json")
        elif p == "/screen.jpg":
            self.send(200, screenshot(), "image/jpeg")
        else:
            self.send(200, PAGE.encode(), "text/html")
    def do_POST(self):
        if not self.authed(): return
        if urlparse(self.path).path != "/input":
            return self.send(404, b"no")
        try:
            n = int(self.headers.get("Content-Length", 0))
            ok = do_input(json.loads(self.rfile.read(min(n, 4096))))
            self.send(200 if ok else 400, b"ok" if ok else b"bad")
        except Exception as e:
            self.send(500, str(e).encode())

if __name__ == "__main__":
    print(f"Serving on {BIND_IP}:{PORT}")
    ThreadingHTTPServer((BIND_IP, PORT), H).serve_forever()
