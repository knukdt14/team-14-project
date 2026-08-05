"""
①Upstage API(Solar Pro) 연결 + ②LangChain 연결 + ③최종 답변 생성 체인

로컬(conda env)에서 실행:
    pip install langchain langchain-upstage
    .env 파일에 UPSTAGE_API_KEY=발급받은키 한 줄 추가 (https://console.upstage.ai 에서 발급)
    python rag_chain.py

전제:
- build_index.py를 먼저 돌려서 ./chroma_db 가 만들어져 있어야 함
- chunks_output.json 이 같은 폴더에 있어야 함 (intent 확정 시 rider 전체 fetch용)
- query_pipeline.py 가 같은 폴더에 있어야 함 (INTENT_RIDER_MAP, 프롬프트 재사용)

참고: 이전에 쓰던 Groq(Qwen3.6-27b) 버전은 rag_chain_groq_qwen_backup.py 로 그대로 백업해뒀음.
나중에 다시 Qwen으로 돌아가고 싶으면 그 파일을 rag_chain.py로 복사해서 쓰면 됨.
"""
from __future__ import annotations
import os
import re
import json
import chromadb
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from langchain_upstage import ChatUpstage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from query_pipeline import (
    INTENT_RIDER_MAP,
    NO_COVERAGE_INTENTS,
    INTENT_CLASSIFY_PROMPT,
    QUERY_REWRITE_PROMPT,
)
from clause_matcher import find_relevant_clauses, find_relevant_clauses_structured

LLM_MODEL_NAME = "solar-pro"   # Upstage Solar Pro (현재 Solar Pro 3를 가리키는 별칭). 한국어 문서 처리에 강함
DB_DIR = "./chroma_db"
COLLECTION_NAME = "travel_insurance"
EMBED_MODEL_NAME = "BAAI/bge-m3"


# ---------------------------------------------------------------------------
# ① Upstage API 연결 (LangChain ChatUpstage)
# ---------------------------------------------------------------------------
def load_qwen_llm():
    """
    intent 분류/rewrite 용과 답변 생성용을 분리한다 (Groq 때와 동일한 이유).
    - classify_llm: 짧고 정확한 출력. temperature=0으로 결정적으로.
    - answer_llm: 조금 더 긴 답변, 역시 temperature=0 (사실 기반 인용이라 창의성 불필요).
    Solar Pro는 Qwen3.6처럼 <think> 블록을 강제로 내보내는 thinking 모델이 아니라서
    reasoning_effort 같은 별도 처리 없이 바로 최종 답변을 준다 (Groq 때 겪었던 이슈 자체가 없음).
    UPSTAGE_API_KEY 환경변수(.env)가 설정돼 있어야 한다.
    함수 이름은 다른 파일(query_pipeline.py 등)과의 호환을 위해 load_qwen_llm으로 유지함.
    """
    if not os.environ.get("UPSTAGE_API_KEY"):
        load_dotenv()  # .env 파일에서 UPSTAGE_API_KEY를 찾아 환경변수로 로드 시도
    if not os.environ.get("UPSTAGE_API_KEY"):
        raise RuntimeError(
            "UPSTAGE_API_KEY가 없습니다. "
            "https://console.upstage.ai 에서 키를 발급받아 "
            "이 폴더의 .env 파일에 UPSTAGE_API_KEY=발급받은키 한 줄을 적어두세요."
        )

    classify_llm = ChatUpstage(model=LLM_MODEL_NAME, temperature=0, max_tokens=60)
    answer_llm = ChatUpstage(model=LLM_MODEL_NAME, temperature=0, max_tokens=256)
    return classify_llm, answer_llm


