"""여러 라우트가 공유하는 trip_context(session) 도우미.

Streamlit 버전의 st.session_state.trip_context / views/_common.py의
require_region()에 대응한다.
"""

from functools import wraps

from flask import flash, redirect, session, url_for


def get_trip_context() -> dict:
    return session.setdefault("trip_context", {})


def set_trip_context_value(key: str, value) -> None:
    ctx = session.setdefault("trip_context", {})
    ctx[key] = value
    session["trip_context"] = ctx
    session.modified = True


def region_required(view):
    """여행지가 확정돼 있어야 진행할 수 있는 라우트에 붙인다."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        region = get_trip_context().get("region")
        if not region:
            flash("먼저 여행지를 정해 주세요. 추첨을 마쳐야 이 단계로 넘어올 수 있습니다.", "warning")
            return redirect(url_for("pick.index"))
        return view(*args, **kwargs)

    return wrapper
