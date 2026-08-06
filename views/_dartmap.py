"""
다트 모드 지도.

별도 지도 파일 없이, 시군구 161곳의 위경도를 그대로 점으로 찍으면
한반도 모양이 나온다.

던지는 방식은 감도 다트와 같다.
지역을 먼저 뽑아 놓고 다트를 거기로 보내는 게 아니라,
조준한 방향으로 다트를 실제로 날려서 꽂힌 자리를 먼저 정하고,
그 자리가 어느 시군구인지를 나중에 판정한다. 그래서 빗나갈 수도 있다.

★ 주의 ★
st.markdown(unsafe_allow_html=True)에 넘길 HTML은 반드시 한 줄로 만든다.
줄바꿈 뒤에 공백 4칸 이상이 오면 마크다운이 코드 블록으로 잡아서
SVG가 안 그려지고 소스가 그대로 화면에 찍힌다.
"""

import math
import random

# 지도에 담을 좌표 범위 (한반도 남쪽 + 제주 + 울릉까지)
LAT_MIN, LAT_MAX = 33.0, 38.65
LNG_MIN, LNG_MAX = 125.9, 131.2

W, H = 480, 600

# 다트를 던지는 자리 (지도 오른쪽 아래)
THROW_X, THROW_Y = 452, 566

# 조준 가능한 좌우 각도
AIM_LIMIT = 20

# 던질 때 손이 흔들리는 정도(도)와 날아가는 거리 배율
SPREAD = 7
DIST_MIN, DIST_MAX = 0.68, 1.28

# 다트 캔버스 크기. data/korea_map.json 을 만들 때 쓴 값과 반드시 같아야 한다.
CANVAS_W, CANVAS_H = 500, 640


