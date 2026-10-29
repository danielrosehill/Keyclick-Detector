#!/usr/bin/env python3
"""Local page to check and correct click labels: `uv run scripts/review.py`, then open http://127.0.0.1:8765.

Reads labels/auto.jsonl, writes every checked take to labels/reviewed.jsonl (same fields, plus reviewed_at;
the last line per id wins), so it can be stopped and resumed. Audio is served from data/wake-words/data.
Page keys: Space play / stop (a white line follows playback), S half / normal speed, Enter accept and next, Left/Right previous/next, Backspace remove the selected click.
Click on the waveform: on a marked click selects it (and plays from 100 ms before), elsewhere adds a 20 ms
click there. Drag a click's edges to adjust it.
"""
import json, sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTO, REVIEWED, DATA = ROOT / "labels/auto.jsonl", ROOT / "labels/reviewed.jsonl", ROOT / "data/wake-words/data"


def takes():
    auto = [json.loads(l) for l in AUTO.read_text().splitlines() if l.strip()]
    done = {}
    if REVIEWED.exists():
        for l in REVIEWED.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                done[r["id"]] = r
    return [done.get(t["id"], t) for t in auto]


PAGE = r"""<!doctype html><meta charset=utf-8><title>Keyclick review</title>
<style>body{font:14px system-ui;margin:16px;background:#111;color:#ddd}canvas{width:100%;height:260px;background:#000;cursor:crosshair}
#bar{display:flex;gap:16px;align-items:center;margin:8px 0}.ok{color:#7c7}.flag{color:#fb5}button{font:inherit}</style>
<div id=bar><b id=pos></b><span id=meta></span><span id=state></span></div>
<canvas id=c width=1800 height=260></canvas>
<div id=bar><button onclick=go(-1)>&larr; prev</button><button onclick=play(0)>&#9654; play (space)</button>
<button onclick=accept()>accept &amp; next (enter)</button><button onclick=nextTodo()>next unreviewed</button>
<b id=rate>normal speed</b><span>S: half / normal speed &middot; click empty space: add click &middot; click a click: select + play &middot; backspace: delete &middot; drag edges to adjust</span></div>
<p id=help style="color:#888">Red: clicks. Blue band: detected speech. Yellow ticks: logged key events. White line: playback position.</p>
<script>
let T=[],i=0,buf=null,ac=new AudioContext(),sel=-1,drag=null,src=null,t0=0,off=0,head=null,rate=1;const c=document.getElementById('c'),g=c.getContext('2d');
fetch('/takes').then(r=>r.json()).then(d=>{T=d;i=Math.max(0,T.findIndex(t=>!t.reviewed_at));load()});
async function load(){stop();head=null;sel=-1;const t=T[i];document.getElementById('pos').textContent=`${i+1} / ${T.length}`;
 document.getElementById('meta').textContent=`${t.id} · ${t.duration_s}s · ${t.flags.join(' ')}`;
 document.getElementById('state').innerHTML=t.reviewed_at?'<span class=ok>reviewed</span>':'<span class=flag>not reviewed</span>';
 buf=await ac.decodeAudioData(await (await fetch('/audio/'+t.file)).arrayBuffer());draw()}
const X=s=>s/T[i].duration_s*c.width, S=x=>x/c.width*T[i].duration_s;
function draw(){const t=T[i],d=buf.getChannelData(0),W=c.width,H=c.height,step=d.length/W;g.clearRect(0,0,W,H);
 if(t.speech_s){g.fillStyle='#123a5a';g.fillRect(X(t.speech_s[0]),0,X(t.speech_s[1])-X(t.speech_s[0]),H)}
 t.clicks.forEach((k,j)=>{g.fillStyle=j==sel?'#f55':'#a22';g.fillRect(X(k[0]),0,Math.max(2,X(k[1])-X(k[0])),H)});
 let m=0;for(const v of d)m=Math.max(m,Math.abs(v));g.strokeStyle='#ccc';g.beginPath();
 for(let x=0;x<W;x++){let lo=0,hi=0;for(let k=Math.floor(x*step);k<(x+1)*step;k++){lo=Math.min(lo,d[k]);hi=Math.max(hi,d[k])}
  g.moveTo(x,H/2-hi/m*H/2);g.lineTo(x,H/2-lo/m*H/2)}g.stroke();
 g.fillStyle='#fd3';t.key_events_s.forEach(e=>g.fillRect(X(e)-1,0,3,14));
 if(head!==null){g.fillStyle='#fff';g.fillRect(X(head)-1,0,2,H)}}
function play(from){stop();ac.resume();from=Math.max(0,from);src=ac.createBufferSource();src.buffer=buf;src.playbackRate.value=rate;
 src.connect(ac.destination);src.start(0,from);t0=ac.currentTime;off=from;const me=src;src.onended=()=>{if(src===me)src=null};tick()}
function stop(){if(src){const s=src;src=null;s.stop()}}
function tick(){if(!src)return;head=Math.min(T[i].duration_s,off+(ac.currentTime-t0)*rate);draw();requestAnimationFrame(tick)}
function hit(x){return T[i].clicks.findIndex(k=>x>=X(k[0])-4&&x<=X(k[1])+4)}
c.onmousedown=e=>{const x=e.offsetX*c.width/c.clientWidth,j=hit(x);
 if(j<0){const s=S(x);T[i].clicks.push([+s.toFixed(3),+(s+.02).toFixed(3)]);T[i].clicks.sort((a,b)=>a[0]-b[0]);sel=hit(x);draw();return}
 sel=j;const k=T[i].clicks[j];drag=Math.abs(x-X(k[0]))<Math.abs(x-X(k[1]))?[j,0]:[j,1];draw();play(k[0]-.1)};
c.onmousemove=e=>{if(!drag||!e.buttons)return;const s=+S(e.offsetX*c.width/c.clientWidth).toFixed(3),k=T[i].clicks[drag[0]];
 k[drag[1]]=s;if(k[0]>k[1])k.reverse();draw()};c.onmouseup=()=>drag=null;
function save(){T[i].reviewed_at=new Date().toISOString();return fetch('/save',{method:'POST',body:JSON.stringify(T[i])})}
async function accept(){await save();go(1)}
function go(n){i=Math.min(T.length-1,Math.max(0,i+n));load()}
function nextTodo(){const j=T.findIndex((t,k)=>k>i&&!t.reviewed_at);if(j>=0){i=j;load()}}
document.onkeydown=e=>{if(e.key==' '){e.preventDefault();src?stop():play(0)}else if(e.key=='s'){rate=rate==1?.5:1;document.getElementById('rate').textContent=rate==1?'normal speed':'half speed'}else if(e.key=='Enter')accept();else if(e.key=='ArrowLeft')go(-1);
 else if(e.key=='ArrowRight')go(1);else if(e.key=='Backspace'&&sel>=0){T[i].clicks.splice(sel,1);sel=-1;draw()}};
</script>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send(PAGE.encode(), "text/html")
        elif self.path == "/takes":
            self.send(json.dumps(takes()).encode(), "application/json")
        elif self.path.startswith("/audio/"):
            f = (DATA / self.path[7:]).resolve()
            if DATA.resolve() not in f.parents or not f.exists():
                return self.send_error(404)
            self.send(f.read_bytes(), "audio/flac")
        else:
            self.send_error(404)

    def do_POST(self):
        r = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        r["clicks"] = sorted([round(a, 3), round(b, 3)] for a, b in r["clicks"])
        r["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with REVIEWED.open("a") as f:
            f.write(json.dumps(r) + "\n")
        self.send(b"{}", "application/json")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
