"""
다트 던지기 캔버스 컴포넌트.

★ 폴더를 따로 만들 필요 없다 ★
예전에는 views/dart_component/index.html 을 직접 만들어야 해서
"No such component directory" 오류가 자주 났다. 지금은 HTML을 이 파일 안에 두고,
앱이 켜질 때 임시 폴더에 index.html + map.js 를 알아서 풀어 놓는다.

★ 왜 별도 모듈인가 ★
declare_component는 자기를 부른 쪽의 모듈 정보를 훑어본다.
st.navigation이 페이지 파일을 exec()로 실행하기 때문에
페이지 안에서 직접 부르면 "module is None" 오류가 난다.
평범한 모듈에 두고 import하면 정상 동작한다.

조준·당기기·비행·꽂힘 연출은 전부 브라우저(canvas)에서 돈다.
파이썬은 "어디에 꽂혔는지"만 돌려받는다.
"""

import tempfile
from pathlib import Path

import streamlit.components.v1 as components

# 시군구 경계 지도 (GADM 기반, data/korea_map.json 에서 읽는다)
MAP_JSON = Path(__file__).resolve().parent.parent / "data" / "korea_map.json"

_HTML = r"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<script src="map.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: transparent;
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    user-select: none; -webkit-user-select: none; overflow: hidden;
  }
  #stage { width: 100%; display: flex; flex-direction: column; align-items: center; }
  canvas { display: block; cursor: grab; touch-action: none; border-radius: 14px; }
  canvas.pulling { cursor: grabbing; }
  #tip {
    margin-top: 6px; font-size: 12.5px; color: #6B7290;
    text-align: center; pointer-events: none; transition: opacity .25s;
  }
</style>
</head>
<body>
<div id="stage">
  <canvas id="cv"></canvas>
  <div id="tip">아래쪽 다트를 <b style="color:#9AA3BC">아래로 당겼다가</b> 놓으세요. 당긴 만큼 멀리 날아갑니다.</div>
</div>

<script>
// ===========================================================================
// Streamlit 통신 (npm 빌드 없이 postMessage로 직접 주고받는다)
// ===========================================================================
function post(t, x) { window.parent.postMessage(Object.assign({isStreamlitMessage:true, type:t}, x), "*"); }
function sendValue(v) { post("streamlit:setComponentValue", {value:v, dataType:"json"}); }
function setHeight(h) { post("streamlit:setFrameHeight", {height:h}); }

let ARGS = null, lastReset = null;
window.addEventListener("message", (e) => {
  if (!e.data || e.data.type !== "streamlit:render") return;
  const first = ARGS === null;
  ARGS = e.data.args;
  if (first) { lastReset = ARGS.resetToken; boot(); return; }
  buildBase();                                    // 조건이 바뀌면 지도 색을 다시 칠한다
  if (ARGS.resetToken !== lastReset) { lastReset = ARGS.resetToken; reset(); }
});
post("streamlit:componentReady", {apiVersion: 1});


// ===========================================================================
// 설정
// ===========================================================================
const MAP = window.KMAP || {w: 500, h: 640, regions: []};
const W = MAP.w;
const MAP_H = MAP.h;                       // 지도가 그려지는 높이
const PAD   = 250;                         // 그 아래, 다트를 당기는 공간
                                           // (최대로 당겼을 때 다트 꼬리까지 들어갈 만큼)
const H     = MAP_H + PAD;

const ANCHOR   = {x: W * 0.5, y: MAP_H + 26};  // 다트를 잡고 있는 자리
const MAX_PULL = 150;                          // 최대로 당길 수 있는 거리
const POWER    = 4.4;                          // 당긴 거리 1px당 날아가는 거리
const FLIGHT_MS = 720;

let C = {
  sea:"#0B0F1C", pad:"#070A14", landOn:"#26355C", landOff:"#151C2E", line:"#3A4468",
  gold:"#E9B949", goldSoft:"#F5D98A", goldDark:"#C9963A",
  gray:"#5A6484", text:"#EDEBE4", muted:"#7A8199"
};

