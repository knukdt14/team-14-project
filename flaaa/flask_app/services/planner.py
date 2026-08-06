"""P2 여행 플래너의 TourAPI → RAG(임베딩 검색) → LLM 일정 생성 파이프라인.

원래 views/page2_planner.py(Streamlit)의 비공개(_-prefixed) 함수들과 동일한
로직이며, st.session_state/st.cache_data 의존을 없애고 인자로 모든 걸
주고받게 바꿨다. Flask 라우트(flask_app/routes/planner.py)에서 호출한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta

import numpy as np
import requests
from huggingface_hub import InferenceClient
from pydantic import ValidationError

from flask_app.services.cache import cache
from services.planner_rag import RELATIONSHIP_KEYWORDS, build_search_query, rank_candidates
from services.planner_schemas import TravelPlan, strict_response_format
from services.tmap_transit import TmapTransitError, fetch_air_travel_info
from services.tour_api import fetch_place_candidates

logger = logging.getLogger(__name__)
<<<<<<< Updated upstream

=======
>>>>>>> Stashed changes
TOUR_SIDO_COMPATIBILITY = {"전라남도": "전라북도"}


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def build_profile(region: dict, form: dict) -> dict:
    days = days_between(form["start"], form["end"])
    return {
        "destination": region["name"],
        "departure": form["departure"],
        "count": form["count"],
        "relationship": form["relationship"],
        "nights": len(days) - 1,
        "days": len(days),
        "transportation": form["transport"],
        "budget_per_person": form["budget"],
        "styles": form["styles"],
        "preferences": form["preferences"],
        "flight_operation": form.get("flight_operation"),
    }


def load_flight_info(region: dict, origin: dict, start: date, transport: str, enabled: bool) -> dict | None:
<<<<<<< Updated upstream
    """Retrieve route facts for air travel only; never let the LLM invent them."""
=======
>>>>>>> Stashed changes
    if transport != "항공":
        return None
    if not enabled:
        return {"available": False, "disabled": True, "message": "TMAP transit lookup is disabled."}
    try:
<<<<<<< Updated upstream
        return fetch_air_travel_info(
            float(origin["latitude"]), float(origin["longitude"]),
            float(region["latitude"]), float(region["longitude"]), start,
            os.getenv("TMAP_APP_KEY", ""),
        )
=======
        return fetch_air_travel_info(float(origin["latitude"]), float(origin["longitude"]), float(region["latitude"]), float(region["longitude"]), start, os.getenv("TMAP_APP_KEY", ""))
>>>>>>> Stashed changes
    except (KeyError, TypeError, ValueError):
        return {"available": False, "message": "Departure coordinates are unavailable."}
    except TmapTransitError as error:
        return {"available": False, "message": str(error)}


@cache.memoize(timeout=21600)
<<<<<<< Updated upstream
def tour_candidates(
    sido: str, sigungu: str, name: str, latitude: float | None, longitude: float | None,
    start: date, end: date, rows: int, api_key: str,
) -> list[dict]:
    """Pass P1 coordinates through so TourAPI's nearby-place fallback can run."""
    return fetch_place_candidates(
        {"sido": sido, "sigungu": sigungu, "name": name, "lat": latitude, "lng": longitude},
        start, end, api_key, rows,
    )
=======
def tour_candidates(sido: str, sigungu: str, name: str, latitude: float | None, longitude: float | None, start: date, end: date, rows: int, api_key: str) -> list[dict]:
    return fetch_place_candidates({"sido": sido, "sigungu": sigungu, "name": name, "lat": latitude, "lng": longitude}, start, end, api_key, rows)
>>>>>>> Stashed changes


@cache.memoize(timeout=86400)
def _embed_cached(text: str, model: str, token: str) -> list[float]:
    client = InferenceClient(provider="hf-inference", api_key=token)
    vector = client.feature_extraction(text, model=model)
    return np.asarray(vector, dtype=np.float32).tolist()


