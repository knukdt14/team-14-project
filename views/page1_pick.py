"""
P1. 여행지 추첨

뽑는 방식이 두 가지다.
  - 카드 : 무작위 3곳을 카드로 받아 하나를 고른다. 카드당 리롤 1회.
  - 다트 : 조준한 방향으로 다트를 날려 꽂힌 자리로 정한다. 빗나가면 바다.

다트는 지역을 먼저 뽑고 거기로 날리는 게 아니라, 꽂힌 좌표를 먼저 정하고
그 자리가 어느 시군구인지를 나중에 판정한다.

하나를 확정하면 TripContext에 기록되고 이후 페이지에서는 바꿀 수 없다.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import geo  # noqa: E402
from views._dartcomp import dart_canvas  # noqa: E402
from views._dartmap import to_canvas, from_canvas  # noqa: E402


# 등급별 색. 등급은 출발지에서 얼마나 먼지를 나타낸다.
GRADE_COLORS = {
    "가벼움": "#8A91A8",
    "괜찮음": "#4FC3D9",
    "본격적": "#A97BE8",
    "대장정": "#E9B949",
}

# 꽂힌 자리에서 이만큼보다 먼 곳밖에 없으면 바다에 빠진 것으로 본다
MISS_KM = 40


# ---------------------------------------------------------------------------
# 이 페이지 전용 스타일
# ---------------------------------------------------------------------------

st.markdown(
    """
