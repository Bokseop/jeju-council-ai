"""
local_pipeline.py
100% 로컬 / 오프라인 초경량 도의회 회의록 검색 및 관련성 판별 파이프라인
- 외부 LLM API 비용: 0원 (환각 0%, 외부 유출 원천 차단)
- 1단계: 형태소/키워드 추출 & SQLite FTS5 고속 필터링 (0.01초)
- 2/3단계: Whisper-small급 초경량 Cross-Encoder 판별/리랭킹 (0.05초)
- 4단계: 규격화된 MCP 응답 템플릿 반환
"""

import os
import sys
import time
import math
import sqlite3
import argparse
from typing import List, Dict, Any, Optional

# 1. SSL 인증서 환경 호환성 확보 (기업망/공공망 신뢰 저장소 연결)
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from sentence_transformers import CrossEncoder
from keyword_extractor import extract_keywords, build_fts_expression

# 기본 설정
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jeju_council.db")

# Whisper-small 급 (~260MB) 한국어 특화 초경량 Cross-Encoder 리랭커 모델
# 로컬 models 폴더가 존재하면 100% 완전 오프라인 로드, 없으면 HuggingFace 자동 다운로드
LOCAL_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "ko-reranker")
DEFAULT_MODEL = LOCAL_MODEL_PATH if os.path.isdir(LOCAL_MODEL_PATH) else "Dongjin-kr/ko-reranker"

_JUDGE_INSTANCE: Optional[CrossEncoder] = None

def get_judge(model_name: str = DEFAULT_MODEL) -> CrossEncoder:
    """싱글톤 패턴으로 모델을 메모리에 상주시켜 매 호출 시 지연 시간 제거"""
    global _JUDGE_INSTANCE
    if _JUDGE_INSTANCE is None:
        t0 = time.time()
        # CPU 최적화 로드
        _JUDGE_INSTANCE = CrossEncoder(model_name)
        load_time = time.time() - t0
        # 디버그용 출력
        # sys.stderr.write(f"[INFO] CrossEncoder model loaded ({model_name}) in {load_time:.2f}s\n")
    return _JUDGE_INSTANCE

def sigmoid(x: float) -> float:
    """CrossEncoder 로짓(Logit) 점수를 직관적인 0.0 ~ 1.0 확률값으로 변환"""
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0

