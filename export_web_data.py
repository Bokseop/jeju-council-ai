"""
export_web_data.py
SQLite DB(jeju_council.db)의 53,697건 실제 발언과 회의 메타데이터를
웹 프론트엔드 및 GitHub Pages용 최적화 JSON 파일로 추출
"""

import os
import sys
import json
import sqlite3
import re

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

DB_PATH = "jeju_council.db"
WEB_DATA_DIR = os.path.join("data_web")
os.makedirs(WEB_DATA_DIR, exist_ok=True)

print("=" * 60)
print("🌐 [웹용 회의록 데이터 익스포트 시작]")
print("=" * 60)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 1. 회의 메타데이터 추출
cur.execute("""
    SELECT m.meeting_name, m.meeting_date, m.source_url, m.speech_count
    FROM meetings m
    ORDER BY m.meeting_date DESC
""")
meetings = []
for row in cur.fetchall():
    meetings.append({
        "name": row[0],
        "date": row[1],
        "url": row[2] or "https://record.council.jeju.kr/source/minutes/pages/search.html",
        "count": row[3]
    })

with open(os.path.join(WEB_DATA_DIR, "meetings.json"), "w", encoding="utf-8") as f:
    json.dump(meetings, f, ensure_ascii=False, indent=2)
print(f"[*] meetings.json 생성: {len(meetings)}개 회의 메타데이터")

# 2. 전체 발언 데이터 추출 (초경량 key-value 구조)
cur.execute("""
    SELECT s.id, s.meeting_name, s.meeting_date, s.speaker_name, s.speaker_role, s.content, COALESCE(s.source_url, m.source_url, '')
    FROM speeches s
    LEFT JOIN meetings m ON s.meeting_name = m.meeting_name AND s.meeting_date = m.meeting_date
    ORDER BY s.id ASC
""")
speeches = []
for r in cur.fetchall():
    spk_role = r[4] or ""
    spk_name = r[3] or ""
    full_speaker = f"{spk_role} {spk_name}".strip() if spk_role != spk_name else spk_name
    speeches.append({
        "i": r[0],
        "m": r[1],
        "d": r[2],
        "s": full_speaker,
        "c": r[5],
        "u": r[6]
    })

conn.close()

with open(os.path.join(WEB_DATA_DIR, "speeches.json"), "w", encoding="utf-8") as f:
    json.dump(speeches, f, ensure_ascii=False)

speeches_mb = os.path.getsize(os.path.join(WEB_DATA_DIR, "speeches.json")) / (1024 * 1024)
print(f"[*] speeches.json 생성: {len(speeches):,}건 발언록 ({speeches_mb:.2f} MB)")

# 3. 사이트 통계 및 설정 메타데이터
stats = {
    "total_speeches": len(speeches),
    "total_meetings": len(meetings),
    "latest_date": meetings[0]["date"] if meetings else "2026-10-23",
    "earliest_date": meetings[-1]["date"] if meetings else "1991-01-01",
    "version": "1.0.0"
}
with open(os.path.join(WEB_DATA_DIR, "stats.json"), "w", encoding="utf-8") as f:
    json.dump(stats, f, ensure_ascii=False, indent=2)
print(f"[*] stats.json 생성 완료")

print("=" * 60)
print("✅ [웹 데이터 추출 완료]")
print("=" * 60)
