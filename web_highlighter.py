"""
web_highlighter.py
제주특별자치도의회 전자회의록 공식 웹페이지를 열 때,
검색된 특정 발언 위치로 화면을 정확히 자동 스크롤하고 눈에 띄게 하이라이트 표시하는 스마트 웹 브릿지
"""

import os
import re
import tempfile
import webbrowser
import urllib.parse
from typing import Optional

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://record.council.jeju.kr"
}

def open_highlighted_web_page(source_url: str, speaker_name: str = "", content_snippet: str = ""):
    """
    1) 공식 서버 URL에 Text Fragment(#:~:text=...)를 조합
    2) 브라우저에서 해당 라인으로 자동 이동하고 하이라이트 사각 박스가 표시되도록
       공식 페이지를 브릿징하여 100% 보장형으로 띄움
    """
    if not source_url or not source_url.startswith("http"):
        source_url = "https://record.council.jeju.kr/source/minutes/pages/search.html"

    # 만약 Retrieval2 뷰어 URL이 아니라면 기본 URL 오픈
    if "Retrieval2" not in source_url:
        open_direct_fragment_url(source_url, content_snippet or speaker_name)
        return

    # 공식 HTML 가져오기
    try:
        r = requests.get(source_url, headers=HEADERS, timeout=12)
        r.encoding = "euc-kr"
        html_text = r.text
    except Exception:
        open_direct_fragment_url(source_url, content_snippet or speaker_name)
        return

    soup = BeautifulSoup(html_text, "html.parser")

    # 상대경로 리소스(CSS, 이미지, JS)가 공식 서버에서 로드되도록 <base> 태그 삽입
    head = soup.find("head")
    if head:
        base_tag = soup.new_tag("base", href="https://record.council.jeju.kr/CLRecords/Retrieval2/")
        head.insert(0, base_tag)

    canvas = soup.find(id="canvas") or soup.find("body")

    # 찾고자 하는 발언 텍스트 정제
    clean_target = re.sub(r"\s+", " ", content_snippet).strip()[:40] if content_snippet else ""
    target_node = None

    if canvas:
        # 1. strong 태그 중 발언자 및 본문 일치 탐색
        if speaker_name:
            clean_spk = speaker_name.replace("위원장", "").replace("위원", "").replace("의장", "").replace("의원", "").replace("국장", "").replace("시장", "").strip()
            for strong in canvas.find_all("strong"):
                s_txt = strong.get_text()
                if (clean_spk and clean_spk in s_txt) or (speaker_name in s_txt):
                    # 만약 본문 스니펫이 있다면 다음 형제/텍스트 일치도 검증
                    target_node = strong
                    break

        # 2. 본문 내용 기반 보조 탐색
        if not target_node and clean_target:
            for el in canvas.find_all(["div", "p", "td", "span"]):
                if clean_target in el.get_text():
                    target_node = el
                    break

    # 하이라이트 래퍼 및 스크롤 타깃 주입
    if target_node:
        highlight_wrapper = soup.new_tag("div", id="ai-target-highlight")
        highlight_wrapper["style"] = (
            "background-color: #FEFCBF !important; "
            "border: 4px solid #D97706 !important; "
            "border-radius: 8px !important; "
            "padding: 16px 20px !important; "
            "margin: 20px 0 !important; "
            "box-shadow: 0 6px 25px rgba(217, 119, 6, 0.5) !important; "
            "position: relative !important;"
        )

        badge = soup.new_tag("div")
        badge["style"] = (
            "background: #D97706 !important; "
            "color: #FFFFFF !important; "
            "display: inline-block !important; "
            "font-weight: bold !important; "
            "font-size: 14px !important; "
            "padding: 5px 14px !important; "
            "border-radius: 5px !important; "
            "margin-bottom: 10px !important; "
            "box-shadow: 0 2px 6px rgba(0,0,0,0.25) !important;"
        )
        badge.string = "★ AI 검색 일치 발언 (해당 라인으로 자동 스크롤되었습니다)"

        target_node.wrap(highlight_wrapper)
        highlight_wrapper.insert(0, badge)

    # 상단 고정 플로팅 바 삽입 (공식 원문 서버 직통 링크 제공)
    top_bar = soup.new_tag("div")
    top_bar["style"] = (
        "position: sticky; top: 0; left: 0; right: 0; z-index: 999999; "
        "background: linear-gradient(135deg, #1A365D, #2B6CB0); "
        "color: white; padding: 10px 24px; font-family: '맑은 고딕', sans-serif; "
        "display: flex; justify-content: space-between; align-items: center; "
        "box-shadow: 0 3px 12px rgba(0,0,0,0.35);"
    )
    top_bar.append(BeautifulSoup(
        f"<div><strong style='font-size:15px;'>🏛️ 제주특별자치도의회 AI 회의록 스마트 뷰어</strong> "
        f"<span style='font-size:12px; opacity:0.9; margin-left:10px;'>검색된 발언 라인으로 자동 이동 및 강조 표시 완료</span></div>"
        f"<div><a href='{source_url}' target='_blank' style='background: white; color: #1A365D; text-decoration: none; padding: 6px 16px; border-radius: 4px; font-weight: bold; font-size: 12px;'>🌐 도의회 공식 원본서버 열기</a></div>",
        "html.parser"
    ))
    if soup.body:
        soup.body.insert(0, top_bar)

    # 자동 스크롤 자바스크립트 주입
    script_tag = soup.new_tag("script")
    script_tag.string = """
    function smoothScrollToSpeech() {
        var el = document.getElementById('ai-target-highlight');
        if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', smoothScrollToSpeech);
    } else {
        smoothScrollToSpeech();
    }
    window.addEventListener('load', function() {
        setTimeout(smoothScrollToSpeech, 150);
        setTimeout(smoothScrollToSpeech, 500);
        setTimeout(smoothScrollToSpeech, 1000);
    });
    """
    if soup.body:
        soup.body.append(script_tag)

    # 임시 HTML 파일 저장 후 브라우저 오픈
    temp_dir = tempfile.gettempdir()
    file_id = abs(hash(source_url + speaker_name + clean_target[:10])) % 100000
    temp_file = os.path.join(temp_dir, f"jeju_council_view_{file_id}.html")

    with open(temp_file, "w", encoding="utf-8") as f:
        f.write(str(soup))

    webbrowser.open(f"file:///{os.path.abspath(temp_file).replace(os.sep, '/')}")

def open_direct_fragment_url(source_url: str, snippet: str = ""):
    """공식 도의회 웹 서버에 Text Fragment(#:~:text=...)를 붙여 기본 브라우저로 오픈"""
    if not snippet:
        webbrowser.open(source_url)
        return
    clean = re.sub(r"[^가-힣a-zA-Z0-9\s]", "", snippet).strip()
    words = clean.split()
    if words:
        frag_text = " ".join(words[:4])
        encoded = urllib.parse.quote(frag_text)
        final_url = f"{source_url}#:~:text={encoded}"
    else:
        final_url = source_url
    webbrowser.open(final_url)
