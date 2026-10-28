"""
crawler.py
제주특별자치도의회 전자회의록 공식 시스템 (record.council.jeju.kr) 100% 실시간 공식 크롤러
- 솔루션: (주)제윤 전자회의록 시스템
- 기능 1: 일자별/월별/연도별 과거 회기 전수 탐색 (/source/minutes/pages/year.html)
- 기능 2: 최근 회기 실시간 고속 수집 (/source/minutes/pages/late.html)
- 기능 3: 상세 본문 (/CLRecords/Retrieval2/index.php?hfile=...) 구조화 발언 파싱
- 기능 4: SQLite FTS5 스마트 증분 적재 (기수집 건너뜀, 신규 회의만 선별 적재)
"""

import os
import re
import sys
import time
import sqlite3
import argparse
from urllib.parse import urljoin
from typing import List, Dict, Any, Optional, Callable

# Windows 콘솔 utf-8 출력 설정
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
from batch_importer import import_speeches_to_db

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jeju_council.db")
BASE_URL = "https://record.council.jeju.kr"
LATE_URL = f"{BASE_URL}/source/minutes/pages/late.html"
YEAR_URL = f"{BASE_URL}/source/minutes/pages/year.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"
}

def is_already_collected(meeting_name: str, meeting_date: str, db_path: str = DB_PATH) -> bool:
    """[증분 업데이트] 이미 수집된 회의인지 확인"""
    if not os.path.exists(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM speeches WHERE meeting_name = ? AND meeting_date = ? LIMIT 1", (meeting_name, meeting_date))
        exists = cur.fetchone() is not None
        conn.close()
        return exists
    except Exception:
        return False

def get_recent_meeting_list(max_pages: int = 10, emit: Optional[Callable[[str], None]] = None) -> List[Dict[str, str]]:
    """
    최근 회의록(late.html)에서 페이징을 순회하며 실제 회의 메타데이터와 본문 링크 수집
    """
    def log(msg):
        if emit:
            emit(msg)
        else:
            print(msg)

    meetings = []
    seen_links = set()

    def parse_table_rows(soup):
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

    log(f"[*] 최근 회의록 목록 1페이지 요청: {LATE_URL}")
    try:
        r1 = requests.get(LATE_URL, headers=HEADERS, timeout=12)
        r1.encoding = "euc-kr"
        soup1 = BeautifulSoup(r1.text, "html.parser")
        meetings.extend(parse_table_rows(soup1))
    except Exception as e:
        log(f"[!] 1페이지 요청 실패: {e}")
        return []

    page_urls = []
    for a in soup1.find_all("a"):
        href = a.get("href", "")
        if "nowpage=" in href:
            full_p = urljoin(LATE_URL, href)
            if full_p not in page_urls:
                page_urls.append(full_p)

    log(f"[*] 1페이지에서 {len(meetings)}건 발견. 추가 페이지 {len(page_urls)}개 탐색 시작...")

    for p_idx, p_url in enumerate(page_urls[:max_pages - 1], start=2):
        try:
            time.sleep(0.2)
            rp = requests.get(p_url, headers=HEADERS, timeout=12)
            rp.encoding = "euc-kr"
            soupp = BeautifulSoup(rp.text, "html.parser")
            rows = parse_table_rows(soupp)
            meetings.extend(rows)
            log(f"    - [{p_idx}페이지] {len(rows)}건 추가 (누적 {len(meetings)}건)")
        except Exception as e:
            log(f"    [!] {p_idx}페이지 수집 오류: {e}")

    return meetings

def get_meetings_by_date_range(
    start_date: str,
    end_date: str,
    emit: Optional[Callable[[str], None]] = None
) -> List[Dict[str, str]]:
    """
    지정한 일자 범위(start_date ~ end_date, 예: '2024-10-01' ~ '2024-10-31')에 해당하는
    모든 과거 회기/위원회의 개별 회의 링크를 연도별 페이지(year.html)에서 탐색
    """
    def log(msg):
        if emit:
            emit(msg)
        else:
            print(msg)

    # 날짜 유효성 검사 및 정규화
    try:
        s_parts = [int(p) for p in start_date.strip().split("-")]
        e_parts = [int(p) for p in end_date.strip().split("-")]
        start_year = s_parts[0]
        end_year = e_parts[0]
        norm_start = f"{s_parts[0]:04d}-{s_parts[1]:02d}-{s_parts[2]:02d}"
        norm_end = f"{e_parts[0]:04d}-{e_parts[1]:02d}-{e_parts[2]:02d}"
    except Exception as e:
        log(f"[!] 날짜 형식 오류 (YYYY-MM-DD 필요): {e}")
        return []

    log(f"[*] 연도별/월별 회의 탐색: {norm_start} ~ {norm_end} (연도: {start_year}~{end_year})")

    matched_meetings = []
    seen_links = set()

    for y in range(start_year, end_year + 1):
        year_url = f"{YEAR_URL}?year={y}&tag=yth"
        try:
            r = requests.get(year_url, headers=HEADERS, timeout=12)
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log(f"[!] {y}년도 페이지 요청 실패: {e}")
            continue

        session_links = []
        for a in soup.find_all("a"):
            href = a.get("href", "")
            txt = a.get_text(" ", strip=True)
            if "tag=ycha" in href and "th=" in href:
                m_dates = re.findall(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", txt)
                if len(m_dates) >= 2:
                    s_d = f"{m_dates[0][0]}-{int(m_dates[0][1]):02d}-{int(m_dates[0][2]):02d}"
                    e_d = f"{m_dates[1][0]}-{int(m_dates[1][1]):02d}-{int(m_dates[1][2]):02d}"
                    if not (e_d < norm_start or s_d > norm_end):
                        session_links.append((txt, urljoin(YEAR_URL, href)))
                else:
                    session_links.append((txt, urljoin(YEAR_URL, href)))

        log(f"[*] [{y}년도] 기간 부합 회기: {len(session_links)}개 발견")

        for s_title, s_url in session_links:
            try:
                time.sleep(0.15)
                rs = requests.get(s_url, headers=HEADERS, timeout=12)
                rs.encoding = "euc-kr"
                soups = BeautifulSoup(rs.text, "html.parser")

                clean_session_title = re.sub(r"\(.*?\)", "", s_title).strip()

                for a in soups.find_all("a"):
                    h = a.get("href", "")
                    t = a.get_text(" ", strip=True)
                    if "Retrieval2" in h:
                        m_d = re.search(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", t)
                        if m_d:
                            meeting_date = f"{m_d.group(1)}-{int(m_d.group(2)):02d}-{int(m_d.group(3)):02d}"
                            if norm_start <= meeting_date <= norm_end:
                                full_url = urljoin(BASE_URL, h)
                                if full_url in seen_links:
                                    continue
                                seen_links.add(full_url)

                                t_clean = re.sub(r"\(.*?\)", "", t).strip()
                                clean_meeting_name = f"{clean_session_title} {t_clean}".strip()
                                clean_meeting_name = re.sub(r"\s+", " ", clean_meeting_name)

                                matched_meetings.append({
                                    "name": clean_meeting_name,
                                    "date": meeting_date,
                                    "link": full_url
                                })
            except Exception as e:
                log(f"    [!] 회기({s_title}) 파싱 오류: {e}")

    log(f"[*] 지정 기간({norm_start} ~ {norm_end}) 내 총 {len(matched_meetings)}개 회의 식별 완료")
    return matched_meetings

def parse_meeting_canvas_speeches(detail_url: str) -> List[Dict[str, str]]:
    """
    상세 회의록 뷰어(/CLRecords/Retrieval2/index.php?hfile=...)에 접속하여
    canvas 영역의 <strong>○발언자</strong> 단위로 실제 발언을 분할 파싱
    """
    try:
        r = requests.get(detail_url, headers=HEADERS, timeout=15)
        r.encoding = "euc-kr"
    except Exception as e:
        print(f"[!] 상세 페이지 요청 실패 ({detail_url}): {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    canvas = soup.find(id="canvas")
    if not canvas:
        canvas = soup.find("div", class_="content") or soup.find("body")

    if not canvas:
        return []

    for br in canvas.find_all("br"):
        br.replace_with("\n")

    for strong in canvas.find_all("strong"):
        txt = strong.get_text().strip()
        if txt.startswith("○") or txt.startswith("◯"):
            strong.insert_before("\n###SPEAKER###")
            strong.insert_after("###ENDSPEAKER###\n")

    raw_text = canvas.get_text()
    raw_text = raw_text.replace("\xa0", " ")

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

def run_crawler(
    max_meetings: Optional[int] = None,
    force_refresh: bool = False,
    db_path: str = DB_PATH,
    start_page: int = 1,
    end_page: int = 10,
    keyword: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    log_callback: Optional[Any] = None
) -> Dict[str, Any]:
    """
    통합 크롤러:
    - start_date & end_date가 지정된 경우: 월별/일자별 과거 회기 전수 탐색
    - 지정되지 않은 경우: 최근 회기(late.html) 자동 수집
    """
    def emit(msg: str):
        print(msg)
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    emit("=" * 70)
    if start_date and end_date:
        emit(f"🌐 [제주특별자치도의회 기간별 회의록 수집 시작: {start_date} ~ {end_date}]")
    else:
        emit("🌐 [제주특별자치도의회 전자회의록 최근 회기 수집 시작]")
    emit(f"   - 대상 시스템: {BASE_URL}")
    emit(f"   - 저장 DB: {db_path}")
    emit("=" * 70)

    if start_date and end_date:
        meeting_list = get_meetings_by_date_range(start_date, end_date, emit=emit)
    else:
        meeting_list = get_recent_meeting_list(max_pages=end_page, emit=emit)

    if not meeting_list:
        emit("[!] 조건에 부합하는 수집 대상 회의가 없습니다.")
        return {"total_meetings_found": 0, "total_speeches_inserted": 0, "skipped_existing": 0}

    if keyword:
        orig_len = len(meeting_list)
        meeting_list = [m for m in meeting_list if keyword in m["name"]]
        emit(f"[*] 키워드('{keyword}') 필터링: {orig_len}건 중 {len(meeting_list)}건 일치")

    if max_meetings:
        meeting_list = meeting_list[:max_meetings]

    total_inserted = 0
    skipped = 0
    crawled_count = 0

    for idx, m in enumerate(meeting_list, start=1):
        name = m["name"]
        date = m["date"]
        link = m["link"]

        emit(f"\n[{idx}/{len(meeting_list)}] {name} ({date})")

        if not force_refresh and is_already_collected(name, date, db_path):
            emit("    -> [기수집] 이미 DB에 적재된 회의이므로 건너뜁니다.")
            skipped += 1
            continue

        speeches = parse_meeting_canvas_speeches(link)
        if not speeches:
            emit("    -> [주의] 본문 발언 추출 결과가 없습니다.")
            continue

        for s in speeches:
            s["meeting_name"] = name
            s["meeting_date"] = date
            s["source_url"] = link

        cnt = import_speeches_to_db(
            speeches=speeches,
            db_path=db_path
        )
        emit(f"    -> [완료] 실제 발언 {cnt}건 추출 및 DB 적재 성공")
        total_inserted += cnt
        crawled_count += 1
        time.sleep(0.2)

    emit("\n" + "=" * 70)
    emit(f"✅ [수집 완료 요약]")
    emit(f"   - 총 대상 회의: {len(meeting_list)}건")
    emit(f"   - 신규 수집 회의: {crawled_count}건")
    emit(f"   - 기수집 건너뜀: {skipped}건")
    emit(f"   - 신규 적재 발언: {total_inserted}건")
    emit("=" * 70)

    return {
        "total_meetings_found": len(meeting_list),
        "total_speeches_inserted": total_inserted,
        "skipped_existing": skipped,
        "newly_crawled_meetings": crawled_count
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="제주도의회 전자회의록 실시간 크롤러")
    parser.add_argument("--start", type=str, default=None, help="시작 일자 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="종료 일자 (YYYY-MM-DD)")
    parser.add_argument("--keyword", type=str, default="", help="회의명 검색 키워드")
    parser.add_argument("--limit", type=int, default=None, help="최대 수집 회의 수")
    parser.add_argument("--force", action="store_true", help="기존 데이터 무시하고 재수집")
    args = parser.parse_args()

    run_crawler(
        start_date=args.start,
        end_date=args.end,
        keyword=args.keyword,
        max_meetings=args.limit,
        force_refresh=args.force
    )