def to_canvas(lat, lng):
    """위경도 -> 다트 캔버스 좌표."""
    x = (lng - LNG_MIN) / (LNG_MAX - LNG_MIN) * CANVAS_W
    y = (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * CANVAS_H
    return round(x, 1), round(y, 1)


def from_canvas(x, y):
    """다트 캔버스 좌표 -> 위경도. 다트가 꽂힌 자리를 되돌릴 때 쓴다."""
    lng = LNG_MIN + (x / CANVAS_W) * (LNG_MAX - LNG_MIN)
    lat = LAT_MAX - (y / CANVAS_H) * (LAT_MAX - LAT_MIN)
    return round(lat, 5), round(lng, 5)


GOLD = "#E9B949"
GRAY = "#5A6484"


def to_xy(lat, lng):
    """위경도 -> SVG 좌표. 위도는 위로 갈수록 크므로 뒤집는다."""
    x = (lng - LNG_MIN) / (LNG_MAX - LNG_MIN) * W
    y = (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * H
    return round(x, 1), round(y, 1)


def to_latlng(x, y):
    """SVG 좌표 -> 위경도. to_xy의 역변환."""
    lng = LNG_MIN + (x / W) * (LNG_MAX - LNG_MIN)
    lat = LAT_MAX - (y / H) * (LAT_MAX - LAT_MIN)
    return round(lat, 5), round(lng, 5)


def pool_center(pool):
    """후보 지역들의 한가운데. 기본 조준 방향이 된다."""
    if len(pool) == 0:
        return W / 2, H / 2
    xs, ys = zip(*[to_xy(r["lat"], r["lng"]) for _, r in pool.iterrows()])
    return sum(xs) / len(xs), sum(ys) / len(ys)


def base_angle(pool):
    """던지는 자리에서 후보 한가운데를 향하는 각도(라디안)."""
    cx, cy = pool_center(pool)
    return math.atan2(cy - THROW_Y, cx - THROW_X)


def aim_endpoint(pool, aim_deg, length=130):
    """조준선의 끝점. 지금 어디를 겨누고 있는지 보여 준다."""
    ang = base_angle(pool) + math.radians(aim_deg)
    return (
        round(THROW_X + math.cos(ang) * length, 1),
        round(THROW_Y + math.sin(ang) * length, 1),
    )


def throw(pool, aim_deg):
    """조준한 방향으로 다트를 던진다. 꽂힌 자리(SVG 좌표)를 돌려준다.

    어느 지역이 나올지는 여기서 정하지 않는다. 좌표만 정한다.
    """
    cx, cy = pool_center(pool)
    reach = math.hypot(cx - THROW_X, cy - THROW_Y)

    ang = (
        base_angle(pool)
        + math.radians(aim_deg)
        + math.radians(random.uniform(-SPREAD, SPREAD))
    )
    dist = reach * random.uniform(DIST_MIN, DIST_MAX)

    return (
        round(THROW_X + math.cos(ang) * dist, 1),
        round(THROW_Y + math.sin(ang) * dist, 1),
    )


def _dart(color=GOLD):
    """다트 하나. 오른쪽 위에서 왼쪽 아래(0,0)로 꽂히는 방향."""
    tip = "#F5D98A" if color == GOLD else "#8A93AC"
    fin = "#C9963A" if color == GOLD else "#454E68"
    return (
        '<g class="dart-body">'
        '<line x1="22" y1="-22" x2="4" y2="-4" stroke="' + color + '"'
        ' stroke-width="2.8" stroke-linecap="round"/>'
        '<path d="M0 0 L8 -4 L4 -8 Z" fill="' + tip + '"/>'
        '<path d="M19 -19 L29 -20 L28 -29 Z" fill="' + fin + '"/>'
        '</g>'
    )


def _wrap(inner):
    return (
        '<div class="dartmap-wrap">'
        '<svg viewBox="0 0 ' + str(W) + ' ' + str(H) + '" class="dartmap"'
        ' xmlns="http://www.w3.org/2000/svg" role="img"'
        ' aria-label="전국 시군구 지도">' + inner + '</svg></div>'
    )


def build_map(regions, pool_names, pool=None, aim_deg=0,
              landing=None, hit=None, animate=True):
    """지도 SVG를 만든다.

    pool_names : 조건을 통과한 지역 이름 집합 (밝게 표시)
    pool       : 조준선을 그리기 위한 후보 DataFrame
    aim_deg    : 지금 겨누고 있는 좌우 각도
    landing    : 다트가 꽂힌 SVG 좌표 (x, y). None이면 아직 조준 중.
    hit        : 판정된 지역 이름. None이면 빗나감(바다).
    animate    : 방금 던진 순간에만 True. False면 정지 화면.
    """
    dots = []
    for _, r in regions.iterrows():
        if hit and r["name"] == hit:
            continue
        x, y = to_xy(r["lat"], r["lng"])
        if r["name"] in pool_names:
            dots.append('<circle cx="%s" cy="%s" r="3.4" fill="#4C5A80"/>' % (x, y))
        else:
            dots.append('<circle cx="%s" cy="%s" r="2.2" fill="#212942"/>' % (x, y))

    # ── 조준 중 ────────────────────────────────────────────
    if landing is None:
        ax, ay = aim_endpoint(pool if pool is not None else regions, aim_deg)
        return _wrap(
            ''.join(dots)
            + '<line class="aim" x1="%s" y1="%s" x2="%s" y2="%s"/>'
              % (THROW_X, THROW_Y, ax, ay)
            + '<g transform="translate(%s,%s)" class="dart-idle">' % (THROW_X, THROW_Y)
            + _dart() + '</g>'
        )

    # ── 던진 뒤 ────────────────────────────────────────────
    lx, ly = landing
    color = GOLD if hit else GRAY

    parts = []
    dx = round(THROW_X - lx, 1)
    dy = round(THROW_Y - ly, 1)

    if animate:
        trail = round(math.hypot(dx, dy), 1)
        parts.append(
            '<line class="trail" x1="%s" y1="%s" x2="%s" y2="%s"'
            ' style="stroke-dasharray:%s;stroke-dashoffset:%s"/>'
            % (THROW_X, THROW_Y, lx, ly, trail, trail)
        )

    parts.append('<g transform="translate(%s,%s)">' % (lx, ly))

    if hit:
        flip = lx > W * 0.62
        label = (
            '<text class="hit-label%s" x="%s" y="6" text-anchor="%s">%s</text>'
            % (" fade" if animate else "", -16 if flip else 16,
               "end" if flip else "start", hit)
        )
        if animate:
            parts.append('<circle class="ripple" r="8"/>')
            parts.append('<circle class="ripple ripple-2" r="8"/>')
        parts.append(
            '<circle class="hit-dot%s" r="6" fill="%s"/>'
            % (" pop" if animate else "", color)
        )
    else:
        label = (
            '<text class="hit-label miss%s" x="16" y="6" text-anchor="start">'
            '바다에 빠졌다</text>' % (" fade" if animate else "")
        )
        parts.append(
            '<circle class="hit-dot%s" r="5" fill="%s"/>'
            % (" pop" if animate else "", color)
        )

    dart = _dart(color)
    if animate:
        parts.append(
            '<g class="dart-fly" style="--dx:%spx;--dy:%spx">' % (dx, dy) + dart + '</g>'
        )
    else:
        parts.append('<g>' + dart + '</g>')

    parts.append(label)
    parts.append('</g>')
    return _wrap(''.join(dots) + ''.join(parts))