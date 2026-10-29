#!/usr/bin/env python3
"""Local page to check and correct click labels: `uv run scripts/review.py`, then open http://127.0.0.1:8765.

Reads labels/auto.jsonl, writes every checked take to labels/reviewed.jsonl (same fields, plus reviewed_at;
the last line per id wins), so it can be stopped and resumed. Audio is served from data/wake-words/data.
Page: click the waveform to move the playhead; drag across it to mark a click (once per click); M marks a
20 ms click at the playhead; drag a mark's edge to adjust it; click a mark then Delete/Backspace to remove it.
Space play / stop from the playhead, R from start, 1 / 2 / 3 speed 100 / 50 / 25 %, Enter accept and next,
Left / Right previous / next take. Marks are numbered on the waveform and listed below it.
Listening aids: playback normalised to the take's peak, +12 / +24 dB boosts behind a limiter, F plays only >1.5 kHz,
and a red trace of >1.5 kHz energy. If labels/pred-<run>.jsonl exists (scripts/predict_all.py), the newest is shown
as cyan bars (U adopts them) and the page lists only disagreeing takes, test split first (toggle on the page).
"""
import json, sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from keyclick.features import merge_events  # noqa: E402

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
    rows = [done.get(t["id"], t) for t in auto]
    for r in rows:
        r["clicks"] = merge_events(r["clicks"])
    preds = sorted((ROOT / "labels").glob("pred-*.jsonl"), key=lambda f: f.stat().st_mtime)
    if preds:
        p = {json.loads(l)["id"]: json.loads(l) for l in preds[-1].read_text().splitlines() if l.strip()}
        for r in rows:
            if r["id"] in p:
                r.update(pred=p[r["id"]]["pred"], disagree=p[r["id"]]["disagree"], pred_run=preds[-1].stem[5:])
    return rows


