// 다트 던지기 캔버스. views/_dartcomp.py의 캔버스 로직을 그대로 옮기고,
// Streamlit postMessage 대신 착지 결과를 히든 폼에 채워 넣고 자동 제출한다.
(() => {
  const stage = document.getElementById("dart-stage");
  if (!stage) return;

  const W = 500;
  const MAP_H = 640;
  const PAD = 250;                          // 지도 아래, 다트를 당기는 공간
  const H = MAP_H + PAD;

  const ANCHOR = { x: W * 0.5, y: MAP_H + 26 };
  const MAX_PULL = 150;
  const POWER = 4.4;
  const FLIGHT_MS = 720;

  const C = {
    sea: "#eef2f9", pad: "#e3e8f2", landOn: "#2f6fed", landOff: "#d7dce8", line: "#c3c9d9",
    gold: "#2f6fed", goldSoft: "#7aa3ff", goldDark: "#1e4fc4",
    gray: "#94a0b8", text: "#1c2333", muted: "#6b7386",
  };

  const poolSet = new Set(window.DART_POOL_NAMES || []);
  let MAP = { w: W, h: MAP_H, regions: [] };

  const cv = document.createElement("canvas");
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr;
  cv.style.width = W + "px"; cv.style.height = H + "px";
  const ctx = cv.getContext("2d");
  ctx.scale(dpr, dpr);
  stage.appendChild(cv);

  const base = document.createElement("canvas");
  base.width = W * dpr; base.height = H * dpr;
  const bctx = base.getContext("2d");
  bctx.scale(dpr, dpr);

  let state = "idle";              // idle | pulling | flying | landed
  let pull = { x: 0, y: 0 };
  let shot = null, landed = null;
  let shakeUntil = 0, rings = [];
  let BOXES = null;
  let submitted = false;

  function buildBase() {
    bctx.clearRect(0, 0, W, H);
    bctx.fillStyle = C.sea;
    bctx.fillRect(0, 0, W, MAP_H);
    bctx.fillStyle = C.pad;
    bctx.fillRect(0, MAP_H, W, PAD);

    bctx.strokeStyle = C.line; bctx.globalAlpha = .5; bctx.lineWidth = 1;
    bctx.beginPath(); bctx.moveTo(0, MAP_H + .5); bctx.lineTo(W, MAP_H + .5); bctx.stroke();
    bctx.globalAlpha = 1;

    for (const r of MAP.regions) {
      const on = poolSet.has(r.n);
      for (const flat of r.p) {
        bctx.beginPath();
        bctx.moveTo(flat[0], flat[1]);
        for (let i = 2; i < flat.length; i += 2) bctx.lineTo(flat[i], flat[i + 1]);
        bctx.closePath();
        bctx.fillStyle = on ? C.landOn : C.landOff;
        bctx.fill();
        bctx.strokeStyle = C.line;
        bctx.lineWidth = 0.7;
        bctx.stroke();
      }
    }
  }

  function pos(e) {
    const r = cv.getBoundingClientRect();
    const scale = r.width / W;
    return { x: (e.clientX - r.left) / scale, y: (e.clientY - r.top) / scale };
  }

  function onDown(e) {
    if (state === "flying" || state === "landed") return;
    state = "pulling";
    cv.classList.add("pulling");
    cv.setPointerCapture(e.pointerId);
    onMove(e);
  }

  function onMove(e) {
    if (state !== "pulling") return;
    const p = pos(e);
    let dx = p.x - ANCHOR.x, dy = p.y - ANCHOR.y;
    if (dy < 0) dy = 0;
    const len = Math.hypot(dx, dy);
    if (len > MAX_PULL) { dx = dx / len * MAX_PULL; dy = dy / len * MAX_PULL; }
    pull = { x: dx, y: dy };
  }

  function onUp() {
    if (state !== "pulling") return;
    cv.classList.remove("pulling");
    const len = Math.hypot(pull.x, pull.y);
    if (len < 18) { state = "idle"; pull = { x: 0, y: 0 }; return; }

    const k = len / MAX_PULL;
    const sp = 6 + 16 * Math.pow(k, 1.3);
    const ang = Math.random() * Math.PI * 2;
    const rad = Math.sqrt(Math.random()) * sp;

    const tx = ANCHOR.x - pull.x * POWER + Math.cos(ang) * rad;
    const ty = ANCHOR.y - pull.y * POWER + Math.sin(ang) * rad;

    shot = {
      sx: ANCHOR.x + pull.x, sy: ANCHOR.y + pull.y, tx, ty,
      t0: performance.now(), spin: (Math.random() - .5) * 1.1,
    };
    state = "flying";
    pull = { x: 0, y: 0 };
    const tip = document.getElementById("dart-tip");
    if (tip) tip.style.opacity = 0;
  }

  function inPoly(flat, x, y) {
    let hit = false;
    for (let i = 0, j = flat.length - 2; i < flat.length; j = i, i += 2) {
      const xi = flat[i], yi = flat[i + 1], xj = flat[j], yj = flat[j + 1];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) hit = !hit;
    }
    return hit;
  }

  function buildBoxes() {
    BOXES = [];
    for (const r of MAP.regions) for (const flat of r.p) {
      let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
      for (let i = 0; i < flat.length; i += 2) {
        if (flat[i] < x0) x0 = flat[i]; if (flat[i] > x1) x1 = flat[i];
        if (flat[i + 1] < y0) y0 = flat[i + 1]; if (flat[i + 1] > y1) y1 = flat[i + 1];
      }
      BOXES.push({ n: r.n, f: flat, x0, y0, x1, y1 });
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

  function snapToLand(x, y) {
    for (let r = 5; r <= 16; r += 5.5)
      for (let a = 0; a < 16; a++) {
        const t = a / 16 * 6.2832;
        const nx = x + Math.cos(t) * r, ny = y + Math.sin(t) * r;
        const n = regionAt(nx, ny);
        if (n) return { n, x: nx, y: ny };
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

    landed = { x: lx, y: ly, name, ok, at: performance.now() };
    state = "landed";
    shakeUntil = performance.now() + 270;
    rings = [{ at: performance.now() }, { at: performance.now() + 160 }];

    if (!submitted) {
      submitted = true;
      setTimeout(() => submitThrow(lx, ly, name), 950);
    }
  }

  function submitThrow(x, y, name) {
    document.getElementById("dart-x").value = x.toFixed(1);
    document.getElementById("dart-y").value = y.toFixed(1);
    document.getElementById("dart-name").value = name || "";
    document.getElementById("dart-throw-form").submit();
  }

  function loop(now) {
    ctx.save();
    ctx.clearRect(0, 0, W, H);
    if (now < shakeUntil) {
      const k = (shakeUntil - now) / 270;
      ctx.translate((Math.random() - .5) * 8 * k, (Math.random() - .5) * 8 * k);
    }
    ctx.drawImage(base, 0, 0, W, H);

    if (landed) drawLanded(now); else if (state === "flying") drawFlight(now);
    if (state === "idle" || state === "pulling") drawPull(now);

    ctx.restore();
    requestAnimationFrame(loop);
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function drawPull(now) {
    const len = Math.hypot(pull.x, pull.y);
    const dx = ANCHOR.x + pull.x, dy = ANCHOR.y + pull.y;

    if (len > 4) {
      const k = len / MAX_PULL;
      ctx.strokeStyle = C.goldDark; ctx.lineWidth = 2; ctx.globalAlpha = .8;
      ctx.beginPath(); ctx.moveTo(ANCHOR.x, ANCHOR.y); ctx.lineTo(dx, dy); ctx.stroke();
      ctx.globalAlpha = 1;

      const bw = 130, bx = ANCHOR.x - bw / 2, by = MAP_H + 9;
      ctx.fillStyle = "rgba(28,35,51,.14)"; roundRect(bx, by, bw, 6, 3); ctx.fill();
      ctx.fillStyle = k > .93 ? C.goldSoft : C.gold;
      roundRect(bx, by, bw * k, 6, 3); ctx.fill();
    }

    if (len <= 4 && !landed) {
      ctx.globalAlpha = .35; ctx.strokeStyle = C.gold; ctx.lineWidth = 1.6;
      ctx.setLineDash([4, 5]);
      const gy = ANCHOR.y + 52 + Math.sin(now / 620) * 5;
      ctx.beginPath(); ctx.moveTo(ANCHOR.x, ANCHOR.y + 26); ctx.lineTo(ANCHOR.x, gy); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(ANCHOR.x, gy + 9); ctx.lineTo(ANCHOR.x - 6, gy); ctx.lineTo(ANCHOR.x + 6, gy);
      ctx.closePath(); ctx.fillStyle = C.gold; ctx.fill();
      ctx.globalAlpha = 1;
    }

    const bob = state === "pulling" ? 0 : Math.sin(now / 520) * 3;
    drawDart(dx, dy + bob, -Math.PI / 2, 1.9, C.gold);
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
      const kk = Math.max(k - i * 0.045, 0), ee = 1 - Math.pow(1 - kk, 2.6);
      ctx.globalAlpha = .13 * (1 - i / 5);
      ctx.beginPath();
      ctx.arc(shot.sx + (shot.tx - shot.sx) * ee,
        shot.sy + (shot.ty - shot.sy) * ee - Math.sin(Math.PI * kk) * 52,
        3.4 * (2.3 - 1.3 * ee), 0, 6.284);
      ctx.fillStyle = C.gold; ctx.fill();
    }
    ctx.globalAlpha = 1;
    drawDart(x, y, ang, sc, C.gold);
  }

  function drawLanded(now) {
    const el = now - landed.at;
    const col = landed.ok ? C.gold : C.gray;

    if (landed.name) {
      const reg = MAP.regions.find((r) => r.n === landed.name);
      if (reg) for (const flat of reg.p) {
        ctx.beginPath(); ctx.moveTo(flat[0], flat[1]);
        for (let i = 2; i < flat.length; i += 2) ctx.lineTo(flat[i], flat[i + 1]);
        ctx.closePath();
        ctx.fillStyle = landed.ok ? "rgba(47,111,237,.30)" : "rgba(148,160,184,.30)";
        ctx.fill();
        ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.stroke();
      }
    }

    for (const r of rings) {
      const t = (now - r.at) / 620;
      if (t < 0 || t > 1) continue;
      ctx.beginPath(); ctx.arc(landed.x, landed.y, 8 + t * 46, 0, 6.284);
      ctx.strokeStyle = col; ctx.globalAlpha = (1 - t) * .75; ctx.lineWidth = 2; ctx.stroke();
    }
    ctx.globalAlpha = 1;

    const pop = Math.min(el / 180, 1);
    ctx.beginPath(); ctx.arc(landed.x, landed.y, 5.5 * (1 + .5 * (1 - pop)), 0, 6.284);
    ctx.fillStyle = col; ctx.fill();

    const wob = el < 520 ? Math.sin(el / 26) * .10 * (1 - el / 520) : 0;
    drawDart(landed.x, landed.y, -Math.PI * 0.72 + wob, 1, col);

    if (el > 150) {
      ctx.globalAlpha = Math.min((el - 150) / 260, 1);
      ctx.font = "800 15px Pretendard, sans-serif";
      const flip = landed.x > W * 0.62;
      ctx.textAlign = flip ? "right" : "left";
      const label = landed.ok ? landed.name : (landed.name ? landed.name + " (조건 밖)" : "바다에 빠졌다");

      const tw = ctx.measureText(label).width;
      const padX = 9, padY = 5, lh = 22;
      const tx = landed.x + (flip ? -16 : 16);
      const bx = flip ? tx - tw - padX : tx - padX;
      ctx.fillStyle = col;
      roundRect(bx, landed.y - lh / 2, tw + padX * 2, lh, lh / 2);
      ctx.fill();

      ctx.fillStyle = "#ffffff";
      ctx.fillText(label, tx, landed.y + 5);
      ctx.globalAlpha = 1;
    }
  }

  function drawDart(x, y, ang, sc, col) {
    ctx.save();
    ctx.translate(x, y); ctx.rotate(ang); ctx.scale(sc, sc);
    ctx.lineCap = "round";
    ctx.strokeStyle = col; ctx.lineWidth = 2.8;
    ctx.beginPath(); ctx.moveTo(-6, 0); ctx.lineTo(-26, 0); ctx.stroke();
    ctx.fillStyle = col === C.gold ? C.goldSoft : "#8A93AC";
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(-9, -4); ctx.lineTo(-9, 4); ctx.closePath(); ctx.fill();
    ctx.fillStyle = col === C.gold ? C.goldDark : "#454E68";
    ctx.beginPath(); ctx.moveTo(-22, 0); ctx.lineTo(-32, -7); ctx.lineTo(-30, 0);
    ctx.lineTo(-32, 7); ctx.closePath(); ctx.fill();
    ctx.restore();
  }

  cv.addEventListener("pointerdown", onDown);
  cv.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);

  fetch(window.DART_MAP_URL)
    .then((r) => r.json())
    .then((data) => {
      MAP = data;
      buildBase();
      requestAnimationFrame(loop);
    })
    .catch(() => {
      buildBase();
      requestAnimationFrame(loop);
    });
})();
