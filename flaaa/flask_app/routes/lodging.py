"""P3. 주변 숙소.

Streamlit 버전(views/page3_lodging.py)과 달리 필터/숙박일 상태를 전부
쿼리스트링으로 표현한다. 그래서 "이전 밤"이나 "다음 밤으로 자동 이동" 같은
동작이 그냥 리다이렉트 URL 하나로 끝난다 — Streamlit에서 겪었던
"위젯이 이미 만들어진 뒤 session_state를 못 바꾼다" 문제 자체가 없다.
"""

import csv
import io
import random
from collections import Counter
from urllib.parse import parse_qs, urlencode

import requests
from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for

from flask_app.state import get_trip_context, region_required
from flask_app.services import lodging as lodging_service

lodging_bp = Blueprint("lodging", __name__, url_prefix="/lodging")


def _route_options(plan: dict) -> list[dict]:
    options = []
    for night in lodging_service.build_night_options(plan):
        center = lodging_service.night_center(night)
        if center:
            options.append({**night, "center": center})
    return options


def _resolve_search(route_options: list[dict], region: dict) -> dict:
    mode = request.args.get("mode") or ("route" if route_options else "manual")
    if mode == "route" and not route_options:
        mode = "manual"

    radius = request.args.get("radius", type=int) or 1000
    if radius not in lodging_service.RADIUS_OPTIONS:
        radius = 1000

    result = {
        "mode": mode,
        "radius": radius,
        "query": "",
        "error": None,
        "places": [],
        "center": None,
        "selected_night": None,
        "night_key": "manual",
        "night_label": "",
    }

    if mode == "route":
        night_idx = request.args.get("night", type=int) or 0
        night_idx = max(0, min(night_idx, len(route_options) - 1))
        selected_night = route_options[night_idx]
        anchors = lodging_service.night_anchors(selected_night)
        result["selected_night"] = selected_night
        result["center"] = selected_night["center"]
        result["night_key"] = f"night_{selected_night['night_index']}"
        result["night_label"] = lodging_service.night_option_label(selected_night)
        try:
            result["places"] = lodging_service.search_lodging_for_anchors(anchors, radius)
        except requests.RequestException as exc:
            result["error"] = f"카카오 API 호출 중 오류가 발생했습니다: {exc}"
    else:
        # 첫 진입(아직 q 파라미터가 없고 동선 후보도 없음)에는 확정 여행지로 자동 검색.
        if request.args.get("q") is None and not route_options:
            query = region["name"]
        else:
            query = (request.args.get("q") or "").strip()
        result["query"] = query
        result["night_label"] = f"'{query}' 직접 검색" if query else ""
        if query:
            try:
                center = lodging_service.geocode_location(query, lodging_service.KAKAO_API_KEY)
            except requests.RequestException as exc:
                result["error"] = f"카카오 API 호출 중 오류가 발생했습니다: {exc}"
                center = None
            if center is None and result["error"] is None:
                result["error"] = "입력하신 위치를 찾을 수 없습니다. 다른 표현으로 다시 시도해보세요."
            elif center:
                result["center"] = center
                try:
                    result["places"] = lodging_service.search_lodging_for_anchors([center], radius)
                except requests.RequestException as exc:
                    result["error"] = f"카카오 API 호출 중 오류가 발생했습니다: {exc}"

    return result


def _annotate_types(places: list[dict]) -> None:
    """place dict에 유형(kind)을 미리 붙여서 템플릿에서 서비스 함수를 몰라도 되게 한다."""
    for place in places:
        place["kind"] = lodging_service.classify_lodging_type(place)


def _selected_types(all_places: list[dict]) -> tuple[list[str], list[str]]:
    type_options = sorted({p["kind"] for p in all_places})
    type_param = request.args.get("type")
    selected = [t for t in type_param.split(",") if t] if type_param is not None else type_options
    return type_options, selected


def _filter_sort(places: list[dict], selected_types: list[str], sort_by: str) -> list[dict]:
    filtered = [p for p in places if p["kind"] in selected_types]
    if sort_by == "name":
        filtered.sort(key=lambda p: p.get("place_name", ""))
    else:
        filtered.sort(key=lambda p: p.get("distance") or 0)
    return filtered


def _query_url(endpoint: str = "lodging.index", **overrides) -> str:
    args = request.args.to_dict(flat=True)
    args.update(overrides)
    args = {k: v for k, v in args.items() if v not in (None, "")}
    return f"{url_for(endpoint)}?{urlencode(args)}" if args else url_for(endpoint)


def _gather(ctx: dict) -> dict:
    """index()/slot() 둘 다 필요한 검색·필터 결과를 한 번만 계산해서 공유한다."""
    region = ctx["region"]
    plan = ctx.get("plan") or {}
    route_options = _route_options(plan)
    result = _resolve_search(route_options, region)
    all_places = result["places"]
    _annotate_types(all_places)

    type_options, selected_types = _selected_types(all_places)
    sort_by = request.args.get("sort", "distance")
    filtered = _filter_sort(all_places, selected_types, sort_by)

    type_filter_links = []
    for t in type_options:
        active = t in selected_types
        new_selected = [x for x in selected_types if x != t] if active else selected_types + [t]
        type_filter_links.append(
            {"label": t, "active": active, "url": _query_url("lodging.index", type=",".join(new_selected))}
        )

    distances = [p["distance"] for p in all_places if p.get("distance")]
    night_options_data = [
        {"index": n["night_index"], "label": lodging_service.night_option_label(n)} for n in route_options
    ]

    return dict(
        route_options=route_options,
        night_options_data=night_options_data,
        mode=result["mode"],
        radius=result["radius"],
        radius_options=lodging_service.RADIUS_OPTIONS,
        query=result["query"],
        error=result["error"],
        center=result["center"],
        selected_night=result["selected_night"],
        night_key=result["night_key"],
        night_label=result["night_label"],
        all_places_count=len(all_places),
        min_distance=(int(min(distances)) if distances else None),
        avg_distance=(int(sum(distances) / len(distances)) if distances else None),
        type_counts=Counter(p["kind"] for p in all_places),
        type_filter_links=type_filter_links,
        sort_by=sort_by,
        filtered=filtered,
        lodging_map=ctx.get("lodging", {}),
        favorites=session.get("favorites", {}),
        type_badge_colors=lodging_service.TYPE_BADGE_COLORS,
        type_icons=lodging_service.TYPE_ICONS,
        total_nights=len(route_options),
    )