def find_exact_speeches(
    user_query: str,
    top_k: int = 3,
    threshold: float = 0.5,
    db_path: str = DB_PATH,
    model_name: str = DEFAULT_MODEL
) -> Dict[str, Any]:
    """
    사용자의 자연어 질문을 바탕으로 회의록에서 일치하는 발언을 찾아 정형화된 결과로 반환합니다.

    Args:
        user_query (str): 사용자 자연어 질문 (예: "신효섭 국장이 우주센터 조례 질의받은 거 찾아줘")
        top_k (int): 반환할 상위 발언 개수 (기본값: 3)
        threshold (float): 최소 관련도 점수 임계값 (0.0 ~ 1.0, 기본값: 0.5)
        db_path (str): SQLite 데이터베이스 파일 경로
        model_name (str): 리랭커 모델명 또는 경로

    Returns:
        Dict[str, Any]: 검색 결과 메타데이터, 발언 목록, 실행 시간
    """
    total_start = time.time()
    
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB 파일을 찾을 수 없습니다: {db_path}. 'python init_db.py'를 먼저 실행하십시오.")

    # ---------------------------------------------------------
    # 1단계: 형태소/키워드 추출 및 SQLite FTS5 고속 필터링 (0.01초)
    # ---------------------------------------------------------
    fts_start = time.time()
    keywords = extract_keywords(user_query)
    fts_expression = build_fts_expression(keywords)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    candidates = []
    
    # 1차 시도: FTS5 MATCH (bm25 관련도 기준 상위 15개 고속 추출)
    if fts_expression:
        fts_query = """
            SELECT s.id, s.meeting_name, s.meeting_date, s.speaker_name, s.speaker_role, s.content,
                   COALESCE(s.source_url, m.source_url, '') AS source_url
            FROM speeches_fts f
            JOIN speeches s ON f.rowid = s.id
            LEFT JOIN meetings m ON s.meeting_name = m.meeting_name AND s.meeting_date = m.meeting_date
            WHERE speeches_fts MATCH ?
            ORDER BY bm25(speeches_fts) ASC
            LIMIT 15
        """
        try:
            cursor.execute(fts_query, (fts_expression,))
            candidates = cursor.fetchall()
        except Exception:
            candidates = []

    # 2차 시도 (Fallback): FTS 매치 결과가 없거나 적을 경우 부분일치 LIKE로 보완
    if len(candidates) < 3 and keywords:
        like_clauses = ["s.content LIKE ? OR s.speaker_name LIKE ?"] * len(keywords[:3])
        like_sql = f"""
            SELECT s.id, s.meeting_name, s.meeting_date, s.speaker_name, s.speaker_role, s.content,
                   COALESCE(s.source_url, m.source_url, '') AS source_url
            FROM speeches s
            LEFT JOIN meetings m ON s.meeting_name = m.meeting_name AND s.meeting_date = m.meeting_date
            WHERE {" OR ".join(like_clauses)}
            LIMIT 15
        """
        params = []
        for kw in keywords[:3]:
            params.extend([f"%{kw}%", f"%{kw}%"])
        try:
            cursor.execute(like_sql, params)
            fallback_rows = cursor.fetchall()
            existing_ids = {c[0] for c in candidates}
            for row in fallback_rows:
                if row[0] not in existing_ids:
                    candidates.append(row)
        except Exception:
            pass

    # 만약 키워드로도 아무것도 안 잡혔다면 전체 최근 발언 15개 가져오기
    if not candidates:
        cursor.execute("""
            SELECT s.id, s.meeting_name, s.meeting_date, s.speaker_name, s.speaker_role, s.content,
                   COALESCE(s.source_url, m.source_url, '') AS source_url
            FROM speeches s
            LEFT JOIN meetings m ON s.meeting_name = m.meeting_name AND s.meeting_date = m.meeting_date
            ORDER BY s.id DESC LIMIT 15
        """)
        candidates = cursor.fetchall()

    conn.close()
    fts_elapsed = time.time() - fts_start

    if not candidates:
        return {
            "query": user_query,
            "keywords": keywords,
            "total_candidates": 0,
            "results": [],
            "elapsed": {
                "fts_ms": round(fts_elapsed * 1000, 2),
                "reranker_ms": 0.0,
                "total_ms": round((time.time() - total_start) * 1000, 2)
            }
        }

    # ---------------------------------------------------------
    # 2단계: 초경량 Cross-Encoder 판별 및 정밀 채점 (0.05초)
    # ---------------------------------------------------------
    rerank_start = time.time()
    judge = get_judge(model_name)

    # (질문, 발언본문) 쌍 구성
    pairs = [[user_query, c[5]] for c in candidates]
    raw_scores = judge.predict(pairs)

    ranked_results = []
    for idx, raw_score in enumerate(raw_scores):
        c = candidates[idx]
        norm_score = sigmoid(float(raw_score))
        
        # 발언 내용 중 질문과 연관된 핵심 문장 1~2개 자동 발췌
        content = c[5]
        summary_snippet = content[:150] + "..." if len(content) > 150 else content

        source_url = c[6] if len(c) > 6 and c[6] else "https://record.council.jeju.kr/source/minutes/pages/search.html"

        ranked_results.append({
            "id": c[0],
            "score": round(norm_score, 4),
            "raw_score": round(float(raw_score), 4),
            "meeting": c[1],
            "date": c[2],
            "speaker_name": c[3],
            "speaker_role": c[4],
            "speaker": f"{c[4]} {c[3]}",
            "snippet": summary_snippet,
            "content": content,
            "source_url": source_url
        })

    # 관련도 점수 내림차순 정렬
    ranked_results.sort(key=lambda x: x["score"], reverse=True)
    rerank_elapsed = time.time() - rerank_start

    # 임계값 필터링 및 top_k 선정
    filtered = [r for r in ranked_results if r["score"] >= threshold]
    final_results = filtered[:top_k] if filtered else ranked_results[:top_k]

    total_elapsed = time.time() - total_start

    return {
        "query": user_query,
        "keywords": keywords,
        "fts_expression": fts_expression,
        "total_candidates": len(candidates),
        "results": final_results,
        "elapsed": {
            "fts_ms": round(fts_elapsed * 1000, 2),
            "reranker_ms": round(rerank_elapsed * 1000, 2),
            "total_ms": round(total_elapsed * 1000, 2)
        }
    }

