"""
mcp_server.py
제주특별자치도의회 100% 로컬 초경량 AI 회의록 검색 표준 MCP Server
- 표준 Model Context Protocol (MCP) JSON-RPC 2.0 over stdio 구현
- Claude Desktop, Antigravity, Cursor 등 모든 MCP 호환 클라이언트와 즉시 연동
- 제공 도구(Tools):
  1) search_council_speeches: 자연어 기반 회의록 고속 검색 및 관련도 정밀 판별
  2) import_council_file: 회의록 파일(HWPX, TXT, JSON, XLSX 등) 로컬 DB 추가 임포트
  3) get_council_stats: 회의록 DB 통계 및 현황 조회
  4) crawl_council_meetings: 도의회 전자회의록 포털 실시간 크롤링 및 적재
"""

import os
import sys
import json
import sqlite3
import traceback
from typing import Dict, Any, List, Optional

# Windows 콘솔 utf-8 설정
if sys.platform == "win32":
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from local_pipeline import find_exact_speeches, format_mcp_response, get_full_meeting_transcript, DB_PATH
from batch_importer import import_file, import_directory
from crawler import run_crawler

SERVER_NAME = "jeju-council-local-ai"
SERVER_VERSION = "1.0.0"

# MCP 도구(Tools) 명세
TOOLS = [
    {
        "name": "search_speeches",
        "description": (
            "제주특별자치도의회 회의록에서 사용자의 자연어 질문과 가장 관련 있는 발언을 100% 로컬 초경량 AI로 고속 검색합니다. "
            "SQLite FTS5 고속 필터링(0.001초)과 한국어 Cross-Encoder 판별 엔진(0.05초)을 결합하여 환각 없이 정확한 원문과 메타데이터를 반환합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색할 자연어 질문 (예: '제455회 본회의 제주 동부해양경찰서 신설 촉구 건의안', '경제활력국 일자리 지원 사업 질의', '서귀포시장 행정사무감사')"
                },
                "top_k": {
                    "type": "integer",
                    "description": "반환할 상위 발언 개수 (기본값: 3)",
                    "default": 3
                },
                "threshold": {
                    "type": "number",
                    "description": "최소 관련도 임계값 점수 (0.0~1.0, 기본값: 0.2)",
                    "default": 0.2
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_council_speeches",
        "description": "search_speeches의 별칭(Alias) 도구입니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "자연어 검색 질문"},
                "top_k": {"type": "integer", "default": 3},
                "threshold": {"type": "number", "default": 0.2}
            },
            "required": ["query"]
        }
    },
    {
        "name": "import_council_file",
        "description": "외부 회의록 파일(한글 HWPX, TXT, 마크다운 MD, JSON, CSV, Excel XLSX)을 로컬 SQLite DB에 파싱하여 추가 적재합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "임포트할 파일 또는 디렉토리의 절대 경로 또는 상대 경로"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "get_council_stats",
        "description": "현재 로컬 회의록 DB에 저장된 총 발언 건수, 회의 목록, 주요 발언자 통계를 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "crawl_council_meetings",
        "description": "제주도의회 전자회의록 포털에서 일자별/월별 과거 회기 또는 최신 회의록을 웹 크롤링하여 증분 업데이트(중복 제외)로 DB에 자동 적재합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "수집 시작 일자 (YYYY-MM-DD, 예: '2024-10-01'). 지정 시 과거 회기 연도별 탐색"
                },
                "end_date": {
                    "type": "string",
                    "description": "수집 종료 일자 (YYYY-MM-DD, 예: '2024-10-31'). 지정 시 과거 회기 연도별 탐색"
                },
                "keyword": {
                    "type": "string",
                    "description": "회의명 검색 키워드 필터 (예: 본회의, 행정사무감사, 미래경제)",
                    "default": ""
                },
                "start_page": {
                    "type": "integer",
                    "description": "최근 회기 수집 시 시작 페이지 번호 (기본: 1)",
                    "default": 1
                },
                "end_page": {
                    "type": "integer",
                    "description": "최근 회기 수집 시 종료 페이지 번호 (기본: 10)",
                    "default": 10
                }
            }
        }
    },
    {
        "name": "get_full_meeting_transcript",
        "description": "특정 회의의 전체 발언록(발언 순서대로 정렬) 및 공식 도의회 웹페이지 링크를 전수 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "meeting_name": {
                    "type": "string",
                    "description": "회의명 (예: '제13대 제455회 본회의 제2차')"
                },
                "meeting_date": {
                    "type": "string",
                    "description": "회의 일자 (YYYY-MM-DD, 예: '2026-10-23')"
                }
            },
            "required": ["meeting_name", "meeting_date"]
        }
    }
]