@lodging_bp.route("/")
@region_required
def index():
    ctx = get_trip_context()

    if not lodging_service.KAKAO_API_KEY:
        return render_template("lodging.html", active_step="lodging", api_key_missing=True)

    data = _gather(ctx)
    place_images = lodging_service.fetch_place_images([p.get("place_url") for p in data["filtered"]])
    slot_url = _query_url("lodging.slot")

    return render_template(
        "lodging.html",
        active_step="lodging",
        api_key_missing=False,
        place_images=place_images,
        slot_url=slot_url,
        qs=_query_url,
        **data,
    )


@lodging_bp.route("/slot")
@region_required
def slot():
    ctx = get_trip_context()

    if not lodging_service.KAKAO_API_KEY:
        return redirect(url_for("lodging.index"))

    spin_seed = request.args.get("spin", type=int)
    if spin_seed is None:
        return redirect(_query_url("lodging.slot", spin=random.randint(1, 999_999)))

    data = _gather(ctx)
    rng = random.Random(spin_seed)
    slot_picks = rng.sample(data["filtered"], k=min(3, len(data["filtered"]))) if data["filtered"] else []
    place_images = lodging_service.fetch_place_images([p.get("place_url") for p in slot_picks])

    return render_template(
        "lodging_slot.html",
        active_step="lodging",
        api_key_missing=False,
        spin=spin_seed,
        slot_picks=slot_picks,
        place_images=place_images,
        list_url=_query_url("lodging.index"),
        qs=lambda **kw: _query_url("lodging.slot", **kw),
        **data,
    )


@lodging_bp.route("/export.csv")
@region_required
def export_csv():
    ctx = get_trip_context()
    region = ctx["region"]
    plan = ctx.get("plan") or {}
    route_options = _route_options(plan)
    result = _resolve_search(route_options, region)
    _annotate_types(result["places"])
    type_options, selected_types = _selected_types(result["places"])
    sort_by = request.args.get("sort", "distance")
    filtered = _filter_sort(result["places"], selected_types, sort_by)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["이름", "유형", "주소", "거리(m)", "전화번호", "카카오맵"])
    for place in filtered:
        writer.writerow(
            [
                place.get("place_name"),
                place.get("kind"),
                place.get("road_address_name") or place.get("address_name"),
                place.get("distance"),
                place.get("phone"),
                place.get("place_url"),
            ]
        )
    csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=nearby_stays.csv"},
    )


def _redirect_endpoint() -> str:
    endpoint = request.form.get("redirect_endpoint", "lodging.index")
    return endpoint if endpoint in ("lodging.index", "lodging.slot") else "lodging.index"


@lodging_bp.route("/favorite", methods=["POST"])
@region_required
def favorite():
    place = {
        "id": request.form.get("id"),
        "place_name": request.form.get("place_name"),
        "road_address_name": request.form.get("address"),
        "phone": request.form.get("phone"),
        "place_url": request.form.get("place_url"),
    }
    fav_key = place.get("id") or place.get("place_url") or place["place_name"]
    favorites = session.setdefault("favorites", {})
    if fav_key in favorites:
        del favorites[fav_key]
    else:
        favorites[fav_key] = place
    session.modified = True
    return redirect(url_for(_redirect_endpoint()) + request.form.get("redirect_qs", ""))


@lodging_bp.route("/select", methods=["POST"])
@region_required
def select():
    ctx = get_trip_context()
    lodgings = ctx.setdefault("lodging", {})
    night_key = request.form.get("night_key", "manual")
    place_name = request.form.get("place_name")
    night_label = request.form.get("night_label", "")

    lodgings[night_key] = {
        "night_label": night_label,
        "name": place_name,
        "type": request.form.get("lodging_type"),
        "address": request.form.get("address"),
        "distance_m": (int(request.form["distance"]) if request.form.get("distance") else None),
        "phone": request.form.get("phone"),
        "place_url": request.form.get("place_url"),
        "latitude": float(request.form["y"]),
        "longitude": float(request.form["x"]),
    }
    session.modified = True
    flash(f"'{place_name}'을(를) {night_label} 숙소로 저장했습니다.", "success")

    redirect_qs = request.form.get("redirect_qs", "")
    total_nights = int(request.form.get("total_nights") or 0)
    if night_key.startswith("night_") and total_nights:
        next_idx = int(night_key.split("_", 1)[1]) + 1
        if next_idx < total_nights:
            parsed = parse_qs(redirect_qs.lstrip("?"))
            parsed["night"] = [str(next_idx)]
            redirect_qs = f"?{urlencode(parsed, doseq=True)}"

    return redirect(url_for(_redirect_endpoint()) + redirect_qs)
