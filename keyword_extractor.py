"""
keyword_extractor.py
로컬 초경량 형태소/키워드 추출기 (외부 무거운 형태소 분석기 없이 100% 오프라인 동작)
- 질의에서 조사, 어미, 불용어를 분리하여 FTS5 검색 키워드 추출
"""

import re
from typing import List

# 자주 쓰이는 한국어 조사, 어미, 불용어 패턴
STOPWORDS = {
    "이", "가", "은", "는", "을", "를", "에", "에서", "의", "로", "으로",
    "와", "과", "도", "만", "까지", "부터", "에게", "한테",
    "관련", "관련해", "관련해서", "대해", "대해서",
    "질의", "답변", "질의받은", "질의한", "말한", "발언한",
    "찾아줘", "검색해줘", "알려줘", "보여줘", "내용", "것", "거", "좀",
    "어디", "언제", "누가", "무엇", "어떻게", "왜"
}

PARTICLES = [
    "에게서", "에게는", "에서는", "으로는", "으로서", "으로써",
    "에서", "으로", "에게", "한테", "하고", "이며", "에는", "와의", "과의",
    "이", "가", "은", "는", "을", "를", "에", "의", "로", "와", "과", "도", "만"
]

def clean_token(token: str) -> str:
    """단어 끝에 붙은 조사/어미를 단계적으로 제거하여 원형 어근 추출"""
    t = re.sub(r"[^\w가-힣a-zA-Z0-9]", "", token).strip()
    if len(t) <= 1:
        return t
        
    for p in PARTICLES:
        if t.endswith(p) and len(t) > len(p) + 1:
            return t[:-len(p)]
    return t

def extract_keywords(query: str) -> List[str]:
    """자연어 질의에서 FTS5 매치에 사용할 유의미한 키워드 목록 추출"""
    tokens = query.strip().split()
    keywords = set()

    for token in tokens:
        cleaned = clean_token(token)
        # 불용어 필터링 및 2글자 이상 키워드 수집
        if cleaned and cleaned not in STOPWORDS and len(cleaned) >= 2:
            keywords.add(cleaned)
        # 원본 토큰 자체도 특수문자 제거 후 2글자 이상이면 후보 추가
        raw_clean = re.sub(r"[^\w가-힣a-zA-Z0-9]", "", token)
        if raw_clean and raw_clean not in STOPWORDS and len(raw_clean) >= 2:
            keywords.add(raw_clean)

    return sorted(list(keywords), key=lambda x: -len(x))

def build_fts_expression(keywords: List[str]) -> str:
    """FTS5 MATCH 표현식 생성 (OR 조건 결합)"""
    if not keywords:
        return ""
    # FTS5 특수문자 이스케이프 및 따옴표 감싸기
    escaped = [f'"{kw}"' for kw in keywords if kw]
    return " OR ".join(escaped)

if __name__ == "__main__":
    test_queries = [
        "신효섭 국장이 우주센터 관련해서 조례 질의받은 거 찾아줘",
        "우주산업 육성 및 센터 건립 관련 업무보고",
        "하원테크노캠퍼스 착공 일정 지연 우려 질의"
    ]
    for q in test_queries:
        kws = extract_keywords(q)
        fts_expr = build_fts_expression(kws)
        print(f"Query: {q}")
        print(f" -> Keywords: {kws}")
        print(f" -> FTS Expression: {fts_expr}\n")