<style>
/* 카드 공통 */
.card {
  position: relative; height: 296px; border-radius: 16px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  overflow: hidden; margin-bottom: 12px;
}
/* 뒷면 — 아직 안 뜯은 카드팩 */
.card-back { background: #131A2E; border: 1px solid #232B45; }
.card-back::before {
  content: ""; position: absolute; top: -60%; left: -60%;
  width: 220%; height: 220%;
  background: linear-gradient(115deg, transparent 42%, rgba(233,185,73,.16) 50%, transparent 58%);
  animation: sheen 3.4s ease-in-out infinite alternate;
}
@keyframes sheen { 0% { transform: translateX(-30%); } 100% { transform: translateX(30%); } }
.card-back .mark { font-size: 52px; font-weight: 900; color: #2B3350; z-index: 1; }
.card-back .hint { font-size: 13px; color: #4A5270; margin-top: 10px; z-index: 1; }
/* 앞면 — 열린 카드 */
.card-front {
  border: 1px solid var(--g); background: #101728;
  box-shadow: inset 0 0 60px -20px var(--g);
  animation: reveal .55s cubic-bezier(.2,.9,.3,1.2) both;
}
@keyframes reveal {
  0%   { transform: scale(.86) rotateY(28deg); opacity: 0; }
  60%  { transform: scale(1.03) rotateY(0deg); opacity: 1; }
  100% { transform: scale(1) rotateY(0deg); opacity: 1; }
}
.card-front::after {
  content: ""; position: absolute; inset: 0;
  background: radial-gradient(circle at 50% 45%, var(--g), transparent 62%);
  opacity: 0; animation: flash .55s ease-out both;
}
@keyframes flash { 0% { opacity: .45; } 100% { opacity: 0; } }
.grade {
  font-size: 11px; font-weight: 700; letter-spacing: .18em;
  color: var(--g); border: 1px solid var(--g);
  padding: 4px 12px; border-radius: 999px; margin-bottom: 18px; z-index: 1;
}
.region-sido { font-size: 14px; color: #7A8199; margin-bottom: 2px; z-index: 1; }
.region-name {
  font-size: 36px; font-weight: 900; color: #EDEBE4;
  letter-spacing: -0.03em; line-height: 1.15; text-align: center; z-index: 1;
}
.meta { font-size: 13px; color: #98A0B8; margin-top: 16px; z-index: 1; }
.tags { margin-top: 14px; z-index: 1; }
.tag {
  display: inline-block; font-size: 12px; color: #B9C0D4;
  background: #1A2238; border-radius: 6px; padding: 4px 10px; margin: 0 3px;
}
/* 다트 지도 */
.dartmap-wrap { display: flex; justify-content: center; padding: 4px 0 0; }
.dartmap { width: 100%; max-width: 400px; height: auto; overflow: visible; }
.aim { stroke: #E9B949; stroke-width: 1.6; stroke-dasharray: 5 5; opacity: .35; }
/* 조준 중인 다트는 살짝 들썩인다 */
.dart-idle .dart-body { animation: bob 2.2s ease-in-out infinite alternate; }
@keyframes bob { from { transform: translate(0,0); } to { transform: translate(5px,-6px); } }
/* 던진 자리에서 꽂힌 자리까지 1.15초 동안 날아간다 */
.dart-fly { animation: fly 1.15s cubic-bezier(.4,.05,.25,1) both; }
@keyframes fly {
  0%   { transform: translate(var(--dx), var(--dy)) scale(1.9); opacity: 0; }
  10%  { opacity: 1; }
  100% { transform: translate(0,0) scale(1); opacity: 1; }
}
/* 날아간 자국. 다트를 따라 그려졌다가 사라진다 */
.trail {
  fill: none; stroke: #E9B949; stroke-width: 1.4; opacity: .45;
  animation: draw 1.15s cubic-bezier(.4,.05,.25,1) both, trailOut .7s 1.15s ease-out both;
}
@keyframes draw { to { stroke-dashoffset: 0; } }
@keyframes trailOut { to { opacity: 0; } }
/* 아래 셋은 다트가 꽂힌 뒤(1.15초)에 시작한다.
   both를 쓰면 지연 시간에도 0% 상태가 적용돼 목표가 미리 드러나므로 forwards를 쓴다 */
.hit-dot { transform-origin: center; }
.hit-dot.pop { transform: scale(0); animation: pop .4s 1.15s cubic-bezier(.2,1.5,.4,1) forwards; }
@keyframes pop { from { transform: scale(0); } to { transform: scale(1); } }
.ripple {
  fill: none; stroke: #E9B949; stroke-width: 2; opacity: 0;
  transform-origin: center; animation: ripple 1.05s 1.15s ease-out forwards;
}
.ripple-2 { animation-delay: 1.42s; }
@keyframes ripple {
  0%   { transform: scale(.8); opacity: .9; }
  100% { transform: scale(5.2); opacity: 0; }
}
.hit-label { font-size: 15px; font-weight: 700; fill: #EDEBE4; }
.hit-label.miss { font-size: 14px; fill: #7A8199; }
.hit-label.fade { opacity: 0; animation: fadeIn .45s 1.28s forwards; }
@keyframes fadeIn { to { opacity: 1; } }
/* 다트 결과 */
.dart-result { text-align: center; padding: 8px 0 2px; }
.dart-result .place {
  font-size: 40px; font-weight: 900; color: #EDEBE4; letter-spacing: -0.03em;
}
.dart-result .place.miss { color: #7A8199; }
.dart-result .note { font-size: 14px; color: #7A8199; margin-top: 8px; }
.dart-result .coord { font-size: 12px; color: #5A6484; margin-top: 4px; }
/* 확정 배너 */
.confirmed {
  border: 1px solid #E9B949; border-radius: 16px;
  padding: 28px 32px; background: #14192B; text-align: center;
}
.confirmed .label { font-size: 12px; letter-spacing: .2em; color: #E9B949; }
.confirmed .place { font-size: 42px; font-weight: 900; color: #EDEBE4; margin: 8px 0 10px; }
.confirmed .note { font-size: 14px; color: #7A8199; line-height: 1.7; }
@media (prefers-reduced-motion: reduce) {
  .card-back::before, .card-front, .card-front::after,
  .dart-idle .dart-body, .dart-fly, .trail,
  .hit-dot, .ripple, .hit-label { animation: none; }
}
</style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# 상태 초기화
# ---------------------------------------------------------------------------

st.session_state.setdefault("cards", [])         # 카드 모드
st.session_state.setdefault("dart", None)        # 다트 모드 결과
st.session_state.setdefault("dart_throw_id", None)  # 이미 처리한 던지기
st.session_state.setdefault("dart_reset", 0)        # 캔버스 초기화 신호
st.session_state.setdefault("confirmed", None)   # 확정 결과 (두 모드 공용)


@st.cache_data
def get_regions():
    return geo.load_regions()


@st.cache_resource
def get_boundaries():
    # SGIS GeoJSON이 있으면 실제 경계 안에서 좌표를 뽑는다
    return geo.load_boundaries()


regions = get_regions()
boundaries = get_boundaries()


def make_card(region):
    """지역 정보로 카드 한 장을 만든다."""
    lat, lng = geo.pick_point(region, boundaries)
    return {
        "region": region.to_dict(),
        "point": (lat, lng),
        "opened": False,
        "rerolled": False,
    }


def confirm(pick, origin):
    """확정 처리. 카드든 다트든 같은 자리에 같은 모양으로 기록한다."""
    r = pick["region"]
    lat, lng = pick["point"]
    st.session_state.confirmed = pick
    st.session_state.trip_context["region"] = {
        "sido": r["sido"],
        "sigungu": r["sigungu"],
        "name": r["name"],
        "latitude": lat,
        "longitude": lng,
        "distance_km": int(r["distance_km"]),
        "locked": True,
    }
    st.session_state.trip_context["origin"] = {
        "name": origin["name"],
        "latitude": float(origin["lat"]),
        "longitude": float(origin["lng"]),
    }


def reset_all():
    st.session_state.cards = []
    st.session_state.dart = None
    st.session_state.dart_throw_id = None
    st.session_state.dart_reset = st.session_state.get('dart_reset', 0) + 1
    st.session_state.confirmed = None
    st.session_state.trip_context = {}


# ---------------------------------------------------------------------------
# 제목
# ---------------------------------------------------------------------------

st.markdown('<div class="page-title">여행지 추첨</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="page-sub">고민은 여기까지. 조건만 정하면 갈 곳은 카드가 정해 줍니다.</div>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# 조건 패널 (본문 가운데)
# ---------------------------------------------------------------------------

names = sorted(regions["name"].tolist())

with st.container(border=True):
    top_left, top_right = st.columns([1, 1])

    with top_left:
        origin_name = st.selectbox("출발지", options=names, index=names.index("대구"))

    with top_right:
        distance_label = st.select_slider(
            "이동 거리 상한",
            options=["100km", "200km", "300km", "제한 없음"],
            value="300km",
            help="당일치기면 100km, 1박이면 200km 정도가 무난합니다.",
        )

    origin = regions[regions["name"] == origin_name].iloc[0]
    max_km = None if distance_label == "제한 없음" else int(distance_label.replace("km", ""))

    opt_left, opt_right = st.columns([1, 1])
    with opt_left:
        coastal_only = st.toggle("바다 있는 곳만", value=False)
    with opt_right:
        exclude_metro = st.toggle("광역시 제외", value=True)

    pool = geo.filter_regions(
        regions,
        origin=origin,
        max_km=max_km,
        coastal_only=coastal_only,
        exclude_metro=exclude_metro,
    )

    st.divider()

    mode = st.segmented_control(
        "뽑는 방식",
        options=["카드 뽑기", "다트 던지기"],
        default="카드 뽑기",
        key="pick_mode",
        label_visibility="collapsed",
    ) or "카드 뽑기"

    if mode == "카드 뽑기":
        info_col, button_col = st.columns([2, 1])
        with info_col:
            if len(pool) < 3:
                st.markdown(f"후보가 **{len(pool)}곳**뿐입니다. 조건을 조금 풀어 주세요.")
            else:
                st.markdown(f"조건에 맞는 여행지 **{len(pool)}곳**에서 3장을 뽑습니다.")
        with button_col:
            draw = st.button(
                "카드 뽑기",
                type="primary",
                use_container_width=True,
                disabled=len(pool) < 3,
            )

        if draw:
            picked = geo.draw_regions(pool, count=3)
            st.session_state.cards = [make_card(r) for r in picked]
            st.session_state.confirmed = None
            st.rerun()

    else:
        if len(pool) == 0:
            st.markdown("조건에 맞는 곳이 없습니다. 조건을 조금 풀어 주세요.")
        else:
            st.markdown(
                f"조건에 맞는 여행지 **{len(pool)}곳**이 밝게 표시됩니다. "
                "지도를 직접 겨눠서 던지세요."
            )


st.write("")


# ---------------------------------------------------------------------------
# 카드 렌더링
# ---------------------------------------------------------------------------

def render_back():
    return """
    <div class="card card-back">
      <div class="mark">?</div>
      <div class="hint">눌러서 열기</div>
    </div>
    """


def render_front(card):
    r = card["region"]
    grade = geo.grade_of(r["distance_km"])
    color = GRADE_COLORS[grade]

    tags = []
    if r["coastal"]:
        tags.append("바다")
    if r["metro"]:
        tags.append("도시")
    tags.append(grade)
    tag_html = "".join(f'<span class="tag">{t}</span>' for t in tags)

    sido_line = "" if r["metro"] else r["sido"]

    return f"""
    <div class="card card-front" style="--g:{color}">
      <div class="grade">{grade}</div>
      <div class="region-sido">{sido_line}</div>
      <div class="region-name">{r['sigungu']}</div>
      <div class="meta">출발지에서 약 {int(r['distance_km'])}km</div>
      <div class="tags">{tag_html}</div>
    </div>
    """


if st.session_state.confirmed:
    # ----- 확정 화면 (두 모드 공용) -----
    conf = st.session_state.confirmed
    r = conf["region"]
    lat, lng = conf["point"]

    st.markdown(
        f"""
        <div class="confirmed">
          <div class="label">이번 여행지</div>
          <div class="place">{r['name']}</div>
          <div class="note">
            출발지에서 약 {int(r['distance_km'])}km · 좌표 {lat}, {lng}<br>
            여행지는 여기서 확정됩니다. 다음 페이지에서는 변경할 수 없습니다.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")
    st.map(pd.DataFrame({"lat": [lat], "lon": [lng]}), zoom=9)

    left, right = st.columns([1, 1])
    with left:
        if st.button("처음부터 다시", use_container_width=True):
            reset_all()
            st.rerun()
    with right:
        if st.button("여행 플래너로 이동", type="primary", use_container_width=True):
            st.switch_page("views/page2_planner.py")


elif mode == "카드 뽑기":
    if not st.session_state.cards:
        st.info("위에서 조건을 정하고 **카드 뽑기**를 눌러 주세요.")

    else:
        columns = st.columns(3, gap="medium")

        for i, card in enumerate(st.session_state.cards):
            with columns[i]:
                if not card["opened"]:
                    st.markdown(render_back(), unsafe_allow_html=True)
                    if st.button("카드 열기", key=f"open_{i}", use_container_width=True):
                        st.session_state.cards[i]["opened"] = True
                        st.rerun()
                    continue

                st.markdown(render_front(card), unsafe_allow_html=True)

                act_left, act_right = st.columns(2)
                with act_left:
                    used_up = card["rerolled"]
                    if st.button(
                        "리롤 완료" if used_up else "다시 뽑기",
                        key=f"reroll_{i}",
                        disabled=used_up,
                        use_container_width=True,
                    ):
                        used = [c["region"]["name"] for c in st.session_state.cards]
                        fresh = geo.draw_regions(pool, count=1, exclude_names=used)
                        if not fresh:
                            st.warning("더 뽑을 지역이 없습니다.")
                        else:
                            new_card = make_card(fresh[0])
                            new_card["opened"] = True
                            new_card["rerolled"] = True   # 리롤은 카드당 1번
                            st.session_state.cards[i] = new_card
                            st.rerun()

                with act_right:
                    if st.button("이걸로 결정", key=f"pick_{i}",
                                 type="primary", use_container_width=True):
                        confirm(card, origin)
                        st.rerun()


else:
    # ----- 다트 모드 -----
    # 지도 그리기·당기기·비행·판정은 전부 브라우저(canvas)에서 돈다.
    # 파이썬은 "어느 시군구에 꽂혔는지"만 돌려받는다.
    result = dart_canvas(
        poolNames=sorted(pool["name"].tolist()),
        resetToken=st.session_state.dart_reset,
        theme={
            "sea": "#0B0F1C", "pad": "#070A14",
            "landOn": "#26355C", "landOff": "#151C2E",
            "line": "#3A4468", "gold": "#E9B949", "goldSoft": "#F5D98A",
            "goldDark": "#C9963A", "gray": "#5A6484",
            "text": "#EDEBE4", "muted": "#7A8199",
        },
        key="dart_canvas",
        default=None,
    )

    # 새로 던진 결과가 왔을 때만 처리한다 (같은 값이 반복해서 들어오므로)
    if result and result.get("throwId") != st.session_state.dart_throw_id:
        st.session_state.dart_throw_id = result["throwId"]
        lat, lng = from_canvas(result["x"], result["y"])
        row = pool[pool["name"] == result.get("name")]
        st.session_state.dart = {
            "point": (lat, lng),
            "region": row.iloc[0].to_dict() if len(row) else None,
            "land": result.get("land"),      # 조건 밖 지역이면 이름이 들어온다
        }

    dart = st.session_state.dart

    if dart is None:
        st.info("아래쪽 다트를 **아래로 당겼다가 놓으세요.** 당긴 만큼 멀리 날아갑니다. 밝은 지역이 조건에 맞는 곳입니다.")

    elif dart["region"] is None:
        # 조건 밖 지역이거나 바다 — 이때는 다시 던지기 무제한
        outside = dart.get("land")
        if outside:
            head = f'<div class="place miss">{outside}</div>'
            note = '<div class="note">조건에 맞지 않는 지역입니다. 다시 던져 주세요.</div>'
        else:
            head = '<div class="place miss">바다에 빠졌다</div>'
            note = '<div class="note">아무 데도 안 꽂혔습니다. 다시 던져 주세요.</div>'

        st.markdown(
            '<div class="dart-result">'
            + head + note
            + f'<div class="coord">{dart["point"][0]}, {dart["point"][1]}</div>'
            + '</div>',
            unsafe_allow_html=True,
        )
        if st.button("다시 던지기", key="dart_miss_retry",
                     type="primary", use_container_width=True):
            st.session_state.dart = None
            st.session_state.dart_reset += 1
            st.rerun()

    else:
        r = dart["region"]
        lat, lng = dart["point"]
        st.markdown(
            '<div class="dart-result">'
            f'<div class="place">{r["name"]}</div>'
            f'<div class="note">출발지에서 약 {int(r["distance_km"])}km · '
            f'{geo.grade_of(r["distance_km"])}</div>'
            f'<div class="coord">{lat}, {lng}</div>'
            '</div>',
            unsafe_allow_html=True,
        )

        left, right = st.columns([1, 1])
        with left:
            if st.button("다시 던지기", key="dart_retry", use_container_width=True):
                st.session_state.dart = None
                st.session_state.dart_reset += 1
                st.rerun()
        with right:
            if st.button("이걸로 결정", key="dart_pick",
                         type="primary", use_container_width=True):
                confirm(dart, origin)
                st.rerun()