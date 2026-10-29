"""
crawl_and_update_web.py
제주특별자치도의회 전자회의록 공식 시스템 (record.council.jeju.kr) 자동 수집 및 웹 데이터 실시간 갱신
- GitHub Actions 스케줄(매일 아침 06:00 KST) 및 로컬 환경에서 100% 무결점 실행
- 대용량 SQLite DB 의존성 없이 data_web/ JSON 데이터를 지능형 증분 갱신
- 신규 회의 발견 시에만 본문 발언을 추출하여 speeches_YYYY.json, meetings.json, manifest.json 즉시 갱신
- 로컬에 jeju_council.db가 존재하는 경우 SQLite DB에도 자동 동시 적재
"""

import os
import re
import sys
import json
import time
import argparse
from datetime import datetime
from urllib.parse import urljoin
from typing import List, Dict, Any, Tuple

# Windows 콘솔 utf-8 출력 설정
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from bs4 import BeautifulSoup

BASE_URL = "https://record.council.jeju.kr"
LATE_URL = f"{BASE_URL}/source/minutes/pages/late.html"
WEB_DATA_DIR = "data_web"
DB_PATH = "jeju_council.db"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"
}

def safe_get(url: str, timeout: int = 15) -> requests.Response:
    """SSL 검증 실패 시 자동 폴백 요청"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.encoding = "euc-kr"
        return r
    except requests.exceptions.SSLError:
        r = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
        r.encoding = "euc-kr"
        return r

def get_recent_meetings_from_web(max_pages: int = 3) -> List[Dict[str, str]]:
    """최근 회의록(late.html) 목록을 페이지별로 탐색하여 회의 기본 정보 수집"""
    print(f"[*] 최근 회의록 목록 조회: {LATE_URL}")
    meetings = []
    seen_links = set()

    def parse_rows(soup):
        found = []
        table = soup.find("table")
        if not table:
            return found
        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) >= 6:
                num = tds[0].get_text(strip=True)
                daesu = tds[1].get_text(strip=True)
                session = tds[2].get_text(strip=True)
                a_tag = tds[3].find("a")
                raw_title = a_tag.get_text(" ", strip=True) if a_tag else tds[3].get_text(strip=True)
                link = urljoin(BASE_URL, a_tag.get("href")) if a_tag else ""
                chasu = tds[4].get_text(strip=True)
                date_raw = tds[5].get_text(strip=True)

                if not link or link in seen_links:
                    continue
                seen_links.add(link)

                m_date = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", date_raw)
                clean_date = f"{m_date.group(1)}-{int(m_date.group(2)):02d}-{int(m_date.group(3)):02d}" if m_date else date_raw
                clean_title = raw_title.replace("[", "").replace("]", "").replace("임시회의록", "").replace("정례회의록", "").strip()
                clean_full_name = f"{daesu} {session} {clean_title} {chasu}".strip()
                clean_full_name = re.sub(r"\s+", " ", clean_full_name)

                found.append({
                    "num": num,
                    "name": clean_full_name,
                    "date": clean_date,
                    "link": link
                })
        return found

    try:
        r1 = safe_get(LATE_URL, timeout=12)
        soup1 = BeautifulSoup(r1.text, "html.parser")
        meetings.extend(parse_rows(soup1))
    except Exception as e:
        print(f"[!] 1페이지 조회 실패: {e}")
        return []

    page_urls = []
    for a in soup1.find_all("a"):
        href = a.get("href", "")
        if "nowpage=" in href:
            full_p = urljoin(LATE_URL, href)
            if full_p not in page_urls:
                page_urls.append(full_p)

    for p_idx, p_url in enumerate(page_urls[:max_pages - 1], start=2):
        try:
            time.sleep(0.2)
            rp = safe_get(p_url, timeout=12)
            soupp = BeautifulSoup(rp.text, "html.parser")
            rows = parse_rows(soupp)
            meetings.extend(rows)
        except Exception as e:
            print(f"[!] {p_idx}페이지 조회 오류: {e}")

    return meetings

def parse_meeting_canvas_speeches(detail_url: str) -> List[Dict[str, str]]:
    """상세 회의록 뷰어(/CLRecords/Retrieval2/index.php?hfile=...)에서 발언 파싱"""
    try:
        r = safe_get(detail_url, timeout=15)
    except Exception as e:
        print(f"[!] 상세 페이지 요청 실패 ({detail_url}): {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    canvas = soup.find(id="canvas") or soup.find("div", class_="content") or soup.find("body")
    if not canvas:
        return []

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
        if len(content_clean) < 10:
            continue

        speeches.append({
            "speaker_name": speaker_name,
            "speaker_role": speaker_role,
            "content": content_clean
        })

    return speeches

def update_local_db_if_exists(new_meetings_data: List[Dict[str, Any]], new_speeches_data: List[Dict[str, Any]]):
    """로컬 SQLite DB(jeju_council.db)가 존재하는 경우 동시 업데이트"""
    if not os.path.exists(DB_PATH):
        return

    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

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

        for s in new_speeches_data:
            cur.execute("""
                INSERT INTO speeches (meeting_name, meeting_date, speaker_name, speaker_role, content, source_url)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (s["meeting_name"], s["meeting_date"], s["speaker_name"], s["speaker_role"], s["content"], s["source_url"]))

        for m in new_meetings_data:
            cur.execute("""
                INSERT INTO meetings (meeting_name, meeting_date, source_url, speech_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(meeting_name, meeting_date) DO UPDATE SET
                    source_url = CASE WHEN EXCLUDED.source_url != '' THEN EXCLUDED.source_url ELSE meetings.source_url END,
                    speech_count = meetings.speech_count + EXCLUDED.speech_count
            """, (m["name"], m["date"], m["url"], m["count"]))

        conn.commit()
        conn.close()
        print(f"[*] 로컬 DB({DB_PATH}) 동기화 완료: 발언 {len(new_speeches_data)}건 추가")
    except Exception as e:
        print(f"[!] 로컬 DB 동기화 오류 (웹 데이터는 정상 갱신됨): {e}")