def retrieve_candidates(region: dict, profile: dict, start: date, end: date) -> tuple[list[dict], str, str]:
    tour_key = os.getenv("TOUR_API_KEY") or os.getenv("TOUR_API_SERVICE_KEY", "")
    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_TOKEN", "")
    if not tour_key:
        raise ValueError(".env에 TOUR_API_KEY를 설정해 주세요.")
    if not hf_token:
        raise ValueError(".env에 HF_TOKEN을 설정해 주세요.")

<<<<<<< Updated upstream
    # The shared TourAPI adapter currently has no "전라남도" label in its
    # area-code table although code 46 is the Jeonnam administrative area.
    # Use its existing code-46 lookup label only at this planner boundary.
    tour_sido = TOUR_SIDO_COMPATIBILITY.get(region["sido"], region["sido"])
    # The shared adapter cannot resolve Jeonnam city/county names under its
    # compatibility label. Search the province and let RAG rank by destination.
    tour_sigungu = "" if tour_sido != region["sido"] else region.get("sigungu", "")
    tour_name = "" if tour_sido != region["sido"] else region["name"]
    candidates = tour_candidates(
        tour_sido, tour_sigungu, tour_name,
        region.get("latitude"), region.get("longitude"), start, end,
        int(os.getenv("TOUR_API_ROWS_PER_TYPE", "6")), tour_key,
    )
    logger.warning(
        "[Planner TourAPI] selected=%s/%s request=%s/%s candidates=%d",
        region["sido"], region.get("sigungu", ""), tour_sido, tour_sigungu, len(candidates),
    )
=======
    tour_sido = TOUR_SIDO_COMPATIBILITY.get(region["sido"], region["sido"])
    tour_sigungu = "" if tour_sido != region["sido"] else region.get("sigungu", "")
    tour_name = "" if tour_sido != region["sido"] else region["name"]
    candidates = tour_candidates(
        tour_sido, tour_sigungu, tour_name, region.get("latitude"), region.get("longitude"), start, end,
        int(os.getenv("TOUR_API_ROWS_PER_TYPE", "6")), tour_key,
    )
    logger.warning("[Planner TourAPI] selected=%s/%s candidates=%d", region["sido"], region.get("sigungu", ""), len(candidates))
>>>>>>> Stashed changes
    query = build_search_query(profile)
    model = os.getenv("HF_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")

    def embed(text: str) -> np.ndarray:
        return np.asarray(_embed_cached(text, model, hf_token), dtype=np.float32)

    ranked, mode = rank_candidates(candidates, query, embed, int(os.getenv("RAG_TOP_K", "12")))
<<<<<<< Updated upstream
    logger.warning(
        "[Planner RAG] query=%r input=%d ranked=%d mode=%s",
        query, len(candidates), len(ranked), mode,
    )
    if not ranked:
        raise ValueError(
            f"이 지역에서 일정 후보 장소를 찾지 못했습니다. "
            f"(TourAPI 후보 {len(candidates)}개, RAG 후보 {len(ranked)}개)"
        )
=======
    logger.warning("[Planner RAG] input=%d ranked=%d mode=%s", len(candidates), len(ranked), mode)
    if not ranked:
        raise ValueError(f"이 지역에서 일정 후보 장소를 찾지 못했습니다. (TourAPI 후보 {len(candidates)}개, RAG 후보 {len(ranked)}개)")
>>>>>>> Stashed changes
    return ranked, query, mode


