"""
sync_meeting_urls.py
DB에 적재된 회의들의 공식 도의회 전자회의록 웹 링크(source_url)를 동기화하고 meetings 메타 테이블을 구축
"""

import sys
import os
import re
import sqlite3
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import requests
from bs4 import BeautifulSoup
from crawler import get_recent_meeting_list, get_meetings_by_date_range, BASE_URL

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jeju_council.db")

def sync_urls(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS speeches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_name TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        speaker_name TEXT NOT NULL,
        speaker_role TEXT,
        content TEXT NOT NULL,
        source_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("SELECT DISTINCT meeting_name, meeting_date FROM speeches")
    db_meetings = cur.fetchall()
    print(f"[*] DB 내 고유 회의 수: {len(db_meetings)}개")

    # 1. 최근 회기(late.html) 전체 수집
    print("[*] 최근 회의록 웹 목록 수집 중...")
    web_meetings = get_recent_meeting_list(max_pages=5)
    print(f"[*] 최근 회기에서 {len(web_meetings)}개 회의 링크 확보")

    # 2. 2026년도 전체 회의록 목록 수집
    print("[*] 2026년도 회기 링크 수집 중...")
    y2026 = get_meetings_by_date_range("2026-01-01", "2026-12-31")
    web_meetings.extend(y2026)
    print(f"[*] 누적 확보 회의: {len(web_meetings)}개")

    # 3. 2024년 10월 등 수집된 적 있는 연도 수집
    print("[*] 2024년 10월 회기 링크 수집 중...")
    y2024 = get_meetings_by_date_range("2024-10-01", "2024-10-31")
    web_meetings.extend(y2024)

    # 링크 매핑 딕셔너리 구축: (date, normalized_name) -> link
    link_map = {}
    for m in web_meetings:
        link = m.get("link")
        if not link:
            continue
        clean_n = re.sub(r"\s+", "", m["name"])
        link_map[(m["date"], clean_n)] = link

    updated_count = 0
    for name, date in db_meetings:
        # 발언 수 카운트
        cur.execute("SELECT COUNT(*) FROM speeches WHERE meeting_name = ? AND meeting_date = ?", (name, date))
        speech_count = cur.fetchone()[0]

        clean_db_n = re.sub(r"\s+", "", name)
        matched_url = None

        # 1. 정확 일치
        if (date, clean_db_n) in link_map:
            matched_url = link_map[(date, clean_db_n)]
        else:
            # 2. 부분 일치 검색
            for (m_date, m_name), m_link in link_map.items():
                if m_date == date and (clean_db_n in m_name or m_name in clean_db_n):
                    matched_url = m_link
                    break

        if not matched_url:
            # 기본 제주도의회 회의록 포털 링크
            matched_url = f"{BASE_URL}/source/minutes/pages/search.html"

        cur.execute("""
        INSERT INTO meetings (meeting_name, meeting_date, source_url, speech_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(meeting_name, meeting_date) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            speech_count = EXCLUDED.speech_count
        """, (name, date, matched_url, speech_count))

        cur.execute("""
        UPDATE speeches SET source_url = ? WHERE meeting_name = ? AND meeting_date = ?
        """, (matched_url, name, date))

        if "Retrieval2" in matched_url:
            updated_count += 1

    conn.commit()
    conn.close()

    print("=" * 60)
    print(f"✅ [동기화 완료] 총 {len(db_meetings)}개 회의 중 공식 직통 링크 매핑: {updated_count}개")
    print(f"   (미매핑 회의는 공식 회의록 검색 포털로 기본 연결 설정)")
    print("=" * 60)

if __name__ == "__main__":
    sync_urls()
