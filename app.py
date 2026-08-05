"""
TripRoll — 국내 랜덤 여행 올인원 서비스

여기가 앱의 시작점이다. 페이지 4개를 사이드바 네비게이션으로 연결한다.
각 페이지 내용은 views/ 폴더에 하나씩 들어 있다.

실행: streamlit run app.py
"""

import streamlit as st

st.set_page_config(
    page_title="TripRoll",
    page_icon="🎴",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# 공통 상태
# ---------------------------------------------------------------------------

if "trip_context" not in st.session_state:
    # 페이지를 지나며 값이 쌓이는 공통 상태
    st.session_state.trip_context = {}


# ---------------------------------------------------------------------------
# 공통 스타일 (모든 페이지에 적용)
# ---------------------------------------------------------------------------

st.markdown(
    """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
html, body, [class*="css"] { font-family: 'Pretendard', sans-serif; }
/* 본문 폭을 적당히 잡아 가운데로 모은다 */
.block-container { max-width: 1080px; padding-top: 2.5rem; padding-bottom: 4rem; }
/* 제목 */
.page-title {
  font-size: 34px; font-weight: 900; color: #EDEBE4;
  letter-spacing: -0.02em; margin-bottom: 6px; text-align: center;
}
.page-sub {
  font-size: 15px; color: #7A8199; margin-bottom: 30px; text-align: center;
}
/* 조건 패널 (st.container(border=True)) */
[data-testid="stVerticalBlockBorderWrapper"] {
  border-color: #232B45 !important;
  border-radius: 16px !important;
  background: #101728;
}

/* 사이드바 */
[data-testid="stSidebar"] { background: #0E1424; border-right: 1px solid #1C2439; }
.side-brand {
  font-size: 20px; font-weight: 900; color: #E9B949;
  letter-spacing: -0.02em; padding: 4px 0 2px;
}
.side-tag { font-size: 12px; color: #6B7290; margin-bottom: 18px; }
.step { font-size: 13px; color: #7A8199; padding: 3px 0; }
</style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# 페이지 정의
# ---------------------------------------------------------------------------

pages = [
    st.Page("views/page1_pick.py", title="여행지 추첨", icon=":material/casino:", default=True),
    st.Page("views/page2_planner.py", title="여행 플래너", icon=":material/map:"),
    st.Page("views/page3_lodging.py", title="주변 숙소", icon=":material/hotel:"),
    st.Page("views/page4_insurance.py", title="여행자 보험", icon=":material/shield:"),
]

nav = st.navigation(pages, position="sidebar")


# ---------------------------------------------------------------------------
# 사이드바 — 진행 상황
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown('<div class="side-brand">TripRoll</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-tag">국내 랜덤 여행 올인원</div>', unsafe_allow_html=True)

    ctx = st.session_state.trip_context
    steps = [
        ("여행지", bool(ctx.get("region"))),
        ("일정", bool(ctx.get("plan"))),
        ("숙소", bool(ctx.get("lodging"))),
        ("보험", bool(ctx.get("insurance"))),
    ]

    st.divider()
    st.caption("진행 상황")
    for label, done in steps:
        mark = "●" if done else "○"
        color = "#E9B949" if done else "#39415C"
        st.markdown(
            f'<div class="step"><span style="color:{color}">{mark}</span> {label}</div>',
            unsafe_allow_html=True,
        )

    if ctx.get("region"):
        st.divider()
        st.caption("선택한 여행지")
        st.markdown(f"**{ctx['region']['name']}**")


nav.run()
