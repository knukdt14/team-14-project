"""
페이지들이 같이 쓰는 작은 도구들.

아직 안 만든 페이지도 '앞 페이지에서 뭘 물려받는지'는 보여줘야
담당자가 바로 이어서 작업할 수 있다.
"""

import streamlit as st


def page_header(title, subtitle=""):
    st.markdown(f'<div class="page-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="page-sub">{subtitle}</div>', unsafe_allow_html=True)


def require_region():
    """여행지가 확정돼 있어야 진행할 수 있는 페이지에서 쓴다.

    확정 전이면 안내를 띄우고 None을 돌려준다.
    """
    region = st.session_state.get("trip_context", {}).get("region")

    if not region:
        st.warning("먼저 여행지를 정해 주세요. 추첨을 마쳐야 이 단계로 넘어올 수 있습니다.")
        if st.button("여행지 추첨으로 가기", type="primary"):
            st.switch_page("views/page1_pick.py")
        return None

    return region


def show_inherited(region):
    """앞 페이지에서 넘어온 값을 보여준다."""
    left, center, right = st.columns(3)
    left.metric("여행지", region["name"])
    center.metric("출발지 거리", f"{region['distance_km']}km")
    right.metric("좌표", f"{region['latitude']:.3f}, {region['longitude']:.3f}")


def todo_panel(owner, items, context_note=""):
    """아직 구현 안 된 페이지에 붙이는 작업 안내판."""
    with st.container(border=True):
        st.markdown(f"**담당: {owner}**")
        st.caption("이 페이지는 아직 준비 중입니다. 아래 항목을 채우면 됩니다.")
        for item in items:
            st.markdown(f"- {item}")
        if context_note:
            st.divider()
            st.caption(context_note)