def run_update(max_pages: int = 3, dry_run: bool = False):
    print("=" * 70)
    print("🌐 [제주특별자치도의회 회의록 자동 수집 및 웹 데이터 갱신 시스템]")
    print(f"   - 대상 시스템: {BASE_URL}")
    print(f"   - 데이터 디렉토리: {WEB_DATA_DIR}/")
    print(f"   - 점검 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    meetings_file = os.path.join(WEB_DATA_DIR, "meetings.json")
    manifest_file = os.path.join(WEB_DATA_DIR, "manifest.json")
    stats_file = os.path.join(WEB_DATA_DIR, "stats.json")

    if not os.path.exists(meetings_file):
        print(f"[!] {meetings_file} 파일이 없습니다. 기본 데이터가 필요합니다.")
        sys.exit(1)

    with open(meetings_file, "r", encoding="utf-8") as f:
        existing_meetings = json.load(f)

    existing_set = set((m["name"], m["date"]) for m in existing_meetings)
    print(f"[*] 현재 수집된 회의 수: {len(existing_meetings):,}개")

    # 최근 회의록 목록 조회
    scraped_meetings = get_recent_meetings_from_web(max_pages=max_pages)
    if not scraped_meetings:
        print("[!] 최근 회의록 목록을 불러오지 못했습니다.")
        return

    # 신규 회의 식별 (기존에 없는 회의만 선별)
    new_candidates = []
    for m in scraped_meetings:
        key = (m["name"], m["date"])
        if key not in existing_set:
            new_candidates.append(m)

    print(f"[*] 최근 회의 {len(scraped_meetings)}건 중 신규 등록 회의: {len(new_candidates)}건")

    if not new_candidates:
        print("\n" + "=" * 70)
        print("✅ [점검 완료] 모든 회의록이 이미 최신 상태입니다. (신규 수집 대상 0건)")
        print("=" * 70)
        return

    if dry_run:
        print(f"[*] [Dry Run] 수집 대상 {len(new_candidates)}건 발견:")
        for m in new_candidates:
            print(f"   - {m['date']} | {m['name']} ({m['link']})")
        return

    # 신규 회의들을 오래된 순서대로 정렬하여 순차적으로 ID 부여
    new_candidates.sort(key=lambda x: x["date"])

    # 기존 발언 최대 ID 및 연도별 파일 로드
    years_loaded = {}
    max_speech_id = 0

    for yr in ["2026", "2025", "2024"]:
        fn = os.path.join(WEB_DATA_DIR, f"speeches_{yr}.json")
        if os.path.exists(fn):
            with open(fn, "r", encoding="utf-8") as f:
                data = json.load(f)
                years_loaded[yr] = data
                if data:
                    max_speech_id = max(max_speech_id, max(s[0] for s in data))
        else:
            years_loaded[yr] = []

    print(f"[*] 현재 최대 발언 ID: {max_speech_id}")

    newly_added_meetings = []
    all_new_db_speeches = []
    current_speech_id = max_speech_id

    for idx, m in enumerate(new_candidates, start=1):
        m_name = m["name"]
        m_date = m["date"]
        m_link = m["link"]
        m_year = m_date[:4]

        print(f"\n[{idx}/{len(new_candidates)}] 신규 회의 발언 수집: {m_name} ({m_date})")
        speeches = parse_meeting_canvas_speeches(m_link)
        print(f"    -> 실제 발언 {len(speeches)}건 파싱 완료")

        if not speeches:
            print(f"    -> [주의] 본문 발언이 없어 건너뜁니다.")
            continue

        new_meeting_idx = len(existing_meetings)
        meeting_entry = {
            "id": new_meeting_idx,
            "name": m_name,
            "date": m_date,
            "url": m_link,
            "count": len(speeches)
        }
        existing_meetings.append(meeting_entry)
        existing_set.add((m_name, m_date))
        newly_added_meetings.append(meeting_entry)

        if m_year not in years_loaded:
            years_loaded[m_year] = []

        for s in speeches:
            current_speech_id += 1
            spk_role = s.get("speaker_role", "")
            spk_name = s.get("speaker_name", "")
            speaker = f"{spk_role} {spk_name}".strip() if spk_role != spk_name else spk_name

            # Compact format: [id, meeting_idx, speaker, content]
            compact_item = [current_speech_id, new_meeting_idx, speaker, s["content"]]
            years_loaded[m_year].append(compact_item)

            all_new_db_speeches.append({
                "meeting_name": m_name,
                "meeting_date": m_date,
                "speaker_name": spk_name,
                "speaker_role": spk_role,
                "content": s["content"],
                "source_url": m_link
            })

        time.sleep(0.3)

    if not newly_added_meetings:
        print("[!] 추가된 발언이 없습니다.")
        return

    # 파일 저장
    # 1. meetings.json 갱신
    with open(meetings_file, "w", encoding="utf-8") as f:
        json.dump(existing_meetings, f, ensure_ascii=False, indent=2)
    print(f"\n[*] meetings.json 갱신 완료 (총 {len(existing_meetings)}개 회의)")

    # 2. speeches_YYYY.json 갱신
    for yr, data in years_loaded.items():
        fn = os.path.join(WEB_DATA_DIR, f"speeches_{yr}.json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        sz_mb = os.path.getsize(fn) / (1024 * 1024)
        print(f"[*] speeches_{yr}.json 갱신 완료: {len(data):,}건 ({sz_mb:.2f} MB)")

    # 3. manifest.json & stats.json 갱신
    total_speeches = sum(len(d) for d in years_loaded.values())
    year_counts = {yr: len(d) for yr, d in years_loaded.items()}
    all_dates = [m["date"] for m in existing_meetings if m.get("date")]
    latest_date = max(all_dates) if all_dates else datetime.now().strftime("%Y-%m-%d")
    earliest_date = min(all_dates) if all_dates else "2024-01-01"

    manifest = {
        "years": sorted(list(years_loaded.keys()), reverse=True),
        "year_counts": year_counts,
        "total_speeches": total_speeches,
        "total_meetings": len(existing_meetings),
        "latest_date": latest_date,
        "earliest_date": earliest_date,
        "version": "4.1.0",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"[*] manifest.json / stats.json 갱신 완료 (총 {total_speeches:,}건 발언)")

    # 4. 로컬 DB 동기화
    update_local_db_if_exists(newly_added_meetings, all_new_db_speeches)

    print("\n" + "=" * 70)
    print(f"🎉 [성공] 신규 회의 {len(newly_added_meetings)}건 및 발언 {len(all_new_db_speeches):,}건 수집 및 웹 데이터 갱신 완료!")
    print("=" * 70)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="제주도의회 회의록 자동 수집 및 웹 데이터 갱신")
    parser.add_argument("--max-pages", type=int, default=3, help="조회할 최근 페이지 수")
    parser.add_argument("--dry-run", action="store_true", help="수집 대상 회의만 출력하고 저장하지 않음")
    args = parser.parse_args()

    run_update(max_pages=args.max_pages, dry_run=args.dry_run)
