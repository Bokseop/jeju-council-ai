"""
crawl_2024_to_recent.py
2024년 1월부터 최근 회기까지 제주특별자치도의회 전체 회의록을 고속 병렬 수집하여
jeju_council.db에 적재하고 웹 배포용 데이터를 생성하는 스크립트
"""

import os
import re
import sys
import time
import sqlite3
import requests
import concurrent.futures
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from typing import List, Dict, Any

# Windows console utf-8 configuration
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

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jeju_council.db")
BASE_URL = "https://record.council.jeju.kr"
YEAR_URL = f"{BASE_URL}/source/minutes/pages/year.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"
}

def init_db(db_path: str = DB_PATH):
    """DB 초기화 및 테이블 생성 (WAL 모드 적용)"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS speeches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_name TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        speaker_name TEXT,
        speaker_role TEXT,
        content TEXT NOT NULL,
        source_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meeting_name TEXT NOT NULL,
        meeting_date TEXT NOT NULL,
        source_url TEXT,
        speech_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(meeting_name, meeting_date)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_speeches_meeting ON speeches(meeting_name, meeting_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_speeches_source_url ON speeches(source_url);")
    conn.commit()
    conn.close()

def get_existing_meeting_urls(db_path: str = DB_PATH) -> set:
    """이미 DB에 수집된 회의 URL 집합 반환"""
    if not os.path.exists(db_path):
        return set()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT source_url FROM speeches WHERE source_url IS NOT NULL AND source_url != ''")
    urls = set(row[0] for row in cur.fetchall())
    conn.close()
    return urls

def collect_all_meeting_targets(years: List[int] = [2024, 2025, 2026]) -> List[Dict[str, str]]:
    """지정 연도의 모든 개별 회의 링크 탐색 및 정규화"""
    print(f"[*] 대상 연도 탐색: {years}")
    all_meetings = []
    seen_urls = set()

    for y in years:
        year_url = f"{YEAR_URL}?year={y}&tag=yth"
        try:
            r = requests.get(year_url, headers=HEADERS, timeout=15)
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print(f"[!] {y}년도 메인 페이지 로드 실패: {e}")
            continue

        session_links = []
        for a in soup.find_all("a"):
            href = a.get("href", "")
            txt = a.get_text(" ", strip=True)
            if "tag=ycha" in href and "th=" in href:
                session_links.append((txt, urljoin(YEAR_URL, href)))

        print(f"  - [{y}년도] 총 {len(session_links)}개 회기 발견")

        for s_title, s_url in session_links:
            clean_session = re.sub(r"\(.*?\)", "", s_title).strip()
            clean_session = re.sub(r"\s+", " ", clean_session)
            try:
                time.sleep(0.05)
                rs = requests.get(s_url, headers=HEADERS, timeout=15)
                rs.encoding = "euc-kr"
                soups = BeautifulSoup(rs.text, "html.parser")

                for a in soups.find_all("a"):
                    h = a.get("href", "")
                    t = a.get_text(" ", strip=True)
                    if "Retrieval2" in h:
                        full_url = urljoin(BASE_URL, h)
                        if full_url in seen_urls:
                            continue
                        seen_urls.add(full_url)

                        m_d = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", t)
                        m_date = f"{m_d.group(1)}-{int(m_d.group(2)):02d}-{int(m_d.group(3)):02d}" if m_d else ""
                        clean_t = re.sub(r"\(.*?\)", "", t).strip()
                        clean_t = re.sub(r"\s+", " ", clean_t)
                        meeting_name = f"{clean_session} {clean_t}".strip()

                        all_meetings.append({
                            "year": y,
                            "name": meeting_name,
                            "date": m_date,
                            "url": full_url
                        })
            except Exception as e:
                print(f"    [!] 회기({s_title}) 세부 목록 로드 실패: {e}")

    print(f"[*] 총 {len(all_meetings)}개 공식 회의 목록 확보 완료")
    return all_meetings

def parse_single_meeting(target: Dict[str, str]) -> Dict[str, Any]:
    """단일 회의의 본문 HTML을 가져와 발언 리스트 파싱"""
    url = target["url"]
    name = target["name"]
    date = target["date"]
    year = target["year"]

    retries = 3
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.encoding = "euc-kr"
            break
        except Exception:
            if attempt == retries - 1:
                return {"target": target, "speeches": [], "error": "timeout"}
            time.sleep(0.5)

    soup = BeautifulSoup(r.text, "html.parser")
    canvas = soup.find(id="canvas") or soup.find("div", class_="content") or soup.find("body")
    if not canvas:
        return {"target": target, "speeches": [], "error": "no canvas"}

    for br in canvas.find_all("br"):
        br.replace_with("\n")

    for strong in canvas.find_all("strong"):
        txt = strong.get_text().strip()
        if txt.startswith("○") or txt.startswith("◯"):
            strong.insert_before("\n###SPEAKER###")
            strong.insert_after("###ENDSPEAKER###\n")

    raw_text = canvas.get_text().replace("\xa0", " ")
    blocks = raw_text.split("###SPEAKER###")
    speeches = []

    for b in blocks[1:]:
        if "###ENDSPEAKER###" not in b:
            continue
        spk_part, body_part = b.split("###ENDSPEAKER###", 1)
        speaker_raw = spk_part.replace("○", "").replace("◯", "").strip()

        if any(skip in speaker_raw for skip in ["출석위원", "출석전문위원", "피감사기관참석자", "기록", "출석공무원", "참석공무원", "서명공무원"]):
            continue

        tokens = speaker_raw.split()
        if not tokens:
            continue

        if len(tokens) == 1:
            speaker_name = tokens[0]
            speaker_role = tokens[0]
        elif len(tokens) == 2:
            if any(r in tokens[0] for r in ["국장", "원장", "위원장", "과장", "도지사", "소장", "감독관", "이사장", "의장", "위원", "실장", "관"]):
                speaker_role = tokens[0]
                speaker_name = tokens[1]
            elif any(r in tokens[1] for r in ["국장", "원장", "위원장", "과장", "도지사", "소장", "감독관", "이사장", "의장", "위원", "실장", "의원"]):
                speaker_name = tokens[0]
                speaker_role = tokens[1]
            else:
                speaker_name = tokens[1]
                speaker_role = tokens[0]
        else:
            speaker_name = tokens[-1]
            speaker_role = " ".join(tokens[:-1])

        content_clean = re.sub(r"\s+", " ", body_part).strip()
        if len(content_clean) < 8:
            continue

        speeches.append({
            "meeting_name": name,
            "meeting_date": date,
            "speaker_name": speaker_name,
            "speaker_role": speaker_role,
            "content": content_clean,
            "source_url": url,
            "year": year
        })

    return {"target": target, "speeches": speeches, "error": None}

def save_batch_to_db(parsed_batch: List[Dict[str, Any]], db_path: str = DB_PATH) -> int:
    """파싱된 발언 묶음을 SQLite에 트랜잭션으로 일괄 저장"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    total_inserted = 0
    meetings_to_upsert = {}

    for item in parsed_batch:
        speeches = item.get("speeches", [])
        if not speeches:
            continue

        for s in speeches:
            cur.execute("""
                INSERT INTO speeches (meeting_name, meeting_date, speaker_name, speaker_role, content, source_url)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                s["meeting_name"],
                s["meeting_date"],
                s["speaker_name"],
                s["speaker_role"],
                s["content"],
                s["source_url"]
            ))
            total_inserted += 1

        t = item["target"]
        key = (t["name"], t["date"], t["url"])
        meetings_to_upsert[key] = meetings_to_upsert.get(key, 0) + len(speeches)

    for (m_name, m_date, s_url), cnt in meetings_to_upsert.items():
        cur.execute("""
            INSERT INTO meetings (meeting_name, meeting_date, source_url, speech_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(meeting_name, meeting_date) DO UPDATE SET
                source_url = CASE WHEN EXCLUDED.source_url != '' THEN EXCLUDED.source_url ELSE meetings.source_url END,
                speech_count = meetings.speech_count + EXCLUDED.speech_count
        """, (m_name, m_date, s_url, cnt))

    conn.commit()
    conn.close()
    return total_inserted

def run_pipeline(max_workers: int = 8):
    start_time = time.time()
    print("=" * 70)
    print("🚀 [2024~현재 제주도의회 전자회의록 전수 고속 수집 파이프라인 가동]")
    print("=" * 70)

    init_db(DB_PATH)
    existing_urls = get_existing_meeting_urls(DB_PATH)
    print(f"[*] 기존 DB 적재 회의 수: {len(existing_urls):,}건")

    all_targets = collect_all_meeting_targets(years=[2024, 2025, 2026])
    targets_to_crawl = [t for t in all_targets if t["url"] not in existing_urls]
    print(f"[*] 신규 수집 대상 회의: {len(targets_to_crawl):,}건 (총 {len(all_targets)}건 중)")

    if not targets_to_crawl:
        print("✅ 모든 회의가 이미 DB에 최신 상태로 적재되어 있습니다.")
        return

    total_speeches_added = 0
    batch_size = 20
    current_batch = []
    completed_meetings = 0

    print(f"\n[*] 병렬 수집 시작 (워커 수: {max_workers})...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {executor.submit(parse_single_meeting, t): t for t in targets_to_crawl}

        for future in concurrent.futures.as_completed(future_to_target):
            try:
                res = future.result()
                completed_meetings += 1
                current_batch.append(res)

                if len(current_batch) >= batch_size or completed_meetings == len(targets_to_crawl):
                    inserted = save_batch_to_db(current_batch, DB_PATH)
                    total_speeches_added += inserted
                    pct = (completed_meetings / len(targets_to_crawl)) * 100
                    elapsed = time.time() - start_time
                    print(f"  [{completed_meetings}/{len(targets_to_crawl)}] ({pct:.1f}%) "
                          f"현재까지 {total_speeches_added:,}건 적재 완료 (경과: {elapsed:.0f}초)")
                    current_batch = []

            except Exception as e:
                print(f"[!] 스레드 처리 오류: {e}")

    elapsed_total = time.time() - start_time
    print("\n" + "=" * 70)
    print(f"🎉 [수집 완료]")
    print(f"  - 신규 수집 회의: {completed_meetings:,}건")
    print(f"  - 신규 적재 발언: {total_speeches_added:,}건")
    print(f"  - 총 소요 시간: {elapsed_total:.1f}초")
    print("=" * 70)

if __name__ == "__main__":
    run_pipeline(max_workers=8)