# ---------------------------------------------------------------------------
# ② LangChain LCEL 체인 구성
# ---------------------------------------------------------------------------
def build_chains(classify_llm, answer_llm):
    intent_prompt = ChatPromptTemplate.from_template(INTENT_CLASSIFY_PROMPT)
    intent_chain = intent_prompt | classify_llm | StrOutputParser()

    rewrite_prompt = ChatPromptTemplate.from_template(QUERY_REWRITE_PROMPT)
    rewrite_chain = rewrite_prompt | classify_llm | StrOutputParser()

    answer_prompt = ChatPromptTemplate.from_template("""당신은 여행자보험 상담 챗봇입니다.
[약관 조항]은 이미 질문과 관련된 부분만 코드가 골라서 준 것입니다. 이 안의 내용만 근거로
답변하세요. [약관 조항]에 없는 내용은 추측하지 말고 "약관에서 확인되지 않습니다"라고 답하세요.

절대 하지 말아야 할 것:
- 비유, 상상, 서술적 표현 금지
- [약관 조항]에 없는 개념(예: 원문에 없는 "도난" 같은 말)을 판단 기준으로 새로 만들지 말 것
- 조 제목이나 번호를 새로 만들지 말고, [약관 조항]에 있는 "[제N조(제목)]" 대괄호와 번호를
  토씨 하나 바꾸지 말고 그대로 사용할 것
- 같은 문장 반복 금지

답변 형식 (이 순서로, 짧게):
1) [약관 조항]에 있는 조 제목(대괄호)과 해당 번호를 그대로 적으세요.
2) 그 문장을 원문 그대로 한 번 인용하세요.
3) "보상되지 않습니다" 또는 "보상됩니다" 중 하나로 한 문장 결론.

매우 중요 (자기모순 금지):
- 인용한 문장 자체가 "보상합니다/보상해 드립니다/보상하여 드립니다"처럼 보상한다는 내용이면,
  결론은 반드시 "보상됩니다"여야 합니다. 조 제목이 면책조항이 아닌데 결론을 "보상되지 않습니다"로
  쓰면 안 됩니다.
- [약관 조항] 안의 면책 사유(보상하지 않는 손해) 번호들을 다 훑어봐도 질문 상황과
  명확히 일치하는 게 하나도 없다면, 억지로 가장 비슷해 보이는 번호를 갖다 쓰지 말고
  "보상됩니다"로 결론 내리세요 (면책 사유가 없으면 원칙적으로 보상되는 것입니다).

[약관 조항]
{context}

[질문]
{question}

[답변]""")
    answer_chain = answer_prompt | answer_llm | StrOutputParser()

    return intent_chain, rewrite_chain, answer_chain


COVERAGE_PHRASES = ("보상합니다", "보상해 드립니다", "보상하여 드립니다", "보상하여드립니다")
EXCLUSION_TITLE_HINT = re.compile(r'(보상하지\s*않는|지급하지\s*않는|보상지\s*않는)')


def _mentions_coverage_without_negation(text: str) -> bool:
    """'보상' 단어가 부정어("~하지 않") 없이 등장하면 그 문맥은 '보상한다'는 뜻으로 본다."""
    for m in re.finditer('보상', text):
        window = text[max(0, m.start() - 8):m.start() + 20]
        if re.search(r'(하지\s*않|되지\s*않|안\s*됩니다|안됩니다)', window):
            continue
        return True
    return False


def enforce_consistent_conclusion(answer: str) -> str:
    """
    LLM 답변에서 '인용한 조 제목/문장'과 '최종 결론'이 서로 모순되는 경우를 코드로 교정한다.
    예: 인용문이 "...보상"(부정어 없이)인데 결론이 "보상되지 않습니다"로 나오는 자기모순 방지.
    """
    # 첫 줄의 [조 제목] 추출
    title_match = re.search(r'\[([^\]]+)\]', answer)
    title = title_match.group(1) if title_match else ""

    is_exclusion_title = bool(EXCLUSION_TITLE_HINT.search(title))
    says_not_covered = "보상되지 않습니다" in answer

    # 조 제목이 면책조항이 아닌데(=보상하는 조항인데) 인용문에 '보상'(부정어 없이)이 있고
    # 결론이 "보상되지 않습니다"로 난 경우 -> 자기모순, 교정
    if not is_exclusion_title and says_not_covered and _mentions_coverage_without_negation(answer):
        answer = re.sub(r'보상되지\s*않습니다', '보상됩니다', answer)
    return answer