PAGE = r"""<!doctype html><meta charset=utf-8><title>Keyclick review</title>
<style>body{font:14px system-ui;margin:16px;background:#111;color:#ddd}canvas{width:100%;height:260px;background:#000;cursor:text}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:8px 0}.ok{color:#7c7}.flag{color:#fb5}button{font:inherit}
#marks li{margin:2px 0}#marks li.sel{color:#f77;font-weight:bold}kbd{background:#333;border-radius:3px;padding:0 4px}</style>
<div class=bar><b id=pos></b><span id=meta></span><span id=state></span></div>
<canvas id=c width=1800 height=260></canvas>
<div class=bar><button onclick=go(-1)>&larr; prev</button><button onclick=toggle()>&#9654; play / stop</button>
<button onclick=play(0)>&#8635; from start</button>
<span>speed <button data-r=1 style=font-weight:bold onclick=speed(1)>100%</button><button data-r=.5 onclick=speed(.5)>50%</button><button data-r=.25 onclick=speed(.25)>25%</button></span>
<span>volume <button data-g=0 style=font-weight:bold onclick=vol(0)>normalised</button><button data-g=12 onclick=vol(12)>+12 dB</button><button data-g=24 onclick=vol(24)>+24 dB</button></span>
<label><input type=checkbox id=hp onchange=route()> click filter (only &gt;1.5 kHz, F)</label>
<label><input type=checkbox id=dis checked onchange=refilter()> only takes where model and labels disagree</label></div><div class=bar>
<button onclick=markHere()>+ mark click at line (M)</button><button onclick=useModel()>use model marks (U)</button><button onclick=clearAll()>clear all marks</button>
<button onclick=accept()><b>accept &amp; next (Enter)</b></button><button onclick=nextTodo()>next unreviewed</button></div>
<div style="display:flex;gap:40px"><div><b id=count></b><ol id=marks></ol></div>
<div style="color:#999;line-height:1.7">
<b>Click</b> the waveform: move the white line there (and keep playing if playing).<br>
<b>Drag</b> across the waveform: mark that stretch as a click. Do it once per click.<br>
<kbd>M</kbd>: mark a 20 ms click at the white line. <b>Drag a red edge</b> to adjust a mark.<br>
<b>Click a red mark</b> to select it; <kbd>Delete</kbd> / <kbd>Backspace</kbd> removes it.<br>
<kbd>Space</kbd> play / stop from the line &middot; <kbd>R</kbd> from start &middot; <kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd> speed 100 / 50 / 25 % &middot; <kbd>&larr;</kbd> <kbd>&rarr;</kbd> previous / next take<br>
Red: marked clicks (numbered). Cyan bars along the top: the model's clicks. Thin red line: energy above 1.5 kHz (clicks spike).<br>
Blue band: detected speech. Yellow ticks: logged key events. <kbd>F</kbd> click filter &middot; <kbd>U</kbd> use model marks.</div>
<div><b>Model marks</b> <span id=run></span><ol id=pmarks></ol></div></div>
<script>
let T=[],i=0,buf=null,ac=new AudioContext(),sel=-1,drag=null,src=null,t0=0,off=0,head=0,rate=1,wave=null,trace=null,gdb=0,peak=1,order=[],oi=0;
const c=document.getElementById('c'),g=c.getContext('2d'),W=c.width,H=c.height;
fetch('/takes').then(r=>r.json()).then(d=>{T=d;refilter()});
function refilter(){const only=document.getElementById('dis').checked&&T.some(t=>'disagree' in t);
 order=T.map((t,k)=>k).filter(k=>!only||T[k].disagree);order.sort((a,b)=>(T[a].split=='test'?0:1)-(T[b].split=='test'?0:1)||a-b);
 oi=Math.max(0,order.findIndex(k=>!T[k].reviewed_at));if(order.length){i=order[oi];load()}}
const gain=ac.createGain(),hpf=ac.createBiquadFilter(),lim=ac.createDynamicsCompressor();
hpf.type='highpass';hpf.frequency.value=1500;lim.threshold.value=-3;lim.knee.value=0;lim.ratio.value=20;lim.attack.value=.001;lim.release.value=.05;
lim.connect(ac.destination);function route(){gain.disconnect();hpf.disconnect();
 if(document.getElementById('hp').checked){gain.connect(hpf);hpf.connect(lim)}else gain.connect(lim)}route();
function vol(db){gdb=db;document.querySelectorAll('[data-g]').forEach(b=>b.style.fontWeight=+b.dataset.g==db?'bold':'');setGain()}
function setGain(){gain.gain.value=.9/peak*10**(gdb/20)}
async function load(){stop();head=0;sel=-1;const t=T[i];document.getElementById('pos').textContent=`${oi+1} / ${order.length}${order.length<T.length?' (of '+T.length+')':''} · ${t.split}`;
 document.getElementById('meta').textContent=`${t.id} · ${t.duration_s}s · ${t.flags.join(' ')}`;
 document.getElementById('state').innerHTML=t.reviewed_at?'<span class=ok>reviewed</span>':'<span class=flag>not reviewed</span>';
 buf=await ac.decodeAudioData(await (await fetch('/audio/'+t.file)).arrayBuffer());wave=null;trace=null;
 peak=1e-6;for(const v of buf.getChannelData(0))peak=Math.max(peak,Math.abs(v));setGain();draw();trace=await hpTrace();draw()}
async function hpTrace(){const o=new OfflineAudioContext(1,buf.length,buf.sampleRate),s=o.createBufferSource(),f=o.createBiquadFilter();
 f.type='highpass';f.frequency.value=1500;s.buffer=buf;s.connect(f);f.connect(o.destination);s.start();
 const d=(await o.startRendering()).getChannelData(0),step=d.length/W,e=new Float32Array(W);
 for(let x=0;x<W;x++){let a=0,n=0;for(let k=Math.floor(x*step);k<(x+1)*step;k++){a+=d[k]*d[k];n++}e[x]=10*Math.log10(a/Math.max(1,n)+1e-12)}
 const srt=[...e].sort((a,b)=>a-b),lo=srt[Math.floor(W*.1)],hi=srt[W-1];return e.map(v=>Math.max(0,(v-lo)/Math.max(1,hi-lo)))}
const X=s=>s/T[i].duration_s*W, S=x=>Math.min(T[i].duration_s,Math.max(0,x/W*T[i].duration_s)), ex=e=>e.offsetX*W/c.clientWidth;
function waveform(){const o=document.createElement('canvas');o.width=W;o.height=H;const q=o.getContext('2d'),d=buf.getChannelData(0),step=d.length/W;
 let m=0;for(const v of d)m=Math.max(m,Math.abs(v));q.strokeStyle='#ccc';q.beginPath();
 for(let x=0;x<W;x++){let lo=0,hi=0;for(let k=Math.floor(x*step);k<(x+1)*step;k++){lo=Math.min(lo,d[k]);hi=Math.max(hi,d[k])}
  q.moveTo(x,H/2-hi/m*H/2);q.lineTo(x,H/2-lo/m*H/2)}q.stroke();return o}
function draw(){const t=T[i];g.clearRect(0,0,W,H);
 if(t.speech_s){g.fillStyle='#123a5a';g.fillRect(X(t.speech_s[0]),0,X(t.speech_s[1])-X(t.speech_s[0]),H)}
 g.drawImage(wave=wave||waveform(),0,0);
 t.clicks.forEach((k,j)=>{const x0=X(k[0]),w=Math.max(3,X(k[1])-x0);g.fillStyle=j==sel?'rgba(255,80,80,.65)':'rgba(220,40,40,.45)';g.fillRect(x0,0,w,H);
  g.fillStyle=j==sel?'#f77':'#e44';g.fillRect(x0,0,w,4);g.fillRect(x0,H-4,w,4);g.font='bold 22px system-ui';g.fillText(j+1,x0+w+3,24)});
 if(trace){g.strokeStyle='#f44';g.lineWidth=1.5;g.beginPath();trace.forEach((v,x)=>x?g.lineTo(x,H-v*(H-30)):g.moveTo(x,H-v*(H-30)));g.stroke();g.lineWidth=1}
 g.fillStyle='#2dd';(t.pred||[]).forEach(k=>g.fillRect(X(k[0]),0,Math.max(3,X(k[1])-X(k[0])),10));
 g.fillStyle='#fd3';t.key_events_s.forEach(e=>g.fillRect(X(e)-1,H-14,3,14));
 g.fillStyle='#fff';g.fillRect(X(head)-1,0,2,H);list()}
function list(){const t=T[i];document.getElementById('run').textContent=t.pred_run?`(${t.pred_run})`:'(none)';
 document.getElementById('pmarks').innerHTML=(t.pred||[]).map((k,j)=>`<li>${k[0].toFixed(3)}–${k[1].toFixed(3)} s
  <button onclick="play(${k[0]}-.15)">&#9654;</button> <button onclick="add(${k[0]},${k[1]})">+ add as click</button></li>`).join('');document.getElementById('count').textContent=`${t.clicks.length} click${t.clicks.length==1?'':'s'} marked`;
 document.getElementById('marks').innerHTML=t.clicks.map((k,j)=>`<li class=${j==sel?'sel':''}>${k[0].toFixed(3)}–${k[1].toFixed(3)} s
  <button onclick="sel=${j};play(${k[0]}-.15)">&#9654;</button> <button onclick="del(${j})">&times;</button></li>`).join('')}
function play(from){stop();ac.resume();from=Math.max(0,Math.min(from,T[i].duration_s-.01));src=ac.createBufferSource();src.buffer=buf;src.playbackRate.value=rate;
 src.connect(gain);src.start(0,from);t0=ac.currentTime;off=from;head=from;const me=src;src.onended=()=>{if(src===me){src=null;draw()}};tick()}
function stop(){if(src){const s=src;src=null;s.stop()}}
function toggle(){src?stop():play(head>=T[i].duration_s-.02?0:head)}
function speed(r){rate=r;document.querySelectorAll('[data-r]').forEach(b=>b.style.fontWeight=+b.dataset.r==r?'bold':'');if(src)play(head)}
function tick(){if(!src)return;head=Math.min(T[i].duration_s,off+(ac.currentTime-t0)*rate);draw();requestAnimationFrame(tick)}
function add(a,b){const t=T[i];t.clicks.push([+a.toFixed(3),+b.toFixed(3)]);t.clicks.sort((p,q)=>p[0]-q[0]);sel=t.clicks.findIndex(k=>k[0]==+a.toFixed(3));draw()}
function markHere(){add(head,Math.min(T[i].duration_s,head+.02))}
function del(j){T[i].clicks.splice(j,1);sel=-1;draw()}
function useModel(){T[i].clicks=(T[i].pred||[]).map(k=>[...k]);sel=-1;draw()}
function clearAll(){T[i].clicks=[];sel=-1;draw()}
function edge(x){const k=T[i].clicks;for(let j=0;j<k.length;j++)for(const e of [0,1])if(Math.abs(x-X(k[j][e]))<6)return [j,e];return null}
function inside(x){return T[i].clicks.findIndex(k=>x>=X(k[0])&&x<=X(k[1]))}
c.onmousemove=e=>{const x=ex(e);if(!drag){c.style.cursor=edge(x)?'ew-resize':inside(x)>=0?'pointer':'text';return}
 if(!e.buttons)return;drag.moved=drag.moved||Math.abs(x-drag.x)>4;if(!drag.moved)return;
 if(drag.edge){const k=T[i].clicks[drag.edge[0]];k[drag.edge[1]]=+S(x).toFixed(3);if(k[0]>k[1]){k.reverse();drag.edge[1]^=1}draw()}
 else{draw();g.fillStyle='rgba(255,200,0,.35)';g.fillRect(Math.min(drag.x,x),0,Math.abs(x-drag.x),H)}};
c.onmousedown=e=>{const x=ex(e);drag={x,edge:edge(x),moved:false};if(drag.edge){sel=drag.edge[0];draw()}};
c.onmouseup=e=>{if(!drag)return;const x=ex(e),d=drag;drag=null;
 if(d.edge&&d.moved)return;
 if(d.moved){add(S(Math.min(d.x,x)),S(Math.max(d.x,x)));return}
 const j=inside(x);sel=j;head=S(x);if(src)play(head);else draw()};
function save(){T[i].reviewed_at=new Date().toISOString();return fetch('/save',{method:'POST',body:JSON.stringify(T[i])})}
async function accept(){await save();go(1)}
function go(n){oi=Math.min(order.length-1,Math.max(0,oi+n));i=order[oi];load()}
function nextTodo(){const j=order.findIndex((k,q)=>q>oi&&!T[k].reviewed_at);if(j>=0){oi=j;i=order[oi];load()}}
document.onkeydown=e=>{const k=e.key;if(e.target.tagName=='BUTTON'&&(k==' '||k=='Enter'))e.preventDefault();
 if(k==' ')toggle();else if(k=='r'||k=='R')play(0);else if(k=='m'||k=='M')markHere();else if(k=='u'||k=='U')useModel();
 else if(k=='f'||k=='F'){const h=document.getElementById('hp');h.checked=!h.checked;route()}else if(k=='1')speed(1);else if(k=='2')speed(.5);
 else if(k=='3')speed(.25);else if(k=='Enter')accept();else if(k=='ArrowLeft')go(-1);else if(k=='ArrowRight')go(1);
 else if((k=='Backspace'||k=='Delete')&&sel>=0){e.preventDefault();del(sel)}};
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
        for k in ("pred", "disagree", "pred_run"):
            r.pop(k, None)
        r["clicks"] = sorted([round(a, 3), round(b, 3)] for a, b in r["clicks"])
        r["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with REVIEWED.open("a") as f:
            f.write(json.dumps(r) + "\n")
        self.send(b"{}", "application/json")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
