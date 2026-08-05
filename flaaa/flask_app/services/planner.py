"""P2 여행 플래너의 TourAPI → RAG(임베딩 검색) → LLM 일정 생성 파이프라인.

원래 views/page2_planner.py(Streamlit)의 비공개(_-prefixed) 함수들과 동일한
로직이며, st.session_state/st.cache_data 의존을 없애고 인자로 모든 걸
주고받게 바꿨다. Flask 라우트(flask_app/routes/planner.py)에서 호출한다.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta

import numpy as np
from huggingface_hub import InferenceClient
from pydantic import ValidationError

from flask_app.services.cache import cache
from services.planner_rag import RELATIONSHIP_KEYWORDS, build_search_query, rank_candidates
from services.planner_schemas import TravelPlan, strict_response_format
from services.tour_api import fetch_place_candidates


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
    }


@cache.memoize(timeout=21600)
def tour_candidates(sido: str, sigungu: str, name: str, start: date, end: date, rows: int, api_key: str) -> list[dict]:
    return fetch_place_candidates({"sido": sido, "sigungu": sigungu, "name": name}, start, end, api_key, rows)


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

    candidates = tour_candidates(
        region["sido"], region.get("sigungu", ""), region["name"], start, end,
        int(os.getenv("TOUR_API_ROWS_PER_TYPE", "6")), tour_key,
    )
    query = build_search_query(profile)
    model = os.getenv("HF_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")

    def embed(text: str) -> np.ndarray:
        return np.asarray(_embed_cached(text, model, hf_token), dtype=np.float32)

    ranked, mode = rank_candidates(candidates, query, embed, int(os.getenv("RAG_TOP_K", "12")))
    if not ranked:
        raise ValueError("이 지역에서 일정 후보 장소를 찾지 못했습니다.")
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
규칙: 후보 밖의 장소를 지어내지 말 것. itinerary는 날짜 목록을 같은 순서로 정확히 한 번씩 모두 포함할 것. 가까운 장소를 묶고, 이동·식사·휴식과 1인 기준 추정비용을 반영할 것. 축제는 여행 기간과 겹칠 때만 사용한다."""


def validate_plan(plan: TravelPlan, dates: list[str], allowed_ids: set[str]) -> None:
    actual = [day.date for day in plan.itinerary]
    if actual != dates:
        raise ValueError(f"LLM 일정이 여행 날짜 전체를 포함하지 않았습니다. 기대: {', '.join(dates)} / 응답: {', '.join(actual)}")
    invalid = {item.content_id for day in plan.itinerary for item in day.items if item.content_id not in allowed_ids}
    if invalid:
        raise ValueError(f"TourAPI 후보에 없는 장소가 포함되었습니다: {', '.join(sorted(invalid))}")


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


def generate_plan(region: dict, profile: dict, start: date, end: date) -> dict:
    """전체 파이프라인: TourAPI 후보 조회 → LLM 호출(최대 2회, 검증 실패 시 재시도).

    성공하면 {"plan": dict, "candidates": [...], "query": str, "mode": str}를 돌려주고,
    실패하면 ValueError/ValidationError를 그대로 올린다 (호출부인 Flask 라우트가 처리).
    """
    candidates, query, mode = retrieve_candidates(region, profile, start, end)
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_TOKEN", "")
    if not token:
        raise ValueError(".env에 HF_TOKEN을 설정해 주세요.")

    dates = [d.isoformat() for d in days_between(start, end)]
    messages = [
        {"role": "system", "content": "TourAPI 근거만 사용하고 JSON schema만 출력하세요."},
        {"role": "user", "content": build_prompt(profile, candidates, dates)},
    ]
    client = InferenceClient(provider=os.getenv("HF_LLM_PROVIDER", "nscale"), api_key=token)
    allowed_ids = {item["content_id"] for item in candidates}

    last_error: Exception | None = None
    for attempt in range(2):
        result = client.chat_completion(
            model=os.getenv("HF_MODEL", "Qwen/Qwen3-32B"),
            messages=messages,
            response_format=strict_response_format([item["content_id"] for item in candidates], dates),
            max_tokens=min(8000, max(3500, len(dates) * 1000)),
            temperature=0.3 if attempt == 0 else 0.1,
        )
        text = result.choices[0].message.content or ""
        try:
            plan = parse_llm_plan(text)
            validate_plan(plan, dates, allowed_ids)
            return {"plan": plan.model_dump(mode="json"), "candidates": candidates, "query": query, "mode": mode}
        except (ValidationError, ValueError) as error:
            last_error = error
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"검증 실패: {error}. 누락 없이 모든 날짜를 순서대로 포함한 전체 JSON만 다시 출력하세요."},
            ]
    raise ValueError(f"재생성 뒤에도 일정 검증에 실패했습니다: {last_error}")