def build_prompt(profile: dict, candidates: list[dict], dates: list[str]) -> str:
    allowed = [
        {key: item.get(key) for key in ("content_id", "name", "category", "address", "latitude", "longitude", "overview", "event_start", "event_end")}
        for item in candidates
    ]
    return f"""당신은 국내 여행 동선 전문가입니다. 반드시 아래 TourAPI 후보만 사용해 JSON 스키마에 맞는 일정을 만드세요.
여행 조건: {json.dumps(profile, ensure_ascii=False)}
여행 날짜: {json.dumps(dates, ensure_ascii=False)}
관계별 선호 키워드: {json.dumps(RELATIONSHIP_KEYWORDS.get(profile['relationship'], []), ensure_ascii=False)}
TourAPI 후보: {json.dumps(allowed, ensure_ascii=False)}
<<<<<<< Updated upstream
비용 기준: 모든 estimated_cost, daily_budget, total_estimated_cost는 1인 기준이다. total_estimated_cost는 budget_per_person를 절대 초과하지 말고, 여행 인원(count)을 곱해 총예산 한도로 사용하지 말 것.
=======
비용 기준: 모든 비용은 1인 기준이며 total_estimated_cost는 budget_per_person를 초과하지 않는다.
>>>>>>> Stashed changes
규칙: 후보 밖의 장소를 지어내지 말 것. itinerary는 날짜 목록을 같은 순서로 정확히 한 번씩 모두 포함할 것. 가까운 장소를 묶고, 이동·식사·휴식과 1인 기준 추정비용을 반영할 것. 축제는 여행 기간과 겹칠 때만 사용한다."""


def validate_plan(plan: TravelPlan, dates: list[str], allowed_ids: set[str], budget_per_person: int) -> None:
    actual = [day.date for day in plan.itinerary]
    if actual != dates:
        raise ValueError(f"LLM 일정이 여행 날짜 전체를 포함하지 않았습니다. 기대: {', '.join(dates)} / 응답: {', '.join(actual)}")
    invalid = {item.content_id for day in plan.itinerary for item in day.items if item.content_id not in allowed_ids}
    if invalid:
        raise ValueError(f"TourAPI 후보에 없는 장소가 포함되었습니다: {', '.join(sorted(invalid))}")
    if plan.total_estimated_cost > budget_per_person:
<<<<<<< Updated upstream
        raise ValueError(
            f"1인 예상 총비용 {plan.total_estimated_cost:,}원이 1인 예산 {budget_per_person:,}원을 초과했습니다."
        )
=======
        raise ValueError(f"1인 예상 총비용 {plan.total_estimated_cost:,}원이 1인 예산을 초과했습니다.")
>>>>>>> Stashed changes


def parse_llm_plan(content: str) -> TravelPlan:
    """Decode JSON output defensively when a provider emits an invalid escape."""
    payload = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", payload, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        payload = fenced.group(1)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", payload)
        data = json.loads(repaired)
    return TravelPlan.model_validate(data)


