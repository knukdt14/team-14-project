"""P4 여행자 보험 약관 RAG 챗봇 — views/page4_insurance.py(Streamlit)의 로직을 그대로 옮긴다.

rag_chain.py / query_pipeline.py / clause_matcher.py(프로젝트 루트, main 브랜치의
보험 챗봇 PR에서 가져옴)는 원래도 Streamlit을 전혀 모르는 순수 함수라 그대로 재사용한다.

무거운 리소스(임베딩 모델·Chroma 컬렉션·LLM 체인)는 프로세스당 한 번만 로드해서
전역 싱글턴으로 캐싱한다 — Streamlit의 @st.cache_resource와 같은 효과를
functools.lru_cache(maxsize=1)로 낸다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from clause_matcher import is_coverage_article, is_exclusion_article, split_numbered_items  # noqa: F401
from query_pipeline import INTENT_RIDER_MAP, NO_COVERAGE_INTENTS  # noqa: F401
from rag_chain import answer_question, build_chains, load_qwen_llm, parse_intent  # noqa: F401

BASE_DIR = Path(__file__).resolve().parents[3]  # flask/flask_app/services/ -> 프로젝트 루트
CHUNKS_PATH = BASE_DIR / "chunks_output.json"
CHROMA_DIR = BASE_DIR / "chroma_db"

INSURER_DISPLAY_NAMES = {
    "meritz": "메리츠화재",
    "CM8150": "현대해상",
    "inTravel": "삼성화재",
    "프로미다이렉트": "DB손해보험",
}
DISPLAY_TO_CODE = {v: k for k, v in INSURER_DISPLAY_NAMES.items()}
INSURER_HOMEPAGES = {
    "meritz": "https://www.meritzfire.com/",
    "CM8150": "https://direct.hi.co.kr/",
    "inTravel": "https://direct.samsungfire.com/",
    "프로미다이렉트": "https://www.directdb.co.kr/",
}
INTENT_LABELS = {
    "휴대품손해": "가방·캐리어·휴대폰 등 물건 도난/파손",
    "배상책임": "실수로 남에게 피해를 준 경우",
    "실손의료비_상해질병": "여행 중 다치거나 아파서 병원 이용",
    "상해질병_사망후유장해": "사망·후유장해 관련",
    "항공기지연": "비행기 지연/결항",
    "여행취소": "여행 자체를 취소하게 된 경우",
    "여권분실": "여권 분실/도난",
}


class ResourcesUnavailable(RuntimeError):
    """chunks_output.json / chroma_db 등 약관 데이터가 아직 준비되지 않았을 때."""


@lru_cache(maxsize=1)
def load_resources() -> dict:
    if not CHUNKS_PATH.exists():
        raise ResourcesUnavailable(
            f"{CHUNKS_PATH.name}이(가) 없습니다. 약관 데이터를 프로젝트 루트에 준비해 주세요."
        )
    if not CHROMA_DIR.exists():
        raise ResourcesUnavailable(
            f"{CHROMA_DIR.name}이(가) 없습니다. build_index.py를 먼저 실행해 주세요."
        )

    with open(CHUNKS_PATH, encoding="utf-8") as f:
        all_chunks = json.load(f)
    embedder = SentenceTransformer("BAAI/bge-m3")
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_collection("travel_insurance")
    classify_llm, answer_llm = load_qwen_llm()
    intent_chain, rewrite_chain, answer_chain = build_chains(classify_llm, answer_llm)

    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    summary_prompt = ChatPromptTemplate.from_template(
        "다음은 여행자보험 약관 조항입니다. 전문용어 없이, 실제로 뭘 보장하는지만 "
        "일반인이 바로 이해할 수 있는 한 문장으로 쉽게 요약하세요. 조항 번호나 '피보험자' 같은 "
        "말은 쓰지 말고, 자연스러운 한국어 한 문장으로만 답하세요.\n\n[조항]\n{clause}\n\n[한 줄 요약]"
    )
    summary_chain = summary_prompt | classify_llm | StrOutputParser()

    return {
        "all_chunks": all_chunks,
        "embedder": embedder,
        "collection": collection,
        "intent_chain": intent_chain,
        "rewrite_chain": rewrite_chain,
        "answer_chain": answer_chain,
        "summary_chain": summary_chain,
    }


def parse_answer_for_ui(answer: str) -> dict:
    if "보상되지 않습니다" in answer:
        verdict = "not_covered"
    elif "보상됩니다" in answer:
        verdict = "covered"
    else:
        verdict = "unclear"
    raw_clause = re.sub(r"위\s*(조항)?[^\n]*따라.*", "", answer, flags=re.S)
    raw_clause = re.sub(r"보상(되지\s*않습니다|됩니다)\.?", "", raw_clause)
    raw_clause = re.sub(r"\n?\d\)\s*$", "", raw_clause)
    raw_clause = raw_clause.strip(" \n") or "(관련 조항을 찾지 못했습니다)"
    return {"raw_clause": raw_clause, "verdict": verdict}


def build_friendly_text(verdict: str) -> str:
    if verdict == "covered":
        return (
            "이 상황은 <b>보상 대상이에요.</b> 사고 경위와 증빙자료(사진, 진단서, 영수증 등)를 "
            "챙겨서 보험사에 청구하시면 됩니다."
        )
    if verdict == "not_covered":
        return (
            "이 상황은 <b>보상 대상이 아니에요.</b> 위 조항에 나온 면책 사유에 해당하기 때문이에요. "
            "다만 세부 상황에 따라 다를 수 있으니 보험사 상담을 함께 받아보시는 걸 권장해요."
        )
    return "약관만으로는 확실히 판단하기 어려운 상황이에요. 보험사 고객센터에 직접 문의해보시는 걸 권장드려요."


def compare_insurers(intent: str, resources: dict) -> list[dict]:
    """no_insurance_chat 단계의 4개사 비교 카드 데이터를 만든다."""
    all_chunks = resources["all_chunks"]
    summary_chain = resources["summary_chain"]

    rows = []
    for code, display_name in INSURER_DISPLAY_NAMES.items():
        rider_names = INTENT_RIDER_MAP.get(intent, {}).get(code, [])
        matched = [c for c in all_chunks if c["insurer"] == code and c.get("rider") in rider_names]
        coverage_chunk = next((c for c in matched if is_coverage_article(c["article"])), None)
        exclusion_chunk = next((c for c in matched if is_exclusion_article(c["article"])), None)

        n_items = len(split_numbered_items(exclusion_chunk["text"])) if exclusion_chunk else None
        one_liner = None
        if coverage_chunk:
            one_liner = summary_chain.invoke({"clause": coverage_chunk["text"]}).strip()

        rows.append(
            {
                "code": code,
                "display_name": display_name,
                "exclusion_count": n_items,
                "coverage_text": coverage_chunk["text"] if coverage_chunk else None,
                "one_liner": one_liner,
                "homepage": INSURER_HOMEPAGES[code],
            }
        )
    return rows


def broadest_coverage_comment(rows: list[dict]) -> str:
    valid = {row["display_name"]: row["exclusion_count"] for row in rows if row["exclusion_count"] is not None}
    if not valid:
        return "이 항목은 4개사 모두 약관상 명확한 특약이 확인되지 않아, 가입 전 각 사에 직접 확인해보시는 걸 권장드려요."
    broadest = min(valid, key=valid.get)
    return (
        f"약관상 면책 항목 개수만 놓고 보면 <b>{broadest}</b>가 가장 적어(총 {valid[broadest]}개) "
        f"상대적으로 보장 범위가 넓은 편으로 보여요. 다만 이건 조항 개수 기준일 뿐, "
        f"<b>실제 보험료와 가입 한도는 다를 수 있으니</b> 아래에서 본인 여행 일정 기준으로 "
        f"직접 견적을 비교해보시는 걸 추천드려요."
    )