def parse_intent(raw: str) -> str:
    # 모델이 "휴대품손해"를 "휴대품 손해"처럼 띄어쓰거나, 따옴표/설명을 덧붙이는 경우까지
    # 잡기 위해 공백을 지우고 비교한다 (완전 일치가 아니라 부분 포함 검사).
    raw_norm = raw.replace(" ", "").replace("\n", "")
    for intent in INTENT_RIDER_MAP:
        if intent.replace(" ", "") in raw_norm:
            return intent
    return "기타"


# ---------------------------------------------------------------------------
# ③ 검색 + 답변 생성 파이프라인 (모드 1 기준: 단일 보험사)
# ---------------------------------------------------------------------------
def build_context(intent: str, insurer: str, question: str, rewrite_chain, all_chunks, collection, embedder, k_fallback=8):
    """
    intent가 '해당없음'이거나 rider 매핑이 없는 경우의 fallback 경로.
    (rider가 매핑된 일반 경로는 answer_question()에서 find_relevant_clauses_structured로
    직접 처리하므로 이 함수까지 오지 않음)
    """
    if intent in NO_COVERAGE_INTENTS:
        note = f"[안내] '{intent}' 관련 보장은 이 상품에 없을 수 있습니다.\n\n"
        rewritten = rewrite_chain.invoke({"question": question})
        q_emb = embedder.encode([rewritten], normalize_embeddings=True).tolist()
        res = collection.query(query_embeddings=q_emb, n_results=k_fallback, where={"insurer": insurer})
        docs = res["documents"][0]
        return note + "\n\n".join(docs)

    # 매핑 없음 -> rewrite + top-k semantic fallback
    rewritten = rewrite_chain.invoke({"question": question})
    q_emb = embedder.encode([rewritten], normalize_embeddings=True).tolist()
    res = collection.query(query_embeddings=q_emb, n_results=k_fallback, where={"insurer": insurer})
    docs = res["documents"][0]
    return "\n\n".join(docs)