def _upstage_completion(messages: list[dict], max_tokens: int, temperature: float) -> str:
<<<<<<< Updated upstream
    """Call Solar Pro 4 through Upstage's OpenAI-compatible Chat API."""
    api_key = os.getenv("UPSTAGE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("UPSTAGE_API_KEY를 .env에 설정해 주세요.")
    base_url = os.getenv("UPSTAGE_BASE_URL", "https://api.upstage.ai/v1").rstrip("/")
    payload = {
        "model": os.getenv("UPSTAGE_MODEL", "solar-pro4"),
        "messages": messages,
        # Solar reliably returns a body with json_object.  The detailed
        # Pydantic schema is still enforced locally after every response.
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Solar Pro 4 otherwise may spend the full completion budget on
        # internal reasoning before it emits the required JSON body.
        "reasoning_effort": os.getenv("UPSTAGE_REASONING_EFFORT", "low"),
    }
    try:
        response = requests.post(
            f"{base_url}/chat/completions", json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=120,
        )
=======
    api_key = os.getenv("UPSTAGE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("UPSTAGE_API_KEY를 .env에 설정해 주세요.")
    payload = {"model": os.getenv("UPSTAGE_MODEL", "solar-pro4"), "messages": messages, "response_format": {"type": "json_object"}, "max_tokens": max_tokens, "temperature": temperature, "reasoning_effort": os.getenv("UPSTAGE_REASONING_EFFORT", "low")}
    try:
        response = requests.post(os.getenv("UPSTAGE_BASE_URL", "https://api.upstage.ai/v1").rstrip("/") + "/chat/completions", json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=120)
>>>>>>> Stashed changes
        response.raise_for_status()
        choice = response.json()["choices"][0]
        message = choice["message"]
        content = message.get("content")
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
        detail = response.text[:300] if "response" in locals() else str(error)
        raise ValueError(f"Upstage Solar API 호출에 실패했습니다: {detail}") from error
    if not isinstance(content, str) or not content.strip():
<<<<<<< Updated upstream
        logger.warning(
            "[Planner Upstage] empty content model=%s finish_reason=%s message_keys=%s",
            payload["model"], choice.get("finish_reason"), sorted(message.keys()),
        )
=======
        logger.warning("[Planner Upstage] empty content model=%s finish_reason=%s", payload["model"], choice.get("finish_reason"))
>>>>>>> Stashed changes
        raise ValueError("Upstage Solar API가 빈 응답을 반환했습니다.")
    return content


def generate_plan(region: dict, profile: dict, start: date, end: date) -> dict:
    """전체 파이프라인: TourAPI 후보 조회 → LLM 호출(최대 2회, 검증 실패 시 재시도).

    성공하면 {"plan": dict, "candidates": [...], "query": str, "mode": str}를 돌려주고,
    실패하면 ValueError/ValidationError를 그대로 올린다 (호출부인 Flask 라우트가 처리).
    """
    candidates, query, mode = retrieve_candidates(region, profile, start, end)
    dates = [d.isoformat() for d in days_between(start, end)]
    output_schema = strict_response_format([item["content_id"] for item in candidates], dates)["json_schema"]["schema"]
    messages = [
<<<<<<< Updated upstream
        {
            "role": "system",
            "content": (
                "TourAPI 근거만 사용하세요. 분석이나 설명 없이 유효한 json 객체 하나만 출력하세요. "
                "아래 JSON Schema의 필드명과 구조를 정확히 지키고, schema 밖의 필드는 절대 추가하지 마세요.\n"
                f"JSON Schema: {json.dumps(output_schema, ensure_ascii=False)}"
            ),
        },
=======
        {"role": "system", "content": f"TourAPI 근거만 사용하세요. 설명 없이 json 객체 하나만 출력하세요. 아래 JSON Schema 필드와 구조만 사용하세요.\n{json.dumps(output_schema, ensure_ascii=False)}"},
>>>>>>> Stashed changes
        {"role": "user", "content": build_prompt(profile, candidates, dates)},
    ]
    allowed_ids = {item["content_id"] for item in candidates}

    last_error: Exception | None = None
    for attempt in range(2):
        try:
<<<<<<< Updated upstream
            text = _upstage_completion(
                messages,
                min(12_000, max(8_000, len(dates) * 1_500)),
                0.3 if attempt == 0 else 0.1,
            )
        except ValueError as error:
            last_error = error
            if attempt == 0:
                logger.warning("[Planner Upstage] first request failed; retrying once: %s", error)
                messages.append({"role": "user", "content": "응답 본문이 비어 있었습니다. 앞서 제공한 JSON Schema 그대로 JSON만 다시 출력하세요."})
=======
            text = _upstage_completion(messages, min(12_000, max(8_000, len(dates) * 1_500)), 0.3 if attempt == 0 else 0.1)
        except ValueError as error:
            last_error = error
            if attempt == 0:
                messages.append({"role": "user", "content": "JSON 본문이 비어 있었습니다. JSON만 다시 출력하세요."})
>>>>>>> Stashed changes
                continue
            raise
        try:
            plan = parse_llm_plan(text)
            validate_plan(plan, dates, allowed_ids, int(profile["budget_per_person"]))
            return {"plan": plan.model_dump(mode="json"), "candidates": candidates, "query": query, "mode": mode}
        except (ValidationError, ValueError) as error:
            last_error = error
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"검증 실패: {error}. 앞서 제공한 JSON Schema의 필드명과 구조를 정확히 사용해 전체 JSON만 다시 출력하세요."},
            ]
    raise ValueError(f"재생성 뒤에도 일정 검증에 실패했습니다: {last_error}")
