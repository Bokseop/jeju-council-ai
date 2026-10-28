"""
batch_importer.py
제주도의회 및 지방의회 회의록 데이터 일괄 임포트 도구
- 지원 형식: .txt, .md, .json, .csv, .xlsx, .hwpx (한글 표준 문서)
- 회의 메타데이터(회의명, 일시) 및 발언자/직책/발언 본문 자동 파싱
- SQLite speeches 테이블 일괄 적재 및 FTS5 자동 인덱싱
"""

import os
import re
import csv
import json
import zipfile
import sqlite3
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jeju_council.db")

# 한국어 지방의회 직책 정규식 패턴
ROLE_PATTERNS = [
    r"위원장", r"부위원장", r"위원", r"도의원", r"의원",
    r"의장", r"부의장",
    r"도지사", r"행정부지사", r"정무부지사",
    r"미래산업국장", r"기획조정실장", r"안전건설국장", r"문화체육교육국장",
    r"농축산식품국장", r"해양수산국장", r"환경보전국장", r"도시균형발전국장",
    r"국장", r"실장", r"과장", r"팀장", r"담당관", r"원장", r"소장", r"총장"
]
ROLE_REGEX = re.compile(r"(" + "|".join(ROLE_PATTERNS) + r")")

# 발언자 시작 기호 및 패턴
# 예: ◯위원장 양영식 성원이 되었으므로...
#     ◯강성의 위원 질의하겠습니다...
#     ◯미래산업국장 신효섭 답변드리겠습니다...
#     [위원장] 양영식: ...
#     신효섭 국장: ...
SPEECH_START_REGEX = re.compile(
    r"^(?:[◯○●◎◆■□▶▷]\s*|\[(?P<bracket_role>[^\]]+)\]\s*)?(?P<name_role>[가-힣]{2,20}(?:\s+[가-힣]{1,10})?)\s*[:：\s]\s*(?P<content>.*)$"
)

def parse_speaker_info(raw_speaker: str) -> tuple[str, str]:
    """발언자 문자열에서 이름과 직책을 분리 (예: '미래산업국장 신효섭' -> ('신효섭', '미래산업국장'))"""
    s = raw_speaker.strip()
    s = re.sub(r"[◯○●◎◆■□▶▷\[\]:：]", "", s).strip()
    tokens = s.split()

    if len(tokens) == 1:
        match = ROLE_REGEX.search(tokens[0])
        if match:
            role = match.group(1)
            name = tokens[0].replace(role, "").strip()
            return name if name else "관계관", role
        return tokens[0], "발언자"

    role_part = []
    name_part = []
    for t in tokens:
        if ROLE_REGEX.search(t):
            role_part.append(t)
        else:
            name_part.append(t)

    name = " ".join(name_part) if name_part else "관계관"
    role = " ".join(role_part) if role_part else "발언자"
    return name, role

def extract_text_from_hwpx(hwpx_path: str) -> str:
    """한글 HWPX (OWPML) 파일 내부의 Section xml에서 순수 텍스트 추출"""
    text_pieces = []
    with zipfile.ZipFile(hwpx_path, 'r') as zf:
        # HWPX의 본문은 Contents/section0.xml, section1.xml 등에 위치함
        section_files = sorted([f for f in zf.namelist() if re.search(r"Contents/section\d+\.xml$", f, re.IGNORECASE)])
        if not section_files:
            # 루트 또는 기타 디렉토리 매칭
            section_files = [f for f in zf.namelist() if f.endswith(".xml") and "section" in f.lower()]

        for sf in section_files:
            with zf.open(sf) as f:
                tree = ET.parse(f)
                root = tree.getroot()
                # 모든 <hp:t> (텍스트 노드) 추출
                for elem in root.iter():
                    if elem.tag.endswith("}t") or elem.tag == "t":
                        if elem.text:
                            text_pieces.append(elem.text)
                    elif elem.tag.endswith("}p") or elem.tag == "p":
                        text_pieces.append("\n")

    return "".join(text_pieces)

