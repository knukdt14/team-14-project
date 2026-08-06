// 다트 던지기 캔버스.
//
// 좌표계가 두 개다:
//   - 캔버스 좌표: 실제로 화면에 그려지는 위치. 다트 비행은 여기서 계산한다.
//   - 지도 좌표 : map-data.json의 폴리곤 좌표. 지역 판정과 서버 전송은 여기서.
// 중앙 정렬·확대·회전은 전부 캔버스 쪽에만 거는 변환이고, 서버로 보내기 직전에
// canvasToMap()으로 되돌린다 — 그래야 from_canvas()가 올바른 위경도를 낸다.
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

  const FIT_MARGIN = 14;                    // 캔버스 가장자리 여백
  const FIT_MAX_ZOOM = 1.4;                 // 중앙 정렬하며 이만큼까지는 키운다
  const SPIN_SPEED = 1;                   // rad/s — 한 바퀴에 약 10초
  const SPIN_EASE_S = 0.45;                 // 켜고 끌 때 크기가 바뀌는 시간
  const SPIN_MARGIN = 14;                   // 회전해도 안 잘리게 두는 여백
  const SPIN_KEY = "triproll.dartSpin";

  // 너무 작아서 겨눌 수 없는 섬은 제 중심을 기준으로 키워서 그린다.
  // 꽂히면 확대를 되돌린 좌표를 서버에 보내므로 위경도는 그대로 맞는다.
  const ISLAND_NAMES = ["울릉", "독도"];
  const ISLAND_ZOOM = 2.1;

  const C = {
    sea: "#eef2f9", pad: "#e3e8f2", line: "#c3c9d9",
    // 조건에서 빠진 지역. 흰색·회색 계열을 유지한다.
    off: [222, 24, 88],            // #d7dce8 = hsl(222, 27%, 88%)
    offLine: "#c3c9d9",
    // 다트 본체·조준선·파워바·착지 표시에 쓰는 색. 여기 3개만 바꾸면 다트 전체 색이 바뀐다.
    dart: "#e63946", dartSoft: "#ff8591", dartDark: "#9d1420",
    gray: "#94a0b8",
  };

  // 시도별 파스텔 톤 [hue, 채도%, 명도%].
  // 붙어 있는 시도끼리는 색상환에서 멀리 떨어뜨렸고(특히 도 안에 박힌 광역시),
  // 광역시는 같은 계열보다 조금 진하게 잡아 도심처럼 읽히게 했다.
  // 시군구는 이 톤 안에서 명도·채도만 미세하게 흔들어 경계를 만든다.
  const SIDO_TONES = [
    ["서울", [5, 62, 72]],                                   // 살몬
    ["인천", [200, 55, 72]],                                 // 파우더 블루
    ["경기", [48, 66, 71]],                                  // 샌드 옐로
    ["강원", [158, 40, 68]],                                 // 세이지 민트
    ["충청북", [232, 48, 74]], ["충북", [232, 48, 74]],       // 페리윙클
    ["충청남", [340, 55, 76]], ["충남", [340, 55, 76]],       // 블러시 핑크
    ["대전", [192, 50, 68]],                                 // 아쿠아
    ["세종", [285, 44, 76]],                                 // 라일락
    ["전라북", [72, 46, 68]], ["전북", [72, 46, 68]],         // 소프트 라임
    ["전라남", [178, 42, 66]], ["전남", [178, 42, 66]],       // 틸
    ["광주", [268, 48, 74]],                                 // 바이올렛
    ["경상북", [32, 62, 72]], ["경북", [32, 62, 72]],         // 애프리콧
    ["대구", [335, 56, 71]],                                 // 로즈
    ["경상남", [140, 36, 68]], ["경남", [140, 36, 68]],       // 제이드
    ["부산", [212, 54, 70]],                                 // 스카이
    ["울산", [25, 58, 68]],                                  // 캐러멜
    ["제주", [251, 77, 77]],                                 // 오키드
  ];

  const poolSet = new Set(window.DART_POOL_NAMES || []);
  const SIDO = window.DART_REGION_SIDO || {};
  let MAP = { w: W, h: MAP_H, regions: [] };

  const cv = document.createElement("canvas");
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr;
  cv.style.width = W + "px"; cv.style.height = H + "px";
  const ctx = cv.getContext("2d");
  ctx.scale(dpr, dpr);
  stage.appendChild(cv);

  // 지역 폴리곤만 그려 두는 레이어. 배경(바다·패드)은 매 프레임 직접 칠하고
  // 이 레이어만 중앙 정렬·회전시킨다.
  const mapLayer = document.createElement("canvas");
  mapLayer.width = W * dpr; mapLayer.height = MAP_H * dpr;
  const mctx = mapLayer.getContext("2d");
  mctx.scale(dpr, dpr);

  let state = "idle";              // idle | pulling | flying | landed
  let pull = { x: 0, y: 0 };
  let shot = null, landed = null;
  let shakeUntil = 0, rings = [];
  let BOXES = null;
  let submitted = false;

  // --------------------------------------------------------------- 색 고르기
  function hashOf(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  }

  function toneOf(name) {
    const sido = SIDO[name] || "";
    for (const [key, tone] of SIDO_TONES) if (sido.indexOf(key) >= 0) return tone;
    return [hashOf(name) % 360, 46, 78];   // 시도 정보가 없으면 이름으로라도 갈라 준다
  }

  function fillOf(name, on) {
    const [h, sat, light] = toneOf(name);
    const j = hashOf(name) % 5;                        // 0..4 — 인접 시군구 구분용
    // 조건 밖: 색을 빼고 회색으로. 경계만 보이게 명도를 아주 조금 흔든다.
    if (!on) return `hsl(${C.off[0]}, ${C.off[1]}%, ${C.off[2] - 1.6 + j * 0.8}%)`;
    // 같은 도 안에서는 명도 ±3.6%, 채도 ±6%만 흔든다 — 계열은 유지하되 경계는 보이게.
    return `hsl(${h}, ${sat + (j - 2) * 3}%, ${light + (j - 2) * 1.8}%)`;
  }

  function strokeOf(name, on) {
    if (!on) return C.offLine;
    const [h, sat] = toneOf(name);
    return `hsla(${h}, ${Math.round(sat * 0.6)}%, 38%, .42)`;
  }

  // ------------------------------------------------- 작은 섬 확대 (울릉도 등)
  const islandZoom = {};           // 지역명 → {cx, cy, k}

  function bboxOf(polys) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const flat of polys) for (let i = 0; i < flat.length; i += 2) {
      if (flat[i] < x0) x0 = flat[i]; if (flat[i] > x1) x1 = flat[i];
      if (flat[i + 1] < y0) y0 = flat[i + 1]; if (flat[i + 1] > y1) y1 = flat[i + 1];
    }
    return { x0, y0, x1, y1 };
  }

  function zoomIslands() {
    for (const r of MAP.regions) {
      if (!ISLAND_NAMES.some((n) => r.n.indexOf(n) >= 0)) continue;
      const b = bboxOf(r.p);
      if (b.x0 > b.x1) continue;
      const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2, k = ISLAND_ZOOM;
      for (const flat of r.p) for (let i = 0; i < flat.length; i += 2) {
        flat[i] = cx + (flat[i] - cx) * k;
        flat[i + 1] = cy + (flat[i + 1] - cy) * k;
      }
      islandZoom[r.n] = { cx, cy, k };
    }
  }

  /** 확대해서 그린 섬에 꽂혔으면 원래 좌표로 되돌린다 (서버 전송용). */
  function unzoomIsland(name, x, y) {
    const z = islandZoom[name];
    if (!z) return { x, y };
    return { x: z.cx + (x - z.cx) / z.k, y: z.cy + (y - z.cy) / z.k };
  }

  // ------------------------------------------------------- 중앙 정렬 / 회전
  let FIT = null;                  // {cx, cy, baseScale, spinScale}
  let spinOn = false;
  let spinAng = 0;
  let spinK = 0;                   // 0=평소 지도, 1=회전 모드 (전환 보간)
  let spinFrozen = false;          // 꽂힌 뒤에는 결과를 읽을 수 있게 멈춘다

  const spinBox = document.getElementById("dart-spin");
  try {
    spinOn = localStorage.getItem(SPIN_KEY) === "1";
  } catch (e) { /* 프라이빗 모드 등 — 기본값으로 간다 */ }
  if (spinBox) {
    spinBox.checked = spinOn;
    spinBox.addEventListener("change", () => {
      spinOn = spinBox.checked;
      try { localStorage.setItem(SPIN_KEY, spinOn ? "1" : "0"); } catch (e) { /* noop */ }
    });
  }

  /** 땅덩어리 전체를 재서 캔버스 한가운데 오도록, 회전해도 안 잘리도록 배율을 잡는다. */
  function computeFit() {
    const all = [];
    for (const r of MAP.regions) for (const flat of r.p) all.push(flat);
    if (!all.length) { FIT = null; return; }

    const b = bboxOf(all);
    const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;

    const bw = Math.max(b.x1 - b.x0, 1), bh = Math.max(b.y1 - b.y0, 1);
    const baseScale = Math.min(
      (W - FIT_MARGIN * 2) / bw,
      (MAP_H - FIT_MARGIN * 2) / bh,
      FIT_MAX_ZOOM,
    );

    let R = 0;
    for (const flat of all) for (let i = 0; i < flat.length; i += 2) {
      const d = Math.hypot(flat[i] - cx, flat[i + 1] - cy);
      if (d > R) R = d;
    }
    const spinScale = Math.min(baseScale, (Math.min(W, MAP_H) / 2 - SPIN_MARGIN) / R);

    FIT = { cx, cy, baseScale, spinScale };
  }

  /** 지금 프레임의 지도 변환. 중앙 정렬은 항상, 회전은 spinK만큼. */
  function mapT() {
    if (!FIT) return null;
    return {
      s: FIT.baseScale + (FIT.spinScale - FIT.baseScale) * spinK,
      a: spinAng * spinK,
      cx: FIT.cx,
      cy: FIT.cy,
    };
  }

  function applyMapTransform(g) {
    const t = mapT();
    if (!t) return 1;
    g.translate(W / 2, MAP_H / 2);
    g.rotate(t.a);
    g.scale(t.s, t.s);
    g.translate(-t.cx, -t.cy);
    return t.s;
  }

  function canvasToMap(x, y) {
    const t = mapT();
    if (!t) return { x, y };
    const dx = x - W / 2, dy = y - MAP_H / 2;
    const c = Math.cos(-t.a), sn = Math.sin(-t.a);
    return { x: (dx * c - dy * sn) / t.s + t.cx, y: (dx * sn + dy * c) / t.s + t.cy };
  }

  function mapToCanvas(x, y) {
    const t = mapT();
    if (!t) return { x, y };
    const dx = (x - t.cx) * t.s, dy = (y - t.cy) * t.s;
    const c = Math.cos(t.a), sn = Math.sin(t.a);
    return { x: W / 2 + dx * c - dy * sn, y: MAP_H / 2 + dx * sn + dy * c };
  }

  // --------------------------------------------------------------- 그리기
  function buildMapLayer() {
    zoomIslands();
    computeFit();
    mctx.clearRect(0, 0, W, MAP_H);
    for (const r of MAP.regions) {
      const on = poolSet.has(r.n);
      mctx.fillStyle = fillOf(r.n, on);
      mctx.strokeStyle = strokeOf(r.n, on);
      mctx.lineWidth = 0.7;
      for (const flat of r.p) {
        mctx.beginPath();
        mctx.moveTo(flat[0], flat[1]);
        for (let i = 2; i < flat.length; i += 2) mctx.lineTo(flat[i], flat[i + 1]);
        mctx.closePath();
        mctx.fill();
        mctx.stroke();
      }
    }
  }

  function drawBackground() {
    ctx.fillStyle = C.sea;
    ctx.fillRect(0, 0, W, MAP_H);
    ctx.fillStyle = C.pad;
    ctx.fillRect(0, MAP_H, W, PAD);

    ctx.strokeStyle = C.line; ctx.globalAlpha = .5; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, MAP_H + .5); ctx.lineTo(W, MAP_H + .5); ctx.stroke();
    ctx.globalAlpha = 1;

    ctx.save();
    ctx.beginPath(); ctx.rect(0, 0, W, MAP_H); ctx.clip();   // 지도가 패드로 안 넘어가게
    applyMapTransform(ctx);
    ctx.drawImage(mapLayer, 0, 0, W, MAP_H);
    ctx.restore();
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
      const b = bboxOf([flat]);
      BOXES.push({ n: r.n, f: flat, x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 });
    }
  }

  // regionAt / snapToLand는 전부 "지도 좌표" 기준이다.
  function regionAt(x, y) {
    if (!BOXES) buildBoxes();
    for (const b of BOXES) {
      if (x < b.x0 || x > b.x1 || y < b.y0 || y > b.y1) continue;
      if (inPoly(b.f, x, y)) return b.n;
    }
    return null;
  }

  function snapToLand(x, y) {
    // 확대·정렬로 화면 배율이 달라졌으니 흡착 반경도 같은 비율로 환산한다.
    const t = mapT();
    const inv = t ? 1 / t.s : 1;
    for (let r = 5 * inv; r <= 16 * inv; r += 5.5 * inv)
      for (let a = 0; a < 16; a++) {
        const th = a / 16 * 6.2832;
        const nx = x + Math.cos(th) * r, ny = y + Math.sin(th) * r;
        const n = regionAt(nx, ny);
        if (n) return { n, x: nx, y: ny };
      }
    return null;
  }

  function land() {
    // 꽂힌 화면 위치를 지도 좌표로 되돌려서 판정한다.
    const m = canvasToMap(shot.tx, shot.ty);
    let mx = m.x, my = m.y;
    let name = regionAt(mx, my);
    if (name === null) {
      const s = snapToLand(mx, my);
      if (s) { name = s.n; mx = s.x; my = s.y; }
    }
    const ok = name !== null && poolSet.has(name);

    landed = { mx, my, name, ok, at: performance.now() };
    state = "landed";
    spinFrozen = true;                       // 결과를 읽는 동안은 멈춰 둔다
    shakeUntil = performance.now() + 270;
    rings = [{ at: performance.now() }, { at: performance.now() + 160 }];

    if (!submitted) {
      submitted = true;
      const real = unzoomIsland(name, mx, my);   // 섬 확대분을 되돌린 진짜 좌표
      setTimeout(() => submitThrow(real.x, real.y, name), 950);
    }
  }

  function submitThrow(x, y, name) {
    // 서버의 from_canvas()는 지도 좌표를 기대한다 — 화면 좌표를 보내면 안 된다.
    document.getElementById("dart-x").value = x.toFixed(1);
    document.getElementById("dart-y").value = y.toFixed(1);
    document.getElementById("dart-name").value = name || "";
    document.getElementById("dart-throw-form").submit();
  }

  let lastFrame = 0;
  function loop(now) {
    const dt = lastFrame ? Math.min((now - lastFrame) / 1000, 0.05) : 0;
    lastFrame = now;

    // 토글 전환은 부드럽게. 끄면 회전이 되감기며 지도가 제자리로 돌아온다.
    const target = spinOn ? 1 : 0;
    const step = dt / SPIN_EASE_S;
    if (spinK < target) spinK = Math.min(target, spinK + step);
    else if (spinK > target) spinK = Math.max(target, spinK - step);
    if (spinOn && !spinFrozen) spinAng = (spinAng + dt * SPIN_SPEED) % (Math.PI * 2);
    if (!spinOn && spinK === 0) spinAng = 0;

    ctx.save();
    ctx.clearRect(0, 0, W, H);
    if (now < shakeUntil) {
      const k = (shakeUntil - now) / 270;
      ctx.translate((Math.random() - .5) * 8 * k, (Math.random() - .5) * 8 * k);
    }
    drawBackground();

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
      ctx.strokeStyle = C.dartDark; ctx.lineWidth = 2; ctx.globalAlpha = .8;
      ctx.beginPath(); ctx.moveTo(ANCHOR.x, ANCHOR.y); ctx.lineTo(dx, dy); ctx.stroke();
      ctx.globalAlpha = 1;

      const bw = 130, bx = ANCHOR.x - bw / 2, by = MAP_H + 9;
      ctx.fillStyle = "rgba(28,35,51,.14)"; roundRect(bx, by, bw, 6, 3); ctx.fill();
      ctx.fillStyle = k > .93 ? C.dartSoft : C.dart;
      roundRect(bx, by, bw * k, 6, 3); ctx.fill();
    }

    if (len <= 4 && !landed) {
      ctx.globalAlpha = .35; ctx.strokeStyle = C.dart; ctx.lineWidth = 1.6;
      ctx.setLineDash([4, 5]);
      const gy = ANCHOR.y + 52 + Math.sin(now / 620) * 5;
      ctx.beginPath(); ctx.moveTo(ANCHOR.x, ANCHOR.y + 26); ctx.lineTo(ANCHOR.x, gy); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(ANCHOR.x, gy + 9); ctx.lineTo(ANCHOR.x - 6, gy); ctx.lineTo(ANCHOR.x + 6, gy);
      ctx.closePath(); ctx.fillStyle = C.dart; ctx.fill();
      ctx.globalAlpha = 1;
    }

    const bob = state === "pulling" ? 0 : Math.sin(now / 520) * 3;
    drawDart(dx, dy + bob, -Math.PI / 2, 1.9, C.dart);
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
      ctx.fillStyle = C.dart; ctx.fill();
    }
    ctx.globalAlpha = 1;
    drawDart(x, y, ang, sc, C.dart);
  }

  function drawLanded(now) {
    const el = now - landed.at;
    const col = landed.ok ? C.dart : C.gray;
    const p = mapToCanvas(landed.mx, landed.my);   // 지도가 돌아가면 핀도 같이 따라간다

    if (landed.name) {
      const reg = MAP.regions.find((r) => r.n === landed.name);
      if (reg) {
        ctx.save();
        ctx.beginPath(); ctx.rect(0, 0, W, MAP_H); ctx.clip();
        const sc = applyMapTransform(ctx);
        for (const flat of reg.p) {
          ctx.beginPath(); ctx.moveTo(flat[0], flat[1]);
          for (let i = 2; i < flat.length; i += 2) ctx.lineTo(flat[i], flat[i + 1]);
          ctx.closePath();
          ctx.fillStyle = landed.ok ? "rgba(255,255,255,.5)" : "rgba(148,160,184,.30)";
          ctx.fill();
          ctx.strokeStyle = col; ctx.lineWidth = (landed.ok ? 2.4 : 1.6) / sc; ctx.stroke();
        }
        ctx.restore();
      }
    }

    for (const r of rings) {
      const t = (now - r.at) / 620;
      if (t < 0 || t > 1) continue;
      ctx.beginPath(); ctx.arc(p.x, p.y, 8 + t * 46, 0, 6.284);
      ctx.strokeStyle = col; ctx.globalAlpha = (1 - t) * .75; ctx.lineWidth = 2; ctx.stroke();
    }
    ctx.globalAlpha = 1;

    const pop = Math.min(el / 180, 1);
    ctx.beginPath(); ctx.arc(p.x, p.y, 5.5 * (1 + .5 * (1 - pop)), 0, 6.284);
    ctx.fillStyle = col; ctx.fill();

    const wob = el < 520 ? Math.sin(el / 26) * .10 * (1 - el / 520) : 0;
    drawDart(p.x, p.y, -Math.PI * 0.72 + wob, 1, col);

    if (el > 150) {
      // 라벨은 회전시키지 않는다 — 글씨는 항상 똑바로 읽혀야 한다.
      ctx.globalAlpha = Math.min((el - 150) / 260, 1);
      ctx.font = "800 15px Pretendard, sans-serif";
      const flip = p.x > W * 0.62;
      ctx.textAlign = flip ? "right" : "left";
      const label = landed.ok ? landed.name : (landed.name ? landed.name + " (조건 밖)" : "바다에 빠졌다");

      const tw = ctx.measureText(label).width;
      const padX = 9, lh = 22;
      const tx = p.x + (flip ? -16 : 16);
      const bx = flip ? tx - tw - padX : tx - padX;
      ctx.fillStyle = col;
      roundRect(bx, p.y - lh / 2, tw + padX * 2, lh, lh / 2);
      ctx.fill();

      ctx.fillStyle = "#ffffff";
      ctx.fillText(label, tx, p.y + 5);
      ctx.globalAlpha = 1;
    }
  }

  function drawDart(x, y, ang, sc, col) {
    ctx.save();
    ctx.translate(x, y); ctx.rotate(ang); ctx.scale(sc, sc);
    ctx.lineCap = "round";
    ctx.strokeStyle = col; ctx.lineWidth = 2.8;
    ctx.beginPath(); ctx.moveTo(-6, 0); ctx.lineTo(-26, 0); ctx.stroke();
    ctx.fillStyle = col === C.dart ? C.dartSoft : "#8A93AC";
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(-9, -4); ctx.lineTo(-9, 4); ctx.closePath(); ctx.fill();
    ctx.fillStyle = col === C.dart ? C.dartDark : "#454E68";
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
      buildMapLayer();
      requestAnimationFrame(loop);
    })
    .catch(() => {
      buildMapLayer();
      requestAnimationFrame(loop);
    });
})();