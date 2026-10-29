#!/usr/bin/env python3
"""Local page to check and correct click labels: `uv run scripts/review.py`, then open http://127.0.0.1:8765.

Reads labels/auto.jsonl, writes every checked take to labels/reviewed.jsonl (same fields, plus reviewed_at;
append-only, the last line per id wins), so it can be stopped and resumed. Audio is served from data/wake-words/data.

Page (rebuilt 2026-10-29 so each gesture does one thing):
- Green boxes are your clicks (what gets saved); dashed cyan boxes are model detections that overlap none of them.
  Thin cyan bars along the top: every model detection.
- A model box's x dismisses it as not a click (grey; saved as `rejected`, model spans you said are not clicks).
- Add: drag across the waveform, M at the white line, or the + on a model box (A when it is selected).
  Remove: the x on a green box, right-click it, or Delete / Backspace / X when selected. Drawing over a click replaces it.
  Adjust: drag a green edge (nearest within 10 px) or press inside a green box and drag (moves its nearer edge), so a
  drag that starts on a click never draws a new one; [ ] nudge the selected click's start 5 ms, Shift+[ ] its end. Z undoes; "reset to saved" returns to the last save.
- Clicking any box or list row selects it and plays only its bounds (3 ms fades so the cut edges don't click);
  Tab / Shift+Tab step through boxes, P replays, L loops. Clicking empty waveform moves the white line.
- Enter saves and moves on; leaving a take with edits (arrows, prev/next, queue change) also saves it.
- Queue: to do (disagreements not yet reviewed), all disagreements, already reviewed, all takes; test split first.
  Disagreements come from the newest labels/pred-<run>.jsonl (scripts/predict_all.py).
Listening aids: playback normalised to the take's peak, +12 / +24 dB boosts behind a limiter, F plays only >1.5 kHz,
and an orange trace of >1.5 kHz energy. Space play / stop, R from start, 1 / 2 / 3 speed 100 / 50 / 25 %.
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
<style>body{font:14px system-ui;margin:16px;background:#111;color:#ddd}canvas{width:100%;height:260px;background:#000;display:block}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:8px 0}.ok{color:#7c7}.flag{color:#fb5}.dirty{color:#f90;font-weight:bold}
button,select{font:inherit}.grp{display:flex;gap:2px;align-items:center}kbd{background:#333;border-radius:3px;padding:0 4px}
table{border-collapse:collapse}td{padding:3px 8px;white-space:nowrap}tr.c{cursor:pointer}tr.c:hover{background:#222}tr.sel{background:#332b00}
.b{border-radius:3px;padding:1px 6px;font-size:12px}.mine{background:#1d5a2e}.miss{background:#5a4a1d}.model{background:#14505a}.rej{background:#333;color:#999}
.legend{color:#999;line-height:1.7}.sw{display:inline-block;width:12px;height:12px;vertical-align:middle;margin-right:4px}</style>
<div class=bar><b id=pos></b><span id=meta></span><span id=state></span></div>
<canvas id=c width=1800 height=260></canvas>
<div class=bar>
 <button onclick=go(-1)>&larr; prev</button><button onclick=accept()><b>&#10003; accept &amp; next (Enter)</b></button><button onclick=go(1)>next &rarr;</button>
 <button onclick=undoOne()>&#8630; undo (Z)</button><button onclick=resetTake()>reset to saved</button>
 <label>show <select id=q onchange=refilter()>
  <option value=todo>to do: disagreements not yet reviewed</option><option value=dis>all disagreements</option>
  <option value=rev>already reviewed (re-check)</option><option value=all>all takes</option></select></label></div>
<div class=bar>
 <button onclick=toggle()>&#9654; play / stop (Space)</button><button onclick=play(0)>&#8635; from start (R)</button>
 <label><input type=checkbox id=loop> loop selected (L)</label>
 <span class=grp>speed <button data-r=1 style=font-weight:bold onclick=speed(1)>100%</button><button data-r=.5 onclick=speed(.5)>50%</button><button data-r=.25 onclick=speed(.25)>25%</button></span>
 <span class=grp>volume <button data-g=0 style=font-weight:bold onclick=vol(0)>normalised</button><button data-g=12 onclick=vol(12)>+12 dB</button><button data-g=24 onclick=vol(24)>+24 dB</button></span>
 <label><input type=checkbox id=hp onchange=route()> only &gt;1.5 kHz (F)</label></div>
<div style="display:flex;gap:40px;align-items:flex-start"><div><b id=count></b><table id=marks></table></div>
<div class=legend>
<span class=sw style=background:#3c6></span>your clicks (saved as labels) &nbsp; <span class=sw style="border:2px dashed #2cd"></span>model suggestion you haven't marked &nbsp; <span class=sw style="border:2px dashed #777"></span>suggestion you dismissed &nbsp; <span class=sw style=background:#2dd;height:5px></span>model's detections
&nbsp; <span class=sw style=background:#fd3;width:3px></span>logged key event &nbsp; <span class=sw style=background:#12304a></span>speech<br>
<b>Add</b> a click: drag across it on the waveform, or press <kbd>M</kbd> at the white line, or click the <b>+</b> on a model suggestion.<br>
<b>Remove</b> a click, or <b>dismiss</b> a model suggestion as not a click: click its <b>&times;</b>, or right-click it, or select it and press <kbd>Delete</kbd>.<br>
<b>Adjust</b>: drag a green box's edge, or press inside it and drag (moves the nearer edge); new clicks are only drawn from empty waveform. Or select it and nudge 5 ms: <kbd>[</kbd> <kbd>]</kbd> start, <kbd>Shift</kbd>+<kbd>[</kbd> <kbd>]</kbd> end.<br>
<b>Listen</b>: click any box (or a row in the list) to select it and hear just that stretch. <kbd>Tab</kbd> / <kbd>Shift+Tab</kbd> next / previous box, <kbd>P</kbd> play it again, <kbd>A</kbd> add the selected suggestion.<br>
Click empty waveform to move the white line. <kbd>Z</kbd> undo &middot; <kbd>Esc</kbd> deselect &middot; <kbd>&larr;</kbd> <kbd>&rarr;</kbd> take (edits are saved when you leave a take) &middot; <kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> speed &middot; <kbd>F</kbd> filter</div></div>
<script>
let T=[],order=[],oi=0,i=-1,buf=null,ac=new AudioContext(),sel=null,drag=null,src=null,t0=0,off=0,head=0,rate=1,wave=null,trace=null,gdb=0,peak=1,undo=[],saved='[]';
const c=document.getElementById('c'),g=c.getContext('2d'),W=c.width,H=c.height,$=id=>document.getElementById(id);
fetch('/takes').then(r=>r.json()).then(d=>{T=d;refilter()});
window.onbeforeunload=e=>{if(i>=0&&dirty())e.preventDefault()};
async function refilter(){if(i>=0&&dirty())await save();const q=$('q').value,hasP=T.some(t=>'disagree' in t);
 const keep={todo:t=>(!hasP||t.disagree)&&!t.reviewed_at,dis:t=>!hasP||t.disagree,rev:t=>!!t.reviewed_at,all:t=>true}[q];
 order=T.map((t,k)=>k).filter(k=>keep(T[k]));order.sort((a,b)=>(T[a].split=='test'?0:1)-(T[b].split=='test'?0:1)||a-b);
 oi=Math.max(0,order.indexOf(i));if(order.length){i=order[oi];load()}else{stop();i=-1;g.clearRect(0,0,W,H);$('pos').textContent='nothing to show here';$('meta').textContent=$('state').textContent='';$('marks').innerHTML=$('count').textContent=''}}
const gain=ac.createGain(),hpf=ac.createBiquadFilter(),lim=ac.createDynamicsCompressor();
hpf.type='highpass';hpf.frequency.value=1500;lim.threshold.value=-3;lim.knee.value=0;lim.ratio.value=20;lim.attack.value=.001;lim.release.value=.05;
lim.connect(ac.destination);function route(){gain.disconnect();hpf.disconnect();
 if($('hp').checked){gain.connect(hpf);hpf.connect(lim)}else gain.connect(lim)}route();
function vol(db){gdb=db;document.querySelectorAll('[data-g]').forEach(b=>b.style.fontWeight=+b.dataset.g==db?'bold':'');setGain()}
function setGain(){gain.gain.value=.9/peak*10**(gdb/20)}
async function load(){stop();head=0;sel=null;undo=[];const t=T[i];t.rejected=t.rejected||[];saved=st();
 $('pos').textContent=`${oi+1} / ${order.length}${order.length<T.length?' (of '+T.length+')':''} · ${t.split}`;
 $('meta').textContent=`${t.id} · ${t.duration_s}s · ${t.flags.join(' ')}`;
 buf=await ac.decodeAudioData(await (await fetch('/audio/'+t.file)).arrayBuffer());wave=null;trace=null;
 peak=1e-6;for(const v of buf.getChannelData(0))peak=Math.max(peak,Math.abs(v));setGain();draw();trace=await hpTrace();draw()}
async function hpTrace(){const o=new OfflineAudioContext(1,buf.length,buf.sampleRate),s=o.createBufferSource(),f=o.createBiquadFilter();
 f.type='highpass';f.frequency.value=1500;s.buffer=buf;s.connect(f);f.connect(o.destination);s.start();
 const d=(await o.startRendering()).getChannelData(0),step=d.length/W,e=new Float32Array(W);
 for(let x=0;x<W;x++){let a=0,n=0;for(let k=Math.floor(x*step);k<(x+1)*step;k++){a+=d[k]*d[k];n++}e[x]=10*Math.log10(a/Math.max(1,n)+1e-12)}
 const srt=[...e].sort((a,b)=>a-b),lo=srt[Math.floor(W*.1)],hi=srt[W-1];return e.map(v=>Math.max(0,(v-lo)/Math.max(1,hi-lo)))}
const X=s=>s/T[i].duration_s*W, S=x=>Math.min(T[i].duration_s,Math.max(0,x/W*T[i].duration_s));
const ex=e=>e.offsetX*W/c.clientWidth, ey=e=>e.offsetY*H/c.clientHeight;
const ov=(a,b)=>a[0]<=b[1]&&b[0]<=a[1], st=()=>JSON.stringify([T[i].clicks,T[i].rejected]), dirty=()=>st()!==saved;
// Boxes on the waveform: your clicks ('mine'), plus model detections that overlap none of them ('model')
function cands(){const t=T[i],p=t.pred||[];
 const r=t.clicks.map((k,j)=>({kind:'mine',j,k,both:p.some(q=>ov(q,k))}));
 p.forEach((k,j)=>{if(!t.clicks.some(q=>ov(q,k)))r.push({kind:'model',j,k,rej:t.rejected.some(q=>ov(q,k))})});return r.sort((a,b)=>a.k[0]-b.k[0])}
const isSel=d=>!!sel&&sel.kind==d.kind&&sel.j==d.j, selK=()=>sel&&(sel.kind=='mine'?T[i].clicks:T[i].pred)[sel.j];
const span=k=>{const x0=X(k[0]);return [x0,Math.max(3,X(k[1])-x0)]}, hbox=k=>{const [x0,w]=span(k);return [x0+w+2,4,20]};
// Buttons drawn beside a box: x removes your click; on a model box + adds it, x dismisses it as not a click (saved in `rejected`)
const handles=d=>{const [hx,hy,hs]=hbox(d.k);return d.kind=='mine'?[['remove','×','#b33',hx,hy,hs]]:
 [['add','+','#178',hx,hy,hs],d.rej?['unreject','↺','#555',hx+hs+2,hy,hs]:['reject','×','#b33',hx+hs+2,hy,hs]]};
function act(d,a){if(a=='remove')remove(d.j);else if(a=='add')addModel(d.j);else reject(d.j)}
function waveform(){const o=document.createElement('canvas');o.width=W;o.height=H;const q=o.getContext('2d'),d=buf.getChannelData(0),step=d.length/W;
 let m=0;for(const v of d)m=Math.max(m,Math.abs(v));q.strokeStyle='#bbb';q.beginPath();
 for(let x=0;x<W;x++){let lo=0,hi=0;for(let k=Math.floor(x*step);k<(x+1)*step;k++){lo=Math.min(lo,d[k]);hi=Math.max(hi,d[k])}
  q.moveTo(x,H/2-hi/m*H/2);q.lineTo(x,H/2-lo/m*H/2)}q.stroke();return o}
function draw(){const t=T[i];g.clearRect(0,0,W,H);
 if(t.speech_s){g.fillStyle='#12304a';g.fillRect(X(t.speech_s[0]),0,X(t.speech_s[1])-X(t.speech_s[0]),H)}
 g.drawImage(wave=wave||waveform(),0,0);
 if(trace){g.strokeStyle='rgba(255,150,60,.5)';g.beginPath();trace.forEach((v,x)=>x?g.lineTo(x,H-v*(H-30)):g.moveTo(x,H-v*(H-30)));g.stroke()}
 g.fillStyle='#fd3';t.key_events_s.forEach(e=>g.fillRect(X(e)-1,H-14,3,14));
 cands().forEach((d,n)=>{const [x0,w]=span(d.k),on=isSel(d),[hx,hy,hs]=hbox(d.k);
  if(d.kind=='mine'){g.fillStyle=on?'rgba(80,230,120,.5)':'rgba(60,200,100,.3)';g.fillRect(x0,0,w,H);g.fillStyle='#3c6';g.fillRect(x0,0,2,H);g.fillRect(x0+w-2,0,2,H)}
  else{const cl=d.rej?'140,140,140':'40,200,230';g.fillStyle=`rgba(${cl},${on?.3:d.rej?.08:.1})`;g.fillRect(x0,0,w,H);g.setLineDash([6,4]);g.strokeStyle=d.rej?'#777':'#2cd';g.lineWidth=2;g.strokeRect(x0+1,1,w-2,H-2);g.setLineDash([]);g.lineWidth=1}
  if(on){g.strokeStyle='#fd3';g.lineWidth=3;g.strokeRect(x0-2,1.5,w+4,H-3);g.lineWidth=1}
  g.font='bold 18px system-ui';g.textAlign='center';handles(d).forEach(([a,s,col,hx,hy,hs])=>{g.fillStyle=col;g.fillRect(hx,hy,hs,hs);g.fillStyle='#fff';g.fillText(s,hx+hs/2,hy+hs-4)});
  if(d.rej){g.font='12px system-ui';g.fillStyle='#999';g.fillText('not a click',x0+w/2,40)}g.font='bold 14px system-ui';g.fillStyle='#ddd';g.fillText(n+1,x0+w/2,H-22);g.textAlign='left'});
 g.fillStyle='#2dd';(t.pred||[]).forEach(k=>{const [x0,w]=span(k);g.fillRect(x0,0,w,5)});
 g.fillStyle='#fff';g.fillRect(X(head)-1,0,2,H);list()}
function list(){const t=T[i],cs=cands();
 $('state').innerHTML=(t.reviewed_at?`<span class=ok>reviewed ${t.reviewed_at.slice(0,16).replace('T',' ')}</span>`:'<span class=flag>not reviewed</span>')+
  (dirty()?' <span class=dirty>· unsaved changes (saved on Enter or when you leave the take)</span>':'');
 $('count').textContent=`${t.clicks.length} click${t.clicks.length==1?'':'s'} marked`+(t.pred_run?` · model ${t.pred_run}`:'');
 $('marks').innerHTML=cs.map((d,n)=>`<tr class="c ${isSel(d)?'sel':''}" onclick="pick('${d.kind}',${d.j})"><td>${n+1}</td><td>${d.k[0].toFixed(3)}–${d.k[1].toFixed(3)} s</td>
  <td>${d.kind=='model'?(d.rej?'<span class="b rej">model only · you said not a click</span>':'<span class="b model">model only, not marked</span>'):d.both?'<span class="b mine">your click · model agrees</span>':'<span class="b miss">your click · model missed it</span>'}</td>
  <td><button onclick="event.stopPropagation();pick('${d.kind}',${d.j})">&#9654;</button></td>
  <td>${d.kind=='model'?`<button onclick="event.stopPropagation();addModel(${d.j})">+ add</button> <button onclick="event.stopPropagation();reject(${d.j})">${d.rej?'&#8634; undo dismiss':'&times; not a click'}</button>`:`<button onclick="event.stopPropagation();remove(${d.j})">&times; remove</button>`}</td></tr>`).join('')}
function play(from,to){stop();ac.resume();from=Math.max(0,Math.min(from,T[i].duration_s-.01));src=ac.createBufferSource();src.buffer=buf;src.playbackRate.value=rate;
 const env=ac.createGain(),now=ac.currentTime;src.connect(env);env.connect(gain);
 if(to!=null){const dur=Math.max(.005,to-from),real=dur/rate,f=Math.min(.003,real/4);  // 3 ms fades: hard edges would add clicks of their own
  env.gain.setValueAtTime(0,now);env.gain.linearRampToValueAtTime(1,now+f);env.gain.setValueAtTime(1,now+real-f);env.gain.linearRampToValueAtTime(0,now+real);
  src.start(0,from,dur)}else src.start(0,from);
 t0=now;off=from;head=from;const me=src;
 src.onended=()=>{if(src!==me)return;src=null;if(to!=null&&$('loop').checked)setTimeout(()=>{if(!src)play(from,to)},250);else draw()};tick()}
function playSel(){const k=selK();if(k)play(k[0],k[1])}
function stop(){if(src){const s=src;src=null;s.stop()}}
function toggle(){src?stop():play(head>=T[i].duration_s-.02?0:head)}
function speed(r){rate=r;document.querySelectorAll('[data-r]').forEach(b=>b.style.fontWeight=+b.dataset.r==r?'bold':'');if(src)play(head)}
function tick(){if(!src)return;head=Math.min(T[i].duration_s,off+(ac.currentTime-t0)*rate);draw();requestAnimationFrame(tick)}
function pick(kind,j){sel={kind,j};draw();playSel()}
function step(n){const cs=cands();if(!cs.length)return;const k=cs.findIndex(isSel),d=cs[k<0?(n>0?0:cs.length-1):(k+n+cs.length)%cs.length];pick(d.kind,d.j)}
function snap(){undo.push(st())}
function restore(v){[T[i].clicks,T[i].rejected]=JSON.parse(v);sel=null;draw()}
// Add a click; any of your clicks it overlaps are replaced, so drawing over a click redraws it
function setMine(k){const t=T[i];k=[+k[0].toFixed(3),+k[1].toFixed(3)];snap();t.clicks=t.clicks.filter(q=>!ov(q,k));t.clicks.push(k);
 t.clicks.sort((a,b)=>a[0]-b[0]);sel={kind:'mine',j:t.clicks.indexOf(k)};draw()}
function addModel(j){const k=T[i].pred[j];setMine([...k]);T[i].rejected=T[i].rejected.filter(q=>!ov(q,k));draw();playSel()}
function reject(j){const t=T[i],k=t.pred[j];snap();t.rejected=t.rejected.some(q=>ov(q,k))?t.rejected.filter(q=>!ov(q,k)):[...t.rejected,[...k]];draw()}
function remove(j){snap();T[i].clicks.splice(j,1);sel=null;draw()}
function markHere(){setMine([head,Math.min(T[i].duration_s,head+.02)]);playSel()}
function undoOne(){if(undo.length)restore(undo.pop())}
function resetTake(){if(dirty()){snap();restore(saved)}}
function handleAt(x,y){for(const d of cands())for(const [a,,,hx,hy,hs] of handles(d))if(x>=hx&&x<=hx+hs&&y>=hy&&y<=hy+hs)return [d,a];return null}
// Edge grabbing: the nearest edge within 10 screen px; pressing inside a green box grabs its nearer edge,
// so resizing never needs a pixel-exact hit and a drag that starts on a click can't draw a new one
const sc=()=>W/c.clientWidth;
function edge(x){const k=T[i].clicks;let best=null,bd=10*sc();for(let j=0;j<k.length;j++)for(const e of [0,1]){const dd=Math.abs(x-X(k[j][e]));if(dd<bd){bd=dd;best=[j,e]}}
 if(best)return best;const j=k.findIndex(q=>x>=X(q[0])&&x<=X(q[1]));return j<0?null:[j,x-X(k[j][0])<X(k[j][1])-x?0:1]}
function nudge(e,ds){const k=selK();if(!k||sel.kind!='mine')return;snap();k[e]=+Math.min(T[i].duration_s,Math.max(0,k[e]+ds)).toFixed(3);if(k[0]>k[1])k.reverse();draw();playSel()}
function boxAt(x){const cs=cands();return cs.find(d=>d.kind=='mine'&&inBox(d,x))||cs.find(d=>inBox(d,x))}
function inBox(d,x){const [x0,w]=span(d.k);return x>=x0-1&&x<=x0+w+1}
c.oncontextmenu=e=>{e.preventDefault();const d=boxAt(ex(e));if(d)d.kind=='mine'?remove(d.j):reject(d.j)};
c.onmousemove=e=>{const x=ex(e);if(!drag){c.style.cursor=handleAt(x,ey(e))?'pointer':edge(x)?'ew-resize':boxAt(x)?'pointer':'crosshair';return}
 if(!e.buttons)return;if(!drag.moved&&Math.abs(x-drag.x)>(drag.edge?3:8)*sc()){drag.moved=true;if(drag.edge)snap()}if(!drag.moved)return;
 if(drag.edge){const k=T[i].clicks[drag.edge[0]];k[drag.edge[1]]=+S(x).toFixed(3);if(k[0]>k[1]){k.reverse();drag.edge[1]^=1}draw()}
 else{draw();g.fillStyle='rgba(80,230,120,.35)';g.fillRect(Math.min(drag.x,x),0,Math.abs(x-drag.x),H)}};
c.onmousedown=e=>{if(e.button)return;const x=ex(e),h=handleAt(x,ey(e));
 if(h){drag=null;act(...h);return}
 drag={x,edge:edge(x),moved:false,box:boxAt(x)};if(drag.edge){sel={kind:'mine',j:drag.edge[0]};draw()}};
c.onmouseup=e=>{if(!drag)return;const x=ex(e),d=drag;drag=null;
 if(d.edge&&d.moved){playSel();return}
 if(d.moved){setMine([S(Math.min(d.x,x)),S(Math.max(d.x,x))]);playSel();return}
 if(d.box){pick(d.box.kind,d.box.j);return}
 sel=null;head=S(x);if(src)play(head);else draw()};
async function save(){const t=T[i];t.reviewed_at=new Date().toISOString();await fetch('/save',{method:'POST',body:JSON.stringify(t)});saved=st()}
async function accept(){await save();go(1)}
async function go(n){if(i<0)return;if(dirty())await save();const k=Math.min(order.length-1,Math.max(0,oi+n));if(k==oi){draw();return}oi=k;i=order[oi];load()}
document.onkeydown=e=>{const k=e.key.toLowerCase();if(e.target.tagName=='SELECT'||e.ctrlKey&&k!='z'||e.metaKey||e.altKey)return;
 if(e.target.tagName=='BUTTON'&&(k==' '||k=='enter'))e.preventDefault();
 if(k==' ')toggle();else if(k=='r')play(0);else if(k=='m')markHere();else if(k=='p')playSel();else if(k=='z')undoOne();
 else if(k=='tab'){e.preventDefault();step(e.shiftKey?-1:1)}else if(k=='escape'){sel=null;draw()}
 else if(k=='a'&&sel&&sel.kind=='model')addModel(sel.j);
 else if(k=='['||k=='{'||k==']'||k=='}')nudge(k=='['||k==']'?0:1,k=='['||k=='{'?-.005:.005);
 else if((k=='backspace'||k=='delete'||k=='x')&&sel){e.preventDefault();sel.kind=='mine'?remove(sel.j):reject(sel.j)}
 else if(k=='l')$('loop').checked=!$('loop').checked;else if(k=='f'){$('hp').checked=!$('hp').checked;route()}
 else if(k=='1')speed(1);else if(k=='2')speed(.5);else if(k=='3')speed(.25);
 else if(k=='enter')accept();else if(k=='arrowleft')go(-1);else if(k=='arrowright')go(1)};
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