def parse_council_text(text: str, default_meeting: str = "제주도의회 회의록", default_date: str = "") -> List[Dict[str, Any]]:
    """
    회의록 텍스트 전문을 분석하여 (회의명, 일시, 발언자, 직책, 발언내용) 리스트로 구조화
    """
    lines = text.splitlines()
    meeting_name = default_meeting
    meeting_date = default_date or datetime.now().strftime("%Y-%m-%d")

    # 1. 문서 헤더에서 회의명 및 일시 추출 시도
    for line in lines[:25]:
        line_clean = line.strip()
        # 회의명 패턴 (예: 제423회 임시회 농수축경제위원회 제1차)
        if re.search(r"제\s*\d+\s*회.*(?:위원회|본회의|특별위원회)", line_clean):
            meeting_name = re.sub(r"\s+", " ", line_clean)
        # 일시 패턴 (예: 2024년 1월 22일)
        date_match = re.search(r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일", line_clean)
        if date_match:
            y, m, d = date_match.groups()
            meeting_date = f"{y}-{int(m):02d}-{int(d):02d}"

    # 2. 발언 단위 분할
    speeches = []
    current_speaker_name = ""
    current_speaker_role = ""
    current_content = []

    def save_current():
        if current_content and (current_speaker_name or current_speaker_role):
            full_content = " ".join(" ".join(current_content).split())
            if len(full_content) >= 15: # 너무 짧은 단순 잡음 제외
                speeches.append({
                    "meeting_name": meeting_name,
                    "meeting_date": meeting_date,
                    "speaker_name": current_speaker_name or "발언자",
                    "speaker_role": current_speaker_role or "의회관계자",
                    "content": full_content
                })

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        # 발언자 시작 기호 패턴 감지
        # 예: ◯위원장 양영식 성원이 되었으므로...
        #     ◯강성의 위원 질의하겠습니다...
        has_symbol = bool(re.match(r"^[◯○●◎◆■□▶▷]\s*", line_str))
        
        # 콜론이나 공백 기반 발언자 감지
        colon_match = re.match(r"^([가-힣]{2,15}(?:\s+[가-힣]{1,10})?)\s*[:：]\s*(.*)$", line_str)
        
        if has_symbol:
            save_current()
            clean_str = re.sub(r"^[◯○●◎◆■□▶▷]\s*", "", line_str)
            tokens = clean_str.split(None, 2)
            if len(tokens) >= 2 and (ROLE_REGEX.search(tokens[0]) or ROLE_REGEX.search(tokens[1])):
                raw_spk = f"{tokens[0]} {tokens[1]}"
                current_speaker_name, current_speaker_role = parse_speaker_info(raw_spk)
                current_content = [tokens[2]] if len(tokens) > 2 else []
            elif len(tokens) >= 1:
                current_speaker_name, current_speaker_role = parse_speaker_info(tokens[0])
                current_content = tokens[1:]
            else:
                current_speaker_name, current_speaker_role = "발언자", "관계관"
                current_content = [clean_str]
        elif colon_match and (ROLE_REGEX.search(colon_match.group(1)) or len(colon_match.group(1).split()) <= 2):
            save_current()
            spk_str = colon_match.group(1)
            current_speaker_name, current_speaker_role = parse_speaker_info(spk_str)
            current_content = [colon_match.group(2)]
        else:
            if current_speaker_name or current_speaker_role:
                current_content.append(line_str)
            else:
                # 발언자 미정 상태의 본문 누적
                pass

    save_current()
    return speeches

def parse_json_file(file_path: str) -> List[Dict[str, Any]]:
    """JSON 파일 파싱 ([{meeting_name, meeting_date, speaker_name, speaker_role, content}, ...] 형식 지원)"""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    speeches = []
    items = data if isinstance(data, list) else data.get("speeches", [data])
    for item in items:
        speeches.append({
            "meeting_name": item.get("meeting_name", "제주도의회 회의록"),
            "meeting_date": item.get("meeting_date", datetime.now().strftime("%Y-%m-%d")),
            "speaker_name": item.get("speaker_name") or item.get("speaker", "발언자"),
            "speaker_role": item.get("speaker_role") or item.get("role", "도의원/관계관"),
            "content": item.get("content") or item.get("text", "")
        })
    return speeches

def parse_csv_file(file_path: str) -> List[Dict[str, Any]]:
    """CSV / TSV 파일 파싱"""
    speeches = []
    with open(file_path, "r", encoding="utf-8-sig") as f:
        delimiter = "\t" if file_path.endswith(".tsv") else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            speeches.append({
                "meeting_name": row.get("meeting_name") or row.get("회의명", "제주도의회 회의록"),
                "meeting_date": row.get("meeting_date") or row.get("일시") or row.get("날짜", datetime.now().strftime("%Y-%m-%d")),
                "speaker_name": row.get("speaker_name") or row.get("발언자", "발언자"),
                "speaker_role": row.get("speaker_role") or row.get("직책", "관계관"),
                "content": row.get("content") or row.get("발언내용") or row.get("내용", "")
            })
    return speeches

def parse_excel_file(file_path: str) -> List[Dict[str, Any]]:
    """Excel (.xlsx) 파일 파싱"""
    try:
        import openpyxl
    except ImportError:
        raise ImportError("openpyxl 패키지가 필요합니다: pip install openpyxl")

    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    speeches = []

    def get_val(row_dict, *keys):
        for k in keys:
            if k in row_dict and row_dict[k]:
                return str(row_dict[k]).strip()
        return ""

    for row_vals in rows[1:]:
        row_dict = dict(zip(header, row_vals))
        content = get_val(row_dict, "content", "발언내용", "내용", "본문")
        if not content:
            continue
        speeches.append({
            "meeting_name": get_val(row_dict, "meeting_name", "회의명", "회의") or "제주도의회 회의록",
            "meeting_date": get_val(row_dict, "meeting_date", "일시", "날짜") or datetime.now().strftime("%Y-%m-%d"),
            "speaker_name": get_val(row_dict, "speaker_name", "발언자", "성명") or "발언자",
            "speaker_role": get_val(row_dict, "speaker_role", "직책", "직위") or "관계관",
            "content": content
        })
    return speeches

def import_speeches_to_db(speeches: List[Dict[str, Any]], db_path: str = DB_PATH) -> int:
    """파싱된 발언 데이터들을 SQLite speeches 테이블에 일괄 INSERT (FTS5 트리거 자동 작동)"""
    if not speeches:
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_name TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        source_url TEXT,
        speech_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(meeting_name, meeting_date)
    )
    """)

    inserted = 0
    meeting_counts = {}
    for s in speeches:
        content = s["content"].strip()
        if not content:
            continue
        m_name = s.get("meeting_name", "제주도의회 회의록")
        m_date = s.get("meeting_date", datetime.now().strftime("%Y-%m-%d"))
        s_url = s.get("source_url", "")

        cursor.execute("""
            INSERT INTO speeches (meeting_name, meeting_date, speaker_name, speaker_role, content, source_url)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            m_name,
            m_date,
            s.get("speaker_name", "발언자"),
            s.get("speaker_role", "관계관"),
            content,
            s_url
        ))
        inserted += 1
        key = (m_name, m_date, s_url)
        meeting_counts[key] = meeting_counts.get(key, 0) + 1

    for (m_name, m_date, s_url), cnt in meeting_counts.items():
        cursor.execute("""
        INSERT INTO meetings (meeting_name, meeting_date, source_url, speech_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(meeting_name, meeting_date) DO UPDATE SET
            source_url = CASE WHEN EXCLUDED.source_url != '' THEN EXCLUDED.source_url ELSE meetings.source_url END,
            speech_count = meetings.speech_count + EXCLUDED.speech_count
        """, (m_name, m_date, s_url, cnt))

    conn.commit()
    conn.close()
    return inserted