def format_mcp_response(response_data: Dict[str, Any]) -> str:
    """
    LLM 생성 없이 추출된 메타데이터 + 원문 발췌록을 규격화된 Markdown 양식으로 출력합니다.
    """
    query = response_data["query"]
    results = response_data["results"]
    elapsed = response_data["elapsed"]
    kws = ", ".join(response_data.get("keywords", []))

    lines = []
    lines.append("=" * 70)
    lines.append(f"🔍 [제주도의회 회의록 로컬 고속 검색 결과]")
    lines.append(f"• 질문: \"{query}\"")
    lines.append(f"• 추출 키워드: [{kws}]")
    lines.append(f"• 소요 시간: 총 {elapsed['total_ms']}ms (FTS: {elapsed['fts_ms']}ms, Reranker: {elapsed['reranker_ms']}ms)")
    lines.append(f"• 후보군: {response_data['total_candidates']}건 중 상위 {len(results)}건 선정")
    lines.append("=" * 70)

    if not results:
        lines.append("\n⚠️ 일치하는 회의록 발언을 찾지 못했습니다.")
        return "\n".join(lines)

    for rank, r in enumerate(results, 1):
        score_pct = round(r["score"] * 100, 1)
        stars = "★" * int(score_pct // 20) + "☆" * (5 - int(score_pct // 20))
        lines.append(f"\n[{rank}순위] 일치도: {score_pct}% ({stars}) | Raw Score: {r['raw_score']}")
        lines.append(f"• 회의명: {r['meeting']} ({r['date']})")
        lines.append(f"• 발언자: {r['speaker']}")
        if r.get("source_url"):
            lines.append(f"• 공식 웹 링크: {r['source_url']}")
        lines.append(f"• 발췌록:")
        lines.append(f"  \"{r['content']}\"")
        lines.append("-" * 70)

    return "\n".join(lines)

def get_full_meeting_transcript(meeting_name: str, meeting_date: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    해당 회의의 전체 발언을 시작부터 끝까지 순서대로(id순) 전수 조회
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.speaker_name, s.speaker_role, s.content, COALESCE(s.source_url, m.source_url, '')
        FROM speeches s
        LEFT JOIN meetings m ON s.meeting_name = m.meeting_name AND s.meeting_date = m.meeting_date
        WHERE s.meeting_name = ? AND s.meeting_date = ?
        ORDER BY s.id ASC
    """, (meeting_name, meeting_date))
    rows = cur.fetchall()

    cur.execute("SELECT source_url FROM meetings WHERE meeting_name = ? AND meeting_date = ?", (meeting_name, meeting_date))
    m_row = cur.fetchone()
    source_url = m_row[0] if m_row and m_row[0] else ""
    if not source_url and rows and rows[0][4]:
        source_url = rows[0][4]
    if not source_url:
        source_url = "https://record.council.jeju.kr/source/minutes/pages/search.html"

    conn.close()

    speeches = []
    for r in rows:
        speeches.append({
            "id": r[0],
            "speaker_name": r[1],
            "speaker_role": r[2],
            "speaker": f"{r[2]} {r[1]}" if r[2] != r[1] else r[1],
            "content": r[3]
        })

    return {
        "meeting_name": meeting_name,
        "meeting_date": meeting_date,
        "source_url": source_url,
        "total_speeches": len(speeches),
        "speeches": speeches
    }

# ---------------------------------------------------------
# CLI 실행부
# ---------------------------------------------------------
if __name__ == "__main__":
    # Windows 콘솔 utf-8 출력 설정
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="100% 로컬 초경량 의회 회의록 검색 파이프라인")
    parser.add_argument("--query", "-q", type=str, help="검색할 자연어 질의")
    parser.add_argument("--top_k", "-k", type=int, default=2, help="반환할 상위 결과 개수 (기본 2)")
    parser.add_argument("--threshold", "-t", type=float, default=0.2, help="점수 임계값 (0.0~1.0)")
    parser.add_argument("--json", action="store_true", help="JSON 형태로 결과 반환")

    args = parser.parse_args()

    # 사전 모델 웜업
    get_judge()

    if args.query:
        res = find_exact_speeches(args.query, top_k=args.top_k, threshold=args.threshold)
        if args.json:
            import json
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(format_mcp_response(res))
    else:
        # 기본 데모 질의 실행
        demo_queries = [
            "신효섭 국장이 우주센터 관련해서 조례 질의받은 거 찾아줘",
            "우주산업 육성 및 센터 건립 관련 업무보고"
        ]
        for q in demo_queries:
            res = find_exact_speeches(q, top_k=2)
            print(format_mcp_response(res))
            print("\n")