def handle_search_council_speeches(arguments: Dict[str, Any]) -> str:
    query = arguments.get("query", "")
    top_k = int(arguments.get("top_k", 3))
    threshold = float(arguments.get("threshold", 0.2))

    if not query:
        return "오류: 검색어(query)가 누락되었습니다."

    res = find_exact_speeches(query, top_k=top_k, threshold=threshold)
    return format_mcp_response(res)

def handle_import_council_file(arguments: Dict[str, Any]) -> str:
    file_path = arguments.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return f"오류: 파일이나 디렉토리를 찾을 수 없습니다: {file_path}"

    if os.path.isdir(file_path):
        res = import_directory(file_path)
        return (
            f"✅ 디렉토리 일괄 임포트 완료\n"
            f"• 디렉토리: {file_path}\n"
            f"• 처리된 파일: {res['processed_files']}개\n"
            f"• 신규 적재 발언: {res['total_inserted']}건"
        )
    else:
        res = import_file(file_path)
        return (
            f"✅ 단일 파일 임포트 완료\n"
            f"• 파일명: {os.path.basename(file_path)}\n"
            f"• 파싱된 발언: {res['parsed_count']}건\n"
            f"• FTS5 색인 적재: {res['inserted_count']}건"
        )

def handle_get_council_stats(arguments: Dict[str, Any]) -> str:
    if not os.path.exists(DB_PATH):
        return "오류: DB 파일이 존재하지 않습니다."

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    total_speeches = cur.execute("SELECT count(*) FROM speeches").fetchone()[0]
    fts_speeches = cur.execute("SELECT count(*) FROM speeches_fts").fetchone()[0]
    meetings = cur.execute("SELECT DISTINCT meeting_name, meeting_date FROM speeches ORDER BY meeting_date DESC LIMIT 10").fetchall()
    speakers = cur.execute("SELECT speaker_role, speaker_name, count(*) as cnt FROM speeches GROUP BY speaker_name, speaker_role ORDER BY cnt DESC LIMIT 8").fetchall()
    conn.close()

    lines = []
    lines.append("📊 [제주특별자치도의회 회의록 로컬 DB 현황]")
    lines.append(f"• 총 적재 발언 건수: {total_speeches:,}건 (FTS5 색인: {fts_speeches:,}건)")
    lines.append(f"• DB 파일 경로: {DB_PATH}")
    lines.append("\n🏛️ 최근 등록된 회의 목록:")
    for m in meetings:
        lines.append(f"  - {m[1]}: {m[0]}")
    lines.append("\n👤 주요 발언자 현황:")
    for s in speakers:
        lines.append(f"  - {s[0]} {s[1]}: {s[2]}회 발언")

    return "\n".join(lines)

