"""
export_web_data.py
SQLite DB(jeju_council.db)의 2024년~2026년 전체 294,780건 공식 회의록 및 발언을
GitHub Pages 및 브라우저 성능 최적화(연도별 청크 + 컴팩트 어레이 인덱싱) 웹 데이터로 익스포트
"""

import os
import sys
import json
import sqlite3

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

DB_PATH = "jeju_council.db"
WEB_DATA_DIR = "data_web"
os.makedirs(WEB_DATA_DIR, exist_ok=True)

print("=" * 60)
print("🌐 [2024~2026 제주도의회 전체 회의록 웹 데이터 익스포트]")
print("=" * 60)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 1. 회의 메타데이터 추출 (전체 고유 회의)
cur.execute("""
    SELECT meeting_name, meeting_date, source_url, count(*)
    FROM speeches
    GROUP BY meeting_name, meeting_date
    ORDER BY meeting_date DESC
""")
meeting_rows = cur.fetchall()
meeting_map = {}
meetings = []

for idx, (m_name, m_date, s_url, count) in enumerate(meeting_rows):
    meeting_map[(m_name, m_date)] = idx
    meetings.append({
        "id": idx,
        "name": m_name,
        "date": m_date,
        "url": s_url or "https://record.council.jeju.kr/source/minutes/pages/search.html",
        "count": count
    })

with open(os.path.join(WEB_DATA_DIR, "meetings.json"), "w", encoding="utf-8") as f:
    json.dump(meetings, f, ensure_ascii=False, indent=2)
print(f"[*] meetings.json 생성: {len(meetings):,}개 회의 메타데이터")

# 2. 연도별 초경량 컴팩트 발언 데이터 추출: [id, meeting_idx, speaker, content]
years = ["2026", "2025", "2024"]
total_speeches = 0
year_counts = {}

for yr in years:
    cur.execute("""
        SELECT id, meeting_name, meeting_date, speaker_name, speaker_role, content
        FROM speeches
        WHERE substr(meeting_date, 1, 4) = ?
        ORDER BY id ASC
    """, (yr,))
    rows = cur.fetchall()

    compact_speeches = []
    for r in rows:
        spk_role = r[4] or ""
        spk_name = r[3] or ""
        speaker = f"{spk_role} {spk_name}".strip() if spk_role != spk_name else spk_name
        m_idx = meeting_map.get((r[1], r[2]), -1)
        compact_speeches.append([r[0], m_idx, speaker, r[5]])

    fn = os.path.join(WEB_DATA_DIR, f"speeches_{yr}.json")
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(compact_speeches, f, ensure_ascii=False)

    sz_mb = os.path.getsize(fn) / (1024 * 1024)
    print(f"[*] speeches_{yr}.json 생성: {len(compact_speeches):,}건 발언 ({sz_mb:.2f} MB)")
    total_speeches += len(compact_speeches)
    year_counts[yr] = len(compact_speeches)

# 3. 매니페스트 및 통계 생성
manifest = {
    "years": years,
    "year_counts": year_counts,
    "total_speeches": total_speeches,
    "total_meetings": len(meetings),
    "latest_date": meetings[0]["date"] if meetings else "2026-10-23",
    "earliest_date": meetings[-1]["date"] if meetings else "2024-01-01",
    "version": "4.0.0"
}
with open(os.path.join(WEB_DATA_DIR, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

with open(os.path.join(WEB_DATA_DIR, "stats.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

conn.close()

print("=" * 60)
print(f"✅ [웹 데이터 추출 완료: 총 {total_speeches:,}건 발언 / {len(meetings):,}개 회의]")
print("=" * 60)