let cv, ctx, base, bctx, scale = 1;
let state = "idle";              // idle | pulling | flying | landed
let pull = {x: 0, y: 0};
let shot = null, landed = null;
let shakeUntil = 0, rings = [];
let poolSet = new Set();


// ===========================================================================
// 시작
// ===========================================================================
function boot() {
  if (ARGS.theme) Object.assign(C, ARGS.theme);

  cv = document.getElementById("cv");
  ctx = cv.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr;
  cv.style.width = W + "px"; cv.style.height = H + "px";
  ctx.scale(dpr, dpr);

  base = document.createElement("canvas");
  base.width = W * dpr; base.height = H * dpr;
  bctx = base.getContext("2d");
  bctx.scale(dpr, dpr);

  buildBase();
  setHeight(H + 34);

  cv.addEventListener("pointerdown", onDown);
  cv.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);

  requestAnimationFrame(loop);
}

function pos(e) {
  const r = cv.getBoundingClientRect();
  scale = r.width / W;
  return {x: (e.clientX - r.left) / scale, y: (e.clientY - r.top) / scale};
}


// ===========================================================================
// 지도 밑그림 — 조건이 바뀔 때만 다시 그린다
// ===========================================================================
function buildBase() {
  if (!bctx) return;
  poolSet = new Set((ARGS && ARGS.poolNames) || []);

  bctx.clearRect(0, 0, W, H);
  bctx.fillStyle = C.sea;
  bctx.fillRect(0, 0, W, MAP_H);
  bctx.fillStyle = C.pad || "#070A14";
  bctx.fillRect(0, MAP_H, W, PAD);

  // 지도와 당기는 공간의 경계선
  bctx.strokeStyle = C.line; bctx.globalAlpha = .5; bctx.lineWidth = 1;
  bctx.beginPath(); bctx.moveTo(0, MAP_H + .5); bctx.lineTo(W, MAP_H + .5); bctx.stroke();
  bctx.globalAlpha = 1;

  for (const r of MAP.regions) {
    const on = poolSet.has(r.n);
    for (const flat of r.p) {
      bctx.beginPath();
      bctx.moveTo(flat[0], flat[1]);
      for (let i = 2; i < flat.length; i += 2) bctx.lineTo(flat[i], flat[i+1]);
      bctx.closePath();
      bctx.fillStyle = on ? C.landOn : C.landOff;
      bctx.fill();
      bctx.strokeStyle = C.line;
      bctx.lineWidth = 0.7;
      bctx.stroke();
    }
  }
}


// ===========================================================================
// 당기기
// ===========================================================================
function onDown(e) {
  if (state === "flying") return;
  if (state === "landed") reset();
  state = "pulling";
  cv.classList.add("pulling");
  cv.setPointerCapture(e.pointerId);
  onMove(e);
}

function onMove(e) {
  if (state !== "pulling") return;
  const p = pos(e);
  let dx = p.x - ANCHOR.x, dy = p.y - ANCHOR.y;
  if (dy < 0) dy = 0;                                  // 아래로만 당긴다
  const len = Math.hypot(dx, dy);
  if (len > MAX_PULL) { dx = dx / len * MAX_PULL; dy = dy / len * MAX_PULL; }
  pull = {x: dx, y: dy};
}

function onUp() {
  if (state !== "pulling") return;
  cv.classList.remove("pulling");
  const len = Math.hypot(pull.x, pull.y);
  if (len < 18) { state = "idle"; pull = {x:0,y:0}; return; }   // 너무 조금 당김

  const k = len / MAX_PULL;
  const sp = 6 + 16 * Math.pow(k, 1.3);                // 세게 던질수록 조금 덜 정확
  const ang = Math.random() * Math.PI * 2;
  const rad = Math.sqrt(Math.random()) * sp;

  const tx = ANCHOR.x - pull.x * POWER + Math.cos(ang) * rad;
  const ty = ANCHOR.y - pull.y * POWER + Math.sin(ang) * rad;

  shot = {sx: ANCHOR.x + pull.x, sy: ANCHOR.y + pull.y, tx, ty,
          t0: performance.now(), spin: (Math.random()-.5) * 1.1};
  state = "flying";
  pull = {x:0, y:0};
  document.getElementById("tip").style.opacity = 0;
}

