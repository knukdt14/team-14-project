"""
여행자보험 RAG 검색 파이프라인
사용자 질문 -> Intent Classification -> (rider 전체 조항 fetch) + Query Rewrite -> Qwen 답변

핵심 설계 원칙
1. Intent가 특정 카테고리로 확정되면: 임베딩 top-k 랭킹에 의존하지 않고
   해당 insurer + rider 조합의 조항을 "전부" 가져온다.
   -> "보상하는 손해"(제1조)와 "보상하지 않는 손해"(제2조, 면책)가
      랭킹에서 분리되어 하나가 누락되는 문제를 구조적으로 없앤다.
2. Intent가 "해당없음/기타"로 분류되면: 억지로 아무 리더에나 끼워맞추지 않고
   "이 상품엔 해당 보장이 없을 수 있음"을 사용자에게 알리거나,
   Query Rewrite + 전체 코퍼스 semantic top-k로 fallback.
3. Query Rewrite는 (a) intent가 애매할 때의 semantic 검색용,
   (b) intent가 확정된 뒤 "그 조항 안에서 정확히 뭘 찾을지" 답변 생성 프롬프트에 넣는 용도로 쓴다.
"""
from __future__ import annotations
import json
import re

# ---------------------------------------------------------------------------
# 1) Intent -> insurer별 실제 rider명 매핑
#    (chunks_output.json에서 실제 추출한 특약명 그대로 사용 - 오타/줄바꿈까지 정확히 일치해야 함)
# ---------------------------------------------------------------------------
INTENT_RIDER_MAP: dict[str, dict[str, list[str]]] = {
    "휴대품손해": {
        "프로미다이렉트": ["국내여행중 휴대품손해(분실제외) 특별약관"],
        "CM8150": ["여행중 휴대품손해(분실제외)보장 특별약관"],
        "inTravel": ["여행중 휴대품손해(분실제외) 특별약관"],
        "meritz": ["휴대품손해(분실제외) 특별약관"],
    },
    "배상책임": {
        "프로미다이렉트": ["국내여행중 배상책임 특별약관"],
        "CM8150": ["배상책임보장 특별약관"],
        "inTravel": ["여행중 배상책임 특별약관"],
        "meritz": ["배상책임 특별약관"],
    },
    "실손의료비_상해질병": {
        "프로미다이렉트": ["기본형 실손의료비 특별약관"],
        "CM8150": ["비급여 실손의료비보장 특별약관", "보통약관(기본계약)"],
        "inTravel": ["보통약관(기본계약)"],
        "meritz": ["실손의료보험 특별약관"],
    },
    "상해질병_사망후유장해": {
        "프로미다이렉트": ["스포츠단체 상해사망후유장해 특별약관"],
        "CM8150": ["질병사망 및 질병 80%이상 고도후유장해보장 특별약관"],
        "inTravel": ["여행중 질병사망 및 질병 80%이상 고도후유장해 특별약관"],
        "meritz": ["질병사망 및 질병80%이상후유장해 특별약관"],
    },
    # 국내여행보험 특성상 아래 카테고리는 상품 자체에 해당 보장이 없거나
    # 극히 제한적으로만 존재함 (예: inTravel의 제주출발 항공기결항 특약뿐).
    # -> 검색을 시도하지 말고, "해당 상품엔 이 보장이 없을 수 있다"고
    #    먼저 안내한 뒤에만 제한적으로 검색하도록 분리해둔다.
    "항공기지연": {},          # 4개사 전부 해당 특약 없음 (국내여행이라 항공 지연 보장 자체가 드묾)
    "여행취소": {},            # 4개사 전부 해당 특약 없음
    "여권분실": {},            # 여권은 오히려 휴대품손해 "보장 제외 물건"으로 명시됨
}

NO_COVERAGE_INTENTS = {"항공기지연", "여행취소", "여권분실"}


# ---------------------------------------------------------------------------
# 2) Intent Classification 프롬프트 (Qwen)
# ---------------------------------------------------------------------------
INTENT_CLASSIFY_PROMPT = """당신은 여행자보험 상담 챗봇의 검색 라우터입니다.
사용자의 질문을 아래 카테고리 중 정확히 하나로 분류하세요.
애매하거나 여러 개에 걸치면 "기타"로 분류하세요. 절대로 설명을 덧붙이지 말고 카테고리명만 출력하세요.

카테고리:
- 휴대품손해 (가방/캐리어/휴대폰/카메라/지갑/시계/의류 등 여행 중 소지한 물건 전반의 도난·파손·분실.
  단, 지갑이든 캐리어든 상관없이 "물건 자체"가 여기 해당하며, 그 안의 현금/신용카드/여권처럼
  "물건 안의 내용물"이 별도로 제외 대상인지는 이 단계에서 따지지 않는다.
  단순분실도 일단 이 카테고리로 분류한다. 보장 여부는 다음 단계에서 조항으로 판단하므로,
  "제외될 것 같다"는 이유로 여기서 미리 기타로 보내지 말 것)
- 배상책임 (렌트카 파손, 타인 물건 파손, 타인 신체 손해 등 내가 남에게 끼친 손해)
- 실손의료비_상해질병 (병원 진료, 감기, 상해로 인한 통원/입원 치료비)
- 상해질병_사망후유장해 (사망, 후유장해 관련)
- 항공기지연 (비행기 지연/결항)
- 여행취소 (여행 자체를 취소하게 된 경우)
- 여권분실 (여권을 잃어버리거나 도난당한 경우)
- 기타 (위 어디에도 명확히 속하지 않는 경우)

질문: {question}
카테고리:"""