def import_file(file_path: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    """단일 파일 확장자에 맞춰 파싱 후 DB에 적재"""
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        speeches = parse_council_text(text, default_meeting=os.path.basename(file_path))
    elif ext == ".hwpx":
        text = extract_text_from_hwpx(file_path)
        speeches = parse_council_text(text, default_meeting=os.path.basename(file_path))
    elif ext == ".json":
        speeches = parse_json_file(file_path)
    elif ext in [".csv", ".tsv"]:
        speeches = parse_csv_file(file_path)
    elif ext in [".xlsx", ".xls"]:
        speeches = parse_excel_file(file_path)
    else:
        raise ValueError(f"지원하지 않는 파일 형식입니다: {ext}")

    count = import_speeches_to_db(speeches, db_path=db_path)
    return {
        "file": file_path,
        "parsed_count": len(speeches),
        "inserted_count": count
    }

def import_directory(dir_path: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    """디렉토리 내 모든 지원 포맷 파일 일괄 임포트"""
    SUPPORTED_EXTS = {".txt", ".md", ".hwpx", ".json", ".csv", ".tsv", ".xlsx"}
    results = []
    total_inserted = 0

    for root, _, files in os.walk(dir_path):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_EXTS:
                full_path = os.path.join(root, f)
                try:
                    res = import_file(full_path, db_path=db_path)
                    results.append(res)
                    total_inserted += res["inserted_count"]
                except Exception as e:
                    results.append({"file": full_path, "error": str(e), "inserted_count": 0})

    return {
        "directory": dir_path,
        "processed_files": len(results),
        "total_inserted": total_inserted,
        "details": results
    }

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="의회 회의록 일괄 임포터 (HWPX, TXT, MD, JSON, CSV, XLSX)")
    parser.add_argument("--input", "-i", type=str, required=True, help="임포트할 파일 또는 디렉토리 경로")
    parser.add_argument("--db", type=str, default=DB_PATH, help=f"SQLite DB 경로 (기본값: {DB_PATH})")

    args = parser.parse_args()

    if os.path.isdir(args.input):
        res = import_directory(args.input, db_path=args.db)
        print(f"[OK] Directory imported: {res['processed_files']} files, {res['total_inserted']} speeches inserted.")
    elif os.path.isfile(args.input):
        res = import_file(args.input, db_path=args.db)
        print(f"[OK] File imported: {res['file']} -> {res['inserted_count']} speeches inserted.")
    else:
        print(f"[ERROR] Path not found: {args.input}")