def handle_crawl_council_meetings(arguments: Dict[str, Any]) -> str:
    start_date = arguments.get("start_date")
    end_date = arguments.get("end_date")
    start_p = int(arguments.get("start_page", 1))
    end_p = int(arguments.get("end_page", 10))
    keyword = arguments.get("keyword", "")

    summary = run_crawler(
        start_date=start_date,
        end_date=end_date,
        start_page=start_p,
        end_page=end_p,
        keyword=keyword
    )
    if start_date and end_date:
        range_desc = f"기간별 수집 ({start_date} ~ {end_date})"
    else:
        range_desc = f"최근 회기 수집 (페이지 {start_p}~{end_p})"

    return (
        f"🌐 도의회 회의록 전자수집 결과:\n"
        f"• 대상 범위: {range_desc}\n"
        f"• 검색 키워드: '{keyword}'\n"
        f"• 발견된 회의: {summary['total_meetings_found']}건\n"
        f"• 기수집 건너뜀: {summary['skipped_existing']}건\n"
        f"• 신규 적재 발언: {summary['total_speeches_inserted']}건"
    )

def handle_get_full_meeting_transcript(arguments: Dict[str, Any]) -> str:
    meeting_name = arguments.get("meeting_name", "").strip()
    meeting_date = arguments.get("meeting_date", "").strip()
    if not meeting_name or not meeting_date:
        return "오류: meeting_name과 meeting_date 파라미터를 입력해 주세요."

    try:
        data = get_full_meeting_transcript(meeting_name, meeting_date)
    except Exception as e:
        return f"전체 회의록 조회 오류: {e}"

    speeches = data.get("speeches", [])
    total = data.get("total_speeches", 0)
    source_url = data.get("source_url", "")

    lines = [
        "=" * 70,
        f"🏛️ [{meeting_name}] 전체 회의록 전문",
        f"• 일시: {meeting_date}",
        f"• 총 발언 수: {total}건",
        f"• 공식 전자회의록 링크: {source_url}",
        "=" * 70,
        ""
    ]
    for idx, s in enumerate(speeches, 1):
        lines.append(f"[{idx} / {total}] ◯{s['speaker']}")
        lines.append(f"{s['content']}")
        lines.append("-" * 70)

    return "\n".join(lines)

def dispatch_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name in ["search_speeches", "search_council_speeches"]:
        return handle_search_council_speeches(arguments)
    elif tool_name == "get_full_meeting_transcript":
        return handle_get_full_meeting_transcript(arguments)
    elif tool_name == "import_council_file":
        return handle_import_council_file(arguments)
    elif tool_name == "get_council_stats":
        return handle_get_council_stats(arguments)
    elif tool_name == "crawl_council_meetings":
        return handle_crawl_council_meetings(arguments)
    else:
        raise ValueError(f"알 수 없는 도구 이름: {tool_name}")

def send_response(response: Dict[str, Any]):
    """JSON-RPC 응답 전송 (stdout)"""
    body = json.dumps(response, ensure_ascii=False)
    sys.stdout.write(body + "\n")
    sys.stdout.flush()

def main():
    """MCP JSON-RPC 2.0 stdio 이벤트 루프"""
    for line in sys.stdin:
        line_str = line.strip()
        if not line_str:
            continue

        try:
            req = json.loads(line_str)
        except Exception:
            continue

        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        # 1. 초기화 핸드셰이크
        if method == "initialize":
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION
                    },
                    "capabilities": {
                        "tools": {}
                    }
                }
            })
        elif method == "notifications/initialized":
            pass # 클라이언트 초기화 완료 노티 (응답 불필요)
        elif method == "ping":
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {}
            })
        # 2. 도구 목록 요청 (tools/list)
        elif method == "tools/list":
            send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": TOOLS
                }
            })
        # 3. 도구 실행 요청 (tools/call)
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            try:
                result_text = dispatch_tool(tool_name, tool_args)
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": result_text
                            }
                        ],
                        "isError": False
                    }
                })
            except Exception as e:
                err_detail = traceback.format_exc()
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"도구 실행 중 오류가 발생했습니다:\n{e}\n\n{err_detail}"
                            }
                        ],
                        "isError": True
                    }
                })
        else:
            # 지원하지 않는 메서드에 대한 에러 응답
            if msg_id is not None:
                send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                })

if __name__ == "__main__":
    main()