def classify_intent(llm_call, question: str) -> str:
    """llm_call: (prompt:str) -> str 형태의 로컬 Qwen 호출 함수를 주입받는다."""
    raw = llm_call(INTENT_CLASSIFY_PROMPT.format(question=question)).strip()
    for intent in INTENT_RIDER_MAP:
        if intent in raw:
            return intent
    return "기타"


# ---------------------------------------------------------------------------
# 3) Query Rewrite 프롬프트 (Qwen) - "기타"로 분류됐거나, mode2 비교검색 시 사용
# ---------------------------------------------------------------------------
QUERY_REWRITE_PROMPT = """당신은 보험 약관 검색 도우미입니다.
사용자의 일상적인 표현을, 국내여행보험 약관에서 실제로 쓰이는 공식 용어로 바꾼 "검색용 문장"만 출력하세요.
설명하지 말고, 답변하지 말고, 오직 검색어 문장 하나만 출력합니다.

예시)
입력: 캐리어를 잃어버렸어요
출력: 수하물 휴대품 분실 손해 보상

입력: 핸드폰을 도둑맞았어요
출력: 휴대품 도난 손해 보상

입력: 렌트카를 긁었어요
출력: 배상책임 재물 손해 보상

입력: {question}
출력:"""


def rewrite_query(llm_call, question: str) -> str:
    return llm_call(QUERY_REWRITE_PROMPT.format(question=question)).strip()


# ---------------------------------------------------------------------------
# 4) 실제 검색 함수 - intent 확정 시 rider 전체 fetch, 아니면 embedding top-k fallback
# ---------------------------------------------------------------------------
def retrieve_for_question(
    question: str,
    insurer: str,
    llm_call,
    collection,          # Chroma collection (모든 청크 저장됨)
    embedder,            # bge-m3 SentenceTransformer
    all_chunks: list[dict],   # chunks_output.json 로드본 (rider 전체 fetch용)
    k_fallback: int = 8,
) -> tuple[str, list[dict]]:
    intent = classify_intent(llm_call, question)

    if intent in NO_COVERAGE_INTENTS:
        # 상품 자체에 해당 보장이 없을 가능성이 높음 -> 사실대로 안내하고,
        # 그래도 혹시 모를 관련 조항을 위해 rewrite 기반 fallback 검색은 시도
        note = f"[안내] '{intent}'은(는) 국내여행보험 상품 특성상 보장하지 않는 경우가 많습니다. 약관상 명시적 제외 여부를 확인합니다."
        rewritten = rewrite_query(llm_call, question)
        q_emb = embedder.encode([rewritten], normalize_embeddings=True).tolist()
        res = collection.query(query_embeddings=q_emb, n_results=k_fallback, where={"insurer": insurer})
        return note, res

    rider_map = INTENT_RIDER_MAP.get(intent, {})
    rider_names = rider_map.get(insurer, [])
    if not rider_names:
        # 매핑이 없으면 rewrite + top-k로 fallback
        rewritten = rewrite_query(llm_call, question)
        q_emb = embedder.encode([rewritten], normalize_embeddings=True).tolist()
        res = collection.query(query_embeddings=q_emb, n_results=k_fallback, where={"insurer": insurer})
        return f"[intent={intent}, rider 매핑 없음 -> semantic fallback]", res

    # intent가 확정되고 rider명도 알려진 경우: 해당 rider의 조항을 전부 가져온다
    # (top-k 랭킹에 의존하지 않으므로 보상/면책 조항 누락 문제가 없음)
    matched = [
        c for c in all_chunks
        if c["insurer"] == insurer and c.get("rider") in rider_names
    ]
    return f"[intent={intent}, rider={rider_names} 전체 {len(matched)}개 조항 fetch]", matched


if __name__ == "__main__":
    with open("chunks_output.json", encoding="utf-8") as f:
        all_chunks = json.load(f)

    # llm_call 목업 (실제로는 로컬 Qwen 호출로 교체)
    def fake_llm_call(prompt: str) -> str:
        if "카테고리" in prompt and "지갑" in prompt:
            return "휴대품손해"
        return "휴대품손해"

    note, matched = retrieve_for_question(
        "지갑을 잃어버렸어요", "meritz", fake_llm_call,
        collection=None, embedder=None, all_chunks=all_chunks,
    )
    print(note)
    for c in matched:
        print(' -', c['article'])