// ===========================================================================
// 어디에 꽂혔나 — 실제 시군구 경계로 판정한다
// ===========================================================================
function inPoly(flat, x, y) {
  let hit = false;
  for (let i = 0, j = flat.length - 2; i < flat.length; j = i, i += 2) {
    const xi = flat[i], yi = flat[i+1], xj = flat[j], yj = flat[j+1];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

// 폴리곤마다 경계상자를 미리 구해 둔다 (판정 속도)
let BOXES = null;
function buildBoxes() {
  BOXES = [];
  for (const r of MAP.regions) for (const flat of r.p) {
    let x0=1e9, y0=1e9, x1=-1e9, y1=-1e9;
    for (let i=0; i<flat.length; i+=2) {
      if (flat[i]<x0) x0=flat[i];   if (flat[i]>x1) x1=flat[i];
      if (flat[i+1]<y0) y0=flat[i+1]; if (flat[i+1]>y1) y1=flat[i+1];
    }
    BOXES.push({n:r.n, f:flat, x0, y0, x1, y1});
  }
}

function regionAt(x, y) {
  if (!BOXES) buildBoxes();
  for (const b of BOXES) {
    if (x < b.x0 || x > b.x1 || y < b.y0 || y > b.y1) continue;
    if (inPoly(b.f, x, y)) return b.n;
  }
  return null;
}

// 아깝게 물에 빠졌으면 가장 가까운 땅으로 붙여 준다 (16px 이내)
function snapToLand(x, y) {
  for (let r = 5; r <= 16; r += 5.5)
    for (let a = 0; a < 16; a++) {
      const t = a / 16 * 6.2832;
      const nx = x + Math.cos(t) * r, ny = y + Math.sin(t) * r;
      const n = regionAt(nx, ny);
      if (n) return {n, x: nx, y: ny};
    }
  return null;
}

function land() {
  let lx = shot.tx, ly = shot.ty;
  let name = regionAt(lx, ly);
  if (name === null) {
    const s = snapToLand(lx, ly);
    if (s) { name = s.n; lx = s.x; ly = s.y; }
  }
  const ok = name !== null && poolSet.has(name);

  landed = {x: lx, y: ly, name: name, ok: ok, at: performance.now()};
  state = "landed";
  shakeUntil = performance.now() + 270;
  rings = [{at: performance.now()}, {at: performance.now() + 160}];

  sendValue({throwId: Date.now(), x: +lx.toFixed(1), y: +ly.toFixed(1),
             name: ok ? name : null, land: name});
}

function reset() {
  landed = null; shot = null; rings = []; pull = {x:0,y:0}; state = "idle";
  document.getElementById("tip").style.opacity = 1;
}


// ===========================================================================
// 그리기
// ===========================================================================
function loop(now) {
  ctx.save();
  ctx.clearRect(0, 0, W, H);
  if (now < shakeUntil) {
    const k = (shakeUntil - now) / 270;
    ctx.translate((Math.random()-.5) * 8 * k, (Math.random()-.5) * 8 * k);
  }
  ctx.drawImage(base, 0, 0, W, H);

  if (landed) drawLanded(now); else if (state === "flying") drawFlight(now);
  if (state === "idle" || state === "pulling") drawPull(now);

  ctx.restore();
  requestAnimationFrame(loop);
}

function drawPull(now) {
  const len = Math.hypot(pull.x, pull.y);
  const dx = ANCHOR.x + pull.x, dy = ANCHOR.y + pull.y;

  if (len > 4) {
    const k = len / MAX_PULL;

    // 어디에 꽂힐지는 알려 주지 않는다. 당기는 줄과 힘만 보여 준다.
    ctx.strokeStyle = C.goldDark; ctx.lineWidth = 2; ctx.globalAlpha = .8;
    ctx.beginPath(); ctx.moveTo(ANCHOR.x, ANCHOR.y); ctx.lineTo(dx, dy); ctx.stroke();
    ctx.globalAlpha = 1;

    // 힘 게이지 — 지도 바로 아래에 고정한다 (다트와 겹치지 않게)
    const bw = 130, bx = ANCHOR.x - bw/2, by = MAP_H + 9;
    ctx.fillStyle = "rgba(255,255,255,.12)"; roundRect(bx, by, bw, 6, 3); ctx.fill();
    ctx.fillStyle = k > .93 ? C.goldSoft : C.gold;
    roundRect(bx, by, bw * k, 6, 3); ctx.fill();
  }

  // 아직 안 당겼으면 아래로 당기라는 표시를 준다
  if (len <= 4 && !landed) {
    ctx.globalAlpha = .35; ctx.strokeStyle = C.gold; ctx.lineWidth = 1.6;
    ctx.setLineDash([4, 5]);
    const gy = ANCHOR.y + 52 + Math.sin(now/620) * 5;
    ctx.beginPath(); ctx.moveTo(ANCHOR.x, ANCHOR.y + 26); ctx.lineTo(ANCHOR.x, gy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(ANCHOR.x, gy + 9); ctx.lineTo(ANCHOR.x - 6, gy); ctx.lineTo(ANCHOR.x + 6, gy);
    ctx.closePath(); ctx.fillStyle = C.gold; ctx.fill();
    ctx.globalAlpha = 1;
  }

  const bob = state === "pulling" ? 0 : Math.sin(now/520) * 3;
  drawDart(dx, dy + bob, -Math.PI/2, 1.9, C.gold);
}

function drawFlight(now) {
  const k = Math.min((now - shot.t0) / FLIGHT_MS, 1);
  if (k >= 1) { land(); return; }
  const e = 1 - Math.pow(1 - k, 2.6);
  const x = shot.sx + (shot.tx - shot.sx) * e;
  const y = shot.sy + (shot.ty - shot.sy) * e - Math.sin(Math.PI * k) * 52;
  const sc = 2.3 - 1.3 * e;
  const ang = Math.atan2(shot.ty - shot.sy, shot.tx - shot.sx) + shot.spin * (1 - k);

  for (let i = 1; i <= 4; i++) {
    const kk = Math.max(k - i*0.045, 0), ee = 1 - Math.pow(1-kk, 2.6);
    ctx.globalAlpha = .13 * (1 - i/5);
    ctx.beginPath();
    ctx.arc(shot.sx + (shot.tx-shot.sx)*ee,
            shot.sy + (shot.ty-shot.sy)*ee - Math.sin(Math.PI*kk)*52,
            3.4 * (2.3 - 1.3*ee), 0, 6.284);
    ctx.fillStyle = C.gold; ctx.fill();
  }
  ctx.globalAlpha = 1;
  drawDart(x, y, ang, sc, C.gold);
}

function drawLanded(now) {
  const el = now - landed.at;
  const col = landed.ok ? C.gold : C.gray;

  // 꽂힌 시군구를 밝게 칠한다
  if (landed.name) {
    const reg = MAP.regions.find(r => r.n === landed.name);
    if (reg) for (const flat of reg.p) {
      ctx.beginPath(); ctx.moveTo(flat[0], flat[1]);
      for (let i = 2; i < flat.length; i += 2) ctx.lineTo(flat[i], flat[i+1]);
      ctx.closePath();
      ctx.fillStyle = landed.ok ? "rgba(233,185,73,.30)" : "rgba(122,129,153,.26)";
      ctx.fill();
      ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.stroke();
    }
  }

  for (const r of rings) {
    const t = (now - r.at) / 620;
    if (t < 0 || t > 1) continue;
    ctx.beginPath(); ctx.arc(landed.x, landed.y, 8 + t*46, 0, 6.284);
    ctx.strokeStyle = col; ctx.globalAlpha = (1-t)*.75; ctx.lineWidth = 2; ctx.stroke();
  }
  ctx.globalAlpha = 1;

  const pop = Math.min(el/180, 1);
  ctx.beginPath(); ctx.arc(landed.x, landed.y, 5.5*(1+.5*(1-pop)), 0, 6.284);
  ctx.fillStyle = col; ctx.fill();

  const wob = el < 520 ? Math.sin(el/26) * .10 * (1 - el/520) : 0;
  drawDart(landed.x, landed.y, -Math.PI*0.72 + wob, 1, col);

  if (el > 150) {
    ctx.globalAlpha = Math.min((el-150)/260, 1);
    ctx.font = "800 15px Pretendard, sans-serif";
    const flip = landed.x > W*0.62;
    ctx.textAlign = flip ? "right" : "left";
    ctx.fillStyle = landed.ok ? C.text : C.muted;
    const label = landed.ok ? landed.name : (landed.name ? landed.name + " (조건 밖)" : "바다에 빠졌다");
    ctx.fillText(label, landed.x + (flip ? -16 : 16), landed.y + 5);
    ctx.globalAlpha = 1;
  }
}

// 다트 한 자루. (x,y)가 촉끝, ang 방향으로 날아가는 자세.
function drawDart(x, y, ang, sc, col) {
  ctx.save();
  ctx.translate(x, y); ctx.rotate(ang); ctx.scale(sc, sc);
  ctx.lineCap = "round";
  ctx.strokeStyle = col; ctx.lineWidth = 2.8;
  ctx.beginPath(); ctx.moveTo(-6, 0); ctx.lineTo(-26, 0); ctx.stroke();
  ctx.fillStyle = col === C.gold ? C.goldSoft : "#8A93AC";
  ctx.beginPath(); ctx.moveTo(0,0); ctx.lineTo(-9,-4); ctx.lineTo(-9,4); ctx.closePath(); ctx.fill();
  ctx.fillStyle = col === C.gold ? C.goldDark : "#454E68";
  ctx.beginPath(); ctx.moveTo(-22,0); ctx.lineTo(-32,-7); ctx.lineTo(-30,0);
  ctx.lineTo(-32,7); ctx.closePath(); ctx.fill();
  ctx.restore();
}

function roundRect(x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x+r, y);
  ctx.arcTo(x+w, y, x+w, y+h, r); ctx.arcTo(x+w, y+h, x, y+h, r);
  ctx.arcTo(x, y+h, x, y, r); ctx.arcTo(x, y, x+w, y, r); ctx.closePath();
}
</script>
</body>
</html>
"""


def _component_dir() -> Path:
    """HTML과 지도 데이터를 임시 폴더에 써 두고 그 경로를 돌려준다.

    내용이 같으면 다시 쓰지 않는다. 폴더가 지워져도 다음 실행 때 새로 만든다.
    """
    d = Path(tempfile.gettempdir()) / "triproll_dart"
    d.mkdir(parents=True, exist_ok=True)

    _sync(d / "index.html", _HTML.lstrip("\n"))

    if MAP_JSON.exists():
        js = "window.KMAP=" + MAP_JSON.read_text(encoding="utf-8") + ";"
    else:
        # 지도 파일이 없어도 앱이 죽지 않게 빈 지도를 넣는다
        js = 'window.KMAP={"w":500,"h":640,"regions":[]};'
    _sync(d / "map.js", js)

    return d


def _sync(path: Path, body: str) -> None:
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        path.write_text(body, encoding="utf-8")


dart_canvas = components.declare_component("dart_throw", path=str(_component_dir()))