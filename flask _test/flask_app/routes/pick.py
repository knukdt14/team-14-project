"""P1. 여행지 추첨.

전국 시군구에서 무작위로 3곳을 뽑아 카드로 보여준다. 카드는 덮여 있고, 눌러서
연다. 카드마다 리롤은 1번씩. 하나를 확정하면 trip_context에 기록된다.

Streamlit 버전(views/page1_pick.py)과 달리 필터(출발지/거리/조건)는 폼 hidden
필드로 매 요청마다 함께 실려 다닌다 — 세션에 몰래 남아있는 "지금 필터 값"이
없어서, 뽑았을 때의 조건과 리롤/확정 시점의 조건이 항상 일치한다.
"""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from flask_app.state import set_trip_context_value
from services import geo

pick_bp = Blueprint("pick", __name__)

DISTANCE_OPTIONS = ["100km", "200km", "300km", "제한 없음"]
GRADE_COLORS = {
    "가벼움": "#8a94a6",
    "괜찮음": "#4aa3d9",
    "본격적": "#9b7bd9",
    "대장정": "#ff6b57",
}


def _regions():
    return geo.load_regions()


def _boundaries():
    return geo.load_boundaries()


def _filters(source) -> dict:
    """source는 request.args(GET) 또는 request.form(POST)."""
    regions = _regions()
    names = sorted(regions["name"].tolist())
    origin_name = source.get("origin") or "대구"
    if origin_name not in names:
        origin_name = names[0]
    distance = source.get("distance") or "300km"
    if distance not in DISTANCE_OPTIONS:
        distance = "300km"
    coastal_only = source.get("coastal") == "1"
    exclude_metro = source.get("exclude_metro", "1") == "1"
    return {
        "origin_name": origin_name,
        "distance": distance,
        "coastal_only": coastal_only,
        "exclude_metro": exclude_metro,
    }


def _pool(filters: dict):
    regions = _regions()
    origin_row = regions[regions["name"] == filters["origin_name"]].iloc[0]
    max_km = None if filters["distance"] == "제한 없음" else int(filters["distance"].replace("km", ""))
    pool = geo.filter_regions(
        regions,
        origin=origin_row,
        max_km=max_km,
        coastal_only=filters["coastal_only"],
        exclude_metro=filters["exclude_metro"],
    )
    return origin_row, pool


def _filter_qs(filters: dict) -> str:
    return (
        f"?origin={filters['origin_name']}&distance={filters['distance']}"
        f"&coastal={'1' if filters['coastal_only'] else '0'}"
        f"&exclude_metro={'1' if filters['exclude_metro'] else '0'}"
    )


def _make_card(region: dict, boundaries: dict) -> dict:
    lat, lng = geo.pick_point(region, boundaries)
    return {
        "region": region,
        "point": [lat, lng],
        "opened": False,
        "rerolled": False,
        "grade": geo.grade_of(region["distance_km"]),
    }


@pick_bp.route("/")
def index():
    filters = _filters(request.args)
    origin_row, pool = _pool(filters)
    regions = _regions()

    cards = session.get("cards", [])
    confirmed = session.get("confirmed")

    return render_template(
        "pick.html",
        active_step="pick",
        names=sorted(regions["name"].tolist()),
        distance_options=DISTANCE_OPTIONS,
        filters=filters,
        filter_qs=_filter_qs(filters),
        pool_count=len(pool),
        can_draw=len(pool) >= 3,
        cards=cards,
        confirmed=confirmed,
        grade_colors=GRADE_COLORS,
    )


@pick_bp.route("/draw", methods=["POST"])
def draw():
    filters = _filters(request.form)
    _, pool = _pool(filters)
    boundaries = _boundaries()

    if len(pool) < 3:
        flash("후보가 3곳 미만입니다. 조건을 조금 풀어 주세요.", "warning")
    else:
        picked = geo.draw_regions(pool, count=3)
        session["cards"] = [_make_card(r.to_dict(), boundaries) for r in picked]
        session["confirmed"] = None
        session.modified = True

    return redirect(url_for("pick.index") + _filter_qs(filters))


@pick_bp.route("/open/<int:idx>", methods=["POST"])
def open_card(idx: int):
    filters = _filters(request.form)
    cards = session.get("cards", [])
    if 0 <= idx < len(cards):
        cards[idx]["opened"] = True
        session["cards"] = cards
        session.modified = True
    return redirect(url_for("pick.index") + _filter_qs(filters))


@pick_bp.route("/reroll/<int:idx>", methods=["POST"])
def reroll(idx: int):
    filters = _filters(request.form)
    _, pool = _pool(filters)
    boundaries = _boundaries()
    cards = session.get("cards", [])

    if 0 <= idx < len(cards) and not cards[idx]["rerolled"]:
        used = [c["region"]["name"] for c in cards]
        fresh = geo.draw_regions(pool, count=1, exclude_names=used)
        if not fresh:
            flash("더 뽑을 지역이 없습니다.", "warning")
        else:
            new_card = _make_card(fresh[0].to_dict(), boundaries)
            new_card["opened"] = True
            new_card["rerolled"] = True
            cards[idx] = new_card
            session["cards"] = cards
            session.modified = True

    return redirect(url_for("pick.index") + _filter_qs(filters))


@pick_bp.route("/confirm/<int:idx>", methods=["POST"])
def confirm(idx: int):
    filters = _filters(request.form)
    origin_row, _ = _pool(filters)
    cards = session.get("cards", [])

    if 0 <= idx < len(cards):
        card = cards[idx]
        session["confirmed"] = card
        session.modified = True
        r = card["region"]
        lat, lng = card["point"]
        set_trip_context_value(
            "region",
            {
                "sido": r["sido"],
                "sigungu": r["sigungu"],
                "name": r["name"],
                "latitude": lat,
                "longitude": lng,
                "distance_km": int(r["distance_km"]),
                "locked": True,
            },
        )
        set_trip_context_value(
            "origin",
            {
                "name": origin_row["name"],
                "latitude": float(origin_row["lat"]),
                "longitude": float(origin_row["lng"]),
            },
        )

    return redirect(url_for("pick.index") + _filter_qs(filters))


@pick_bp.route("/restart", methods=["POST"])
def restart():
    session.pop("cards", None)
    session.pop("confirmed", None)
    session.pop("trip_context", None)
    session.pop("favorites", None)
    return redirect(url_for("pick.index"))