def answer_question(question: str, insurer: str, intent_chain, rewrite_chain, answer_chain, all_chunks, collection, embedder) -> dict:
    raw_intent = intent_chain.invoke({"question": question})
    intent = parse_intent(raw_intent)

    rider_names = INTENT_RIDER_MAP.get(intent, {}).get(insurer, [])
    if intent not in NO_COVERAGE_INTENTS and rider_names:
        matched = [c for c in all_chunks if c["insurer"] == insurer and c.get("rider") in rider_names]
        structured = find_relevant_clauses_structured(matched, question)

        if structured["matched_article"] and structured["matched_items"]:
            # 면책 사유가 코드로 명확하게 특정됨 -> 인용문은 LLM한테 맡기지 않고
            # 원문을 그대로 코드가 조립한다 (3B 모델이 반복적으로 엉뚱한 조/제목을
            # 인용하거나 단어를 손상시키는 문제를 구조적으로 차단).
            n, body = structured["matched_items"][0]
            citation = f"[{structured['matched_article']}] {n}. {body}"
            answer = f"{citation}\n\n위 조항에 따라 '{question}'은(는) 보상되지 않습니다."
            return {
                "intent": intent, "raw_intent": raw_intent, "context_preview": citation, "answer": answer,
                "mode": "code_templated",
            }

        if structured.get("verdict") == "covered_no_exclusion_found":
            # "도난"처럼 면책 목록에 없으면 보상 대상이라고 확신 가능한 카테고리인데,
            # 실제로 면책 목록 전체를 뒤져도 해당 사유가 없었던 경우 -> LLM한테 맡기지 않고
            # 코드가 직접 "보상됩니다"로 결론짓는다. (LLM이 "도난"과 "분실"을 혼동해
            # 엉뚱한 면책 조항을 갖다 붙이는 문제를 여기서 원천 차단)
            answer = f"면책조항(보상하지 않는 손해) 목록을 전부 확인했으나 해당 사유가 없습니다.\n\n위에 따라 '{question}'은(는) 보상됩니다."
            return {
                "intent": intent, "raw_intent": raw_intent,
                "context_preview": "면책 목록 전체 확인 - 해당 사유 없음", "answer": answer,
                "mode": "code_templated",
            }

        context = structured["context_text"]
    else:
        context = build_context(intent, insurer, question, rewrite_chain, all_chunks, collection, embedder)

    # Upstage도 티어별 요청 크기/속도 제한이 있고, 실손의료비 특약처럼 거대한
    # 중첩 표가 낀 rider를 통째로 넘기면 불필요하게 비용/시간이 커짐.
    # 한국어 기준 대략 1자=1~1.5토큰으로 잡고 안전하게 넉넉히 잘라낸다.
    MAX_CONTEXT_CHARS = 3000
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n[이하 내용은 길이 제한으로 생략됨]"

    answer = answer_chain.invoke({"context": context, "question": question})
    answer = enforce_consistent_conclusion(answer)
    return {"intent": intent, "raw_intent": raw_intent, "context_preview": context[:300], "answer": answer, "mode": "llm_generated"}


if __name__ == "__main__":
    print("청크 로딩...")
    with open("chunks_output.json", encoding="utf-8") as f:
        all_chunks = json.load(f)

    print(f"임베딩 모델 로딩: {EMBED_MODEL_NAME}")
    embedder = SentenceTransformer(EMBED_MODEL_NAME)

    print("Chroma 연결...")
    client = chromadb.PersistentClient(path=DB_DIR)
    collection = client.get_collection(COLLECTION_NAME)

    print(f"Upstage API 연결: {LLM_MODEL_NAME}")
    classify_llm, answer_llm = load_qwen_llm()
    intent_chain, rewrite_chain, answer_chain = build_chains(classify_llm, answer_llm)

    # 여기 리스트에 (질문, 보험사) 쌍을 추가/수정하면서 한 번에 여러 개 테스트할 수 있음
    # 면책(보상 안 됨)만 몰아서 테스트하면 검증 의미가 없으니, 보상되는 케이스도 섞음
    TEST_CASES = [
        ("지갑을 잃어버렸어요", "meritz"),             # 분실 -> 보상 안 됨 (제2조 8호)
        ("캐리어를 도둑맞았어요", "meritz"),            # 도난 -> 보상됨 (분실과 달리 면책 아님)
        ("여행 중 넘어져서 다쳤어요", "meritz"),        # 상해 치료 -> 보상됨 (실손의료비/상해)
        ("실수로 다른 사람을 다치게 했어요", "meritz"), # 일반 배상책임 -> 보상됨
        ("렌트카를 긁었어요", "meritz"),               # 차량 관련 배상책임 -> 보상 안 됨 (면책 10호)
        ("캐리어가 완전히 부서졌어요", "meritz"),       # 실질적 파손(단순 외관 아님) -> 보상됨
    ]

    for question, insurer in TEST_CASES:
        print(f"\n--- 테스트: {insurer}에서 '{question}' ---")
        result = answer_question(
            question, insurer,
            intent_chain, rewrite_chain, answer_chain,
            all_chunks, collection, embedder,
        )
        print("raw_intent (원문):", repr(result["raw_intent"]))
        print("intent:", result["intent"])
        print("mode:", result["mode"])
        print("context 미리보기:", result["context_preview"][:200])
        print("답변:", result["answer"])
