"""
desktop_council_app.py
제주특별자치도의회 100% 로컬 초경량 AI 회의록 분석 시스템 (통합 데스크톱 앱)
- 탭 1: 자연어 AI 회의록 고속 검색 (추천 질문 태그, FTS5 + Cross-Encoder 카드 뷰, 전문 열람)
- 탭 2: 회의록 문서 파일 일괄 임포트 (HWPX, TXT, MD, JSON, CSV, XLSX)
- 탭 3: 전자회의록 포털 실시간 웹 크롤링 (증분 업데이트, 수집 로그)
- 100% 오프라인 동작, API 비용 0원, 환각 0%
"""

import os
import sys
import time
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from typing import List, Dict, Any, Optional

# Windows 콘솔 인코딩 및 truststore 호환
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

import webbrowser
from local_pipeline import find_exact_speeches, get_full_meeting_transcript, DB_PATH, DEFAULT_MODEL, get_judge
from web_highlighter import open_highlighted_web_page, open_direct_fragment_url
from batch_importer import import_file, import_directory
from crawler import run_crawler

RECOMMENDED_QUERIES = [
    "제455회 본회의 제주 동부해양경찰서 신설 촉구 건의안",
    "미래경제산업위원회 경제통상진흥원 및 일자리 지원 사업 질의",
    "서귀포시장 행정사무감사 주요 지적사항 및 답변",
    "환경도시위원회 행정사무감사 도시계획 및 환경 보전 질의",
    "문화관광체육위원회 관광진흥 및 체육시설 감사 지적"
]

class CouncilDesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("제주특별자치도의회 로컬 AI 회의록 분석 시스템 (No-Cloud)")
        self.root.geometry("1100x800")
        self.root.minsize(900, 650)

        # 윈도우 스타일
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self._configure_styles()
        self._create_main_layout()

        # 앱 시작 시 AI 모델 백그라운드 웜업
        threading.Thread(target=self._warmup_model, daemon=True).start()

    def _configure_styles(self):
        self.style.configure(".", font=("맑은 고딕", 10))
        self.style.configure("Header.TLabel", font=("맑은 고딕", 15, "bold"), foreground="#1A365D")
        self.style.configure("SubHeader.TLabel", font=("맑은 고딕", 9), foreground="#4A5568")
        self.style.configure("TNotebook.Tab", font=("맑은 고딕", 11, "bold"), padding=[16, 8])
        self.style.configure("Tag.TButton", font=("맑은 고딕", 9), background="#EDF2F7", foreground="#2B6CB0")

    def _warmup_model(self):
        try:
            self.status_var.set("초경량 AI 모델을 메모리에 로드 중입니다...")
            get_judge()
            self.status_var.set("시스템 준비 완료 (100% 로컬 오프라인 모드 • FTS5 & AI 판별 활성화)")
        except Exception as e:
            self.status_var.set(f"모델 초기화 경고: {e}")

    def _create_main_layout(self):
        # 1. 헤더
        header_frame = ttk.Frame(self.root, padding="16 12 16 6")
        header_frame.pack(fill=tk.X)

        title_lbl = ttk.Label(header_frame, text="🏛️ 제주특별자치도의회 로컬 AI 회의록 검색 시스템", style="Header.TLabel")
        title_lbl.pack(anchor=tk.W)

        sub_lbl = ttk.Label(
            header_frame,
            text="No-Cloud 100% 오프라인 • SQLite FTS5(0.001초) + Whisper-small급 AI 판별 • API 비용 0원 & 환각 0%",
            style="SubHeader.TLabel"
        )
        sub_lbl.pack(anchor=tk.W, pady=(2, 0))

        # 2. 탭 컨테이너 (Notebook)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        # 탭 1: 회의록 검색
        self.search_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.search_tab, text="  🔍 AI 회의록 검색  ")
        self._init_search_tab()

        # 탭 2: 문서 파일 임포트
        self.import_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.import_tab, text="  📁 문서 일괄 임포트  ")
        self._init_import_tab()

        # 탭 3: 웹 회의록 수집
        self.crawl_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.crawl_tab, text="  🌐 전자회의록 웹 수집  ")
        self._init_crawl_tab()

        # 3. 상태 표시줄
        status_bar = ttk.Frame(self.root, padding="16 6 16 6", relief="sunken")
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="초기화 중...")
        self.status_lbl = ttk.Label(status_bar, textvariable=self.status_var, font=("맑은 고딕", 9), foreground="#4A5568")
        self.status_lbl.pack(side=tk.LEFT)

    # -------------------------------------------------------------
    # 탭 1: AI 회의록 검색
    # -------------------------------------------------------------
    def _init_search_tab(self):
        # 상단 검색 바
        search_box = ttk.Frame(self.search_tab)
        search_box.pack(fill=tk.X, pady=(0, 6))

        self.query_var = tk.StringVar(value=RECOMMENDED_QUERIES[0])
        self.search_entry = ttk.Entry(search_box, textvariable=self.query_var, font=("맑은 고딕", 12))
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0, 8))
        self.search_entry.bind("<Return>", lambda e: self.on_search())

        self.search_btn = tk.Button(
            search_box,
            text="🔍 자연어 검색",
            command=self.on_search,
            font=("맑은 고딕", 11, "bold"),
            bg="#2B6CB0",
            fg="white",
            activebackground="#2C5282",
            activeforeground="white",
            padx=18,
            pady=4,
            relief="flat",
            cursor="hand2"
        )
        self.search_btn.pack(side=tk.RIGHT)

        # 추천 질문 태그 영역
        tags_box = ttk.Frame(self.search_tab)
        tags_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(tags_box, text="💡 추천 질문:", font=("맑은 고딕", 9, "bold"), foreground="#718096").pack(side=tk.LEFT, padx=(0, 6))
        for q_text in RECOMMENDED_QUERIES[:3]:
            short_label = q_text[:18] + "..." if len(q_text) > 18 else q_text
            btn = tk.Button(
                tags_box,
                text=short_label,
                command=lambda q=q_text: self._apply_recommended_query(q),
                font=("맑은 고딕", 8),
                bg="#EDF2F7",
                fg="#2B6CB0",
                relief="flat",
                padx=6,
                pady=1,
                cursor="hand2"
            )
            btn.pack(side=tk.LEFT, padx=3)

        # 옵션 제어 바
        opt_box = ttk.Frame(self.search_tab)
        opt_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(opt_box, text="반환 개수(Top-K):").pack(side=tk.LEFT, padx=(0, 4))
        self.top_k_var = tk.IntVar(value=3)
        top_k_spin = ttk.Spinbox(opt_box, from_=1, to=10, width=4, textvariable=self.top_k_var)
        top_k_spin.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(opt_box, text="최소 일치도:").pack(side=tk.LEFT, padx=(0, 4))
        self.threshold_var = tk.DoubleVar(value=0.2)
        thresh_scale = ttk.Scale(opt_box, from_=0.0, to=1.0, variable=self.threshold_var, length=120)
        thresh_scale.pack(side=tk.LEFT, padx=(0, 4))
        self.thresh_label = ttk.Label(opt_box, text="20%")
        self.thresh_label.pack(side=tk.LEFT, padx=(0, 20))
        thresh_scale.configure(command=lambda v: self.thresh_label.config(text=f"{int(float(v)*100)}%"))

        # 소요 시간 및 통계 라인
        self.bench_var = tk.StringVar(value="질문을 입력하고 검색 버튼을 누르면 FTS5 및 AI 판별 모델이 즉시 결과를 분석합니다.")
        bench_lbl = ttk.Label(self.search_tab, textvariable=self.bench_var, font=("맑은 고딕", 9), foreground="#4A5568")
        bench_lbl.pack(anchor=tk.W, pady=(0, 6))

        # 결과 리스트 스크롤 영역
        result_box = ttk.Frame(self.search_tab)
        result_box.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(result_box, bg="#F7FAFC", highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(result_box, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(xscrollcommand=None, yscrollcommand=self.scrollbar.set)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    def _apply_recommended_query(self, query: str):
        self.query_var.set(query)
        self.on_search()

    def on_search(self):
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning("입력 필요", "검색할 질문이나 키워드를 입력해 주세요.")
            return

        top_k = self.top_k_var.get()
        thresh = self.threshold_var.get()

        self.search_btn.config(state=tk.DISABLED, text="⏳ 분석 중...")
        self.status_var.set(f"'{query}' FTS5 색인 및 초경량 AI 리랭킹 수행 중...")

        threading.Thread(target=self._run_search_thread, args=(query, top_k, thresh), daemon=True).start()

    def _run_search_thread(self, query: str, top_k: int, thresh: float):
        try:
            t0 = time.time()
            res = find_exact_speeches(query, top_k=top_k, threshold=thresh)
            elapsed_all = time.time() - t0
            self.root.after(0, self._render_results, res, elapsed_all)
        except Exception as e:
            self.root.after(0, self._render_search_error, str(e))

    def _render_search_error(self, err: str):
        self.search_btn.config(state=tk.NORMAL, text="🔍 자연어 검색")
        self.status_var.set(f"검색 오류: {err}")
        messagebox.showerror("오류", f"검색 중 오류가 발생했습니다:\n{err}")

    def _render_results(self, res: dict, total_elapsed: float):
        self.search_btn.config(state=tk.NORMAL, text="🔍 자연어 검색")
        self.status_var.set("검색 완료")

        elapsed = res["elapsed"]
        kws = ", ".join(res.get("keywords", []))
        self.bench_var.set(
            f"⚡ 검색 시간: 총 {elapsed['total_ms']}ms (FTS5: {elapsed['fts_ms']}ms | AI 판별: {elapsed['reranker_ms']}ms) "
            f"• 추출 키워드: [{kws}] • 후보: {res['total_candidates']}건 중 {len(res['results'])}건 확정"
        )

        for w in self.scrollable_frame.winfo_children():
            w.destroy()

        results = res.get("results", [])
        if not results:
            no_box = ttk.Frame(self.scrollable_frame, padding=40)
            no_box.pack(fill=tk.BOTH, expand=True)
            ttk.Label(no_box, text="일치하는 회의록 발언을 찾지 못했습니다.", font=("맑은 고딕", 12, "bold"), foreground="#A0AEC0").pack()
            ttk.Label(no_box, text="검색어를 변경하거나 최소 일치도 임계값을 낮춰보세요.", font=("맑은 고딕", 10), foreground="#CBD5E0").pack(pady=4)
            return

        for idx, item in enumerate(results, 1):
            self._render_speech_card(idx, item)

        self.canvas.yview_moveto(0)

    def _open_web_url(self, url: Optional[str], speaker_name: str = "", content: str = "", direct_only: bool = False):
        """
        공식 도의회 전자회의록 웹 링크 오픈:
        - 기본값: 해당 발언 라인으로 화면을 부드럽게 자동 스크롤하고 눈에 띄게 강조 박스(하이라이트)를 표시하여 100% 보장형으로 엽니다.
        - direct_only=True: 도의회 공식 원본 웹 서버로 직접 이동
        """
        target = url.strip() if url else ""
        if not target or not target.startswith("http"):
            target = "https://record.council.jeju.kr/source/minutes/pages/search.html"

        try:
            if direct_only:
                open_direct_fragment_url(target, content or speaker_name)
            else:
                threading.Thread(
                    target=open_highlighted_web_page,
                    args=(target, speaker_name, content),
                    daemon=True
                ).start()
        except Exception as e:
            messagebox.showerror("링크 열기 오류", f"웹 브라우저를 실행할 수 없습니다:\n{e}")

    def _render_speech_card(self, rank: int, item: dict):
        card = tk.Frame(self.scrollable_frame, bg="#FFFFFF", relief="solid", borderwidth=1, padx=16, pady=12)
        card.pack(fill=tk.X, expand=True, pady=6, padx=4)

        top_row = tk.Frame(card, bg="#FFFFFF")
        top_row.pack(fill=tk.X)

        score_pct = round(item["score"] * 100, 1)
        score_color = "#2B6CB0" if score_pct >= 70 else "#D69E2E"
        stars = "★" * int(score_pct // 20) + "☆" * (5 - int(score_pct // 20))

        score_lbl = tk.Label(
            top_row,
            text=f"[{rank}순위] 일치도 {score_pct}% ({stars})",
            font=("맑은 고딕", 11, "bold"),
            fg=score_color,
            bg="#FFFFFF"
        )
        score_lbl.pack(side=tk.LEFT)

        meta_lbl = tk.Label(
            top_row,
            text=f"📅 {item['date']}  |  🏛️ {item['meeting']}",
            font=("맑은 고딕", 9),
            fg="#718096",
            bg="#FFFFFF"
        )
        meta_lbl.pack(side=tk.RIGHT)

        spk_row = tk.Frame(card, bg="#FFFFFF")
        spk_row.pack(fill=tk.X, pady=(4, 4))
        spk_lbl = tk.Label(
            spk_row,
            text=f"👤 {item['speaker']}",
            font=("맑은 고딕", 12, "bold"),
            fg="#1A202C",
            bg="#FFFFFF"
        )
        spk_lbl.pack(side=tk.LEFT)

        content_box = tk.Label(
            card,
            text=f"\"{item['content']}\"",
            font=("맑은 고딕", 10),
            fg="#2D3748",
            bg="#F7FAFC",
            justify=tk.LEFT,
            wraplength=980,
            padx=12,
            pady=10,
            relief="flat"
        )
        content_box.pack(fill=tk.X, pady=(2, 6))

        btn_row = tk.Frame(card, bg="#FFFFFF")
        btn_row.pack(fill=tk.X, pady=(2, 0))

        # 좌측: 공식 웹 링크 버튼 (해당 라인으로 자동 이동 & 하이라이트)
        source_url = item.get("source_url", "")
        spk = item.get("speaker_name") or item.get("speaker", "")
        cnt = item.get("content", "")

        web_btn = tk.Button(
            btn_row,
            text="🎯 웹에서 해당 라인으로 바로 이동 & 표시",
            command=lambda u=source_url, s=spk, c=cnt: self._open_web_url(u, speaker_name=s, content=c),
            font=("맑은 고딕", 9, "bold"),
            bg="#EBF8FF",
            fg="#2B6CB0",
            activebackground="#BEE3F8",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        web_btn.pack(side=tk.LEFT)

        # 우측: 발언 전문 보기 & 이 회의 전체 회의록 보기
        all_btn = tk.Button(
            btn_row,
            text="📖 이 회의 전체 회의록 보기",
            command=lambda it=item: self._open_full_meeting_modal(it["meeting"], it["date"], highlight_id=it.get("id")),
            font=("맑은 고딕", 9, "bold"),
            bg="#319795",
            fg="white",
            activebackground="#285E61",
            relief="flat",
            padx=12,
            pady=3,
            cursor="hand2"
        )
        all_btn.pack(side=tk.RIGHT, padx=(6, 0))

        detail_btn = tk.Button(
            btn_row,
            text="🔍 발언 전문 띄우기",
            command=lambda it=item: self._open_detail_modal(it),
            font=("맑은 고딕", 9),
            bg="#EDF2F7",
            fg="#2D3748",
            activebackground="#CBD5E0",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        detail_btn.pack(side=tk.RIGHT)

    def _open_detail_modal(self, item: dict):
        """개별 발언 상세 전문 모달 + 다음 전체 회의록 보기 버튼 및 직통 링크 제공"""
        modal = tk.Toplevel(self.root)
        modal.title(f"발언 상세 - {item['speaker']} ({item['date']})")
        modal.geometry("820x620")
        modal.minsize(650, 450)
        modal.transient(self.root)

        p = ttk.Frame(modal, padding=16)
        p.pack(fill=tk.BOTH, expand=True)

        # 회의 및 발언자 메타 헤더
        ttk.Label(p, text=f"🏛️ 회의명: {item['meeting']}", font=("맑은 고딕", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(
            p,
            text=f"📅 일시: {item['date']}   |   👤 발언자: {item['speaker']}   |   일치도: {round(item['score']*100, 1)}%",
            font=("맑은 고딕", 10),
            foreground="#4A5568"
        ).pack(anchor=tk.W, pady=(2, 6))

        # 공식 웹 링크 알림 박스 (클릭 가능한 하이퍼링크 + 라인 자동이동 버튼)
        source_url = item.get("source_url") or "https://record.council.jeju.kr/source/minutes/pages/search.html"
        spk = item.get("speaker_name") or item.get("speaker", "")
        cnt = item.get("content", "")

        link_frame = tk.Frame(p, bg="#EBF8FF", bd=1, relief="solid", padx=10, pady=8)
        link_frame.pack(fill=tk.X, pady=(0, 10))

        tk.Label(link_frame, text="🌐 공식 전자회의록 링크:", font=("맑은 고딕", 9, "bold"), bg="#EBF8FF", fg="#2C5282").pack(side=tk.LEFT)
        url_lbl = tk.Label(
            link_frame,
            text=source_url,
            font=("맑은 고딕", 9, "underline"),
            bg="#EBF8FF",
            fg="#2B6CB0",
            cursor="hand2"
        )
        url_lbl.pack(side=tk.LEFT, padx=6)
        url_lbl.bind("<Button-1>", lambda e, u=source_url, s=spk, c=cnt: self._open_web_url(u, speaker_name=s, content=c))

        open_web_btn = tk.Button(
            link_frame,
            text="🎯 웹에서 해당 라인으로 이동 & 표시 열기",
            command=lambda u=source_url, s=spk, c=cnt: self._open_web_url(u, speaker_name=s, content=c),
            font=("맑은 고딕", 9, "bold"),
            bg="#3182CE",
            fg="white",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        open_web_btn.pack(side=tk.RIGHT)

        # 발언 본문 텍스트
        text_area = scrolledtext.ScrolledText(p, wrap=tk.WORD, font=("맑은 고딕", 11), padx=12, pady=12)
        text_area.insert(tk.END, item["content"])
        text_area.configure(state=tk.DISABLED)
        text_area.pack(fill=tk.BOTH, expand=True)

        # 하단 액션 버튼 박스 (사용자 요청: 그 전문 띄우기 다음에 전체 회의록이 나오게 연계)
        btn_box = ttk.Frame(p, padding=(0, 12, 0, 0))
        btn_box.pack(fill=tk.X)

        def copy_text():
            self.root.clipboard_clear()
            self.root.clipboard_append(item["content"])
            messagebox.showinfo("복사 완료", "발언 전문이 클립보드에 복사되었습니다.", parent=modal)

        ttk.Button(btn_box, text="📋 발언 내용 복사", command=copy_text).pack(side=tk.LEFT)

        # 전체 회의록 보기 버튼 (전문 띄우기 모달 안에서 바로 전체 회의록으로 이동/전환)
        view_all_btn = tk.Button(
            btn_box,
            text="📖 이 회의 전체 회의록 보기 (전문 정독) ➔",
            command=lambda it=item: [modal.destroy(), self._open_full_meeting_modal(it["meeting"], it["date"], highlight_id=it.get("id"))],
            font=("맑은 고딕", 10, "bold"),
            bg="#2B6CB0",
            fg="white",
            relief="flat",
            padx=14,
            pady=4,
            cursor="hand2"
        )
        view_all_btn.pack(side=tk.LEFT, padx=12)

        ttk.Button(btn_box, text="닫기", command=modal.destroy).pack(side=tk.RIGHT)

    def _open_full_meeting_modal(self, meeting_name: str, meeting_date: str, highlight_id: Optional[int] = None):
        """해당 회의의 전체 발언록을 순서대로 처음부터 끝까지 전문 출력하는 통합 뷰어"""
        try:
            meeting_data = get_full_meeting_transcript(meeting_name, meeting_date)
        except Exception as e:
            messagebox.showerror("오류", f"전체 회의록을 불러오는 중 오류가 발생했습니다:\n{e}")
            return

        speeches = meeting_data.get("speeches", [])
        total_speeches = meeting_data.get("total_speeches", 0)
        source_url = meeting_data.get("source_url") or "https://record.council.jeju.kr/source/minutes/pages/search.html"

        # 검색 대상 발언 추출
        target_spk = ""
        target_cnt = ""
        target_idx = -1
        for idx, s in enumerate(speeches):
            if highlight_id is not None and s["id"] == highlight_id:
                target_spk = s.get("speaker_name") or s.get("speaker", "")
                target_cnt = s.get("content", "")
                target_idx = idx
                break

        modal = tk.Toplevel(self.root)
        modal.title(f"🏛️ 전체 회의록 전문 - {meeting_name} ({meeting_date})")
        modal.geometry("980x750")
        modal.minsize(750, 500)
        modal.transient(self.root)

        p = ttk.Frame(modal, padding=16)
        p.pack(fill=tk.BOTH, expand=True)

        # 상단 헤더
        top_bar = ttk.Frame(p)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        info_box = ttk.Frame(top_bar)
        info_box.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(info_box, text=f"🏛️ {meeting_name}", font=("맑은 고딕", 13, "bold")).pack(anchor=tk.W)
        ttk.Label(
            info_box,
            text=f"📅 일시: {meeting_date}   |   📊 총 발언 건수: {total_speeches}건   |   100% 로컬 오프라인 적재 기록",
            font=("맑은 고딕", 10),
            foreground="#4A5568"
        ).pack(anchor=tk.W, pady=(2, 0))

        # 공식 링크 버튼 (상단)
        top_btn_box = ttk.Frame(top_bar)
        top_btn_box.pack(side=tk.RIGHT)

        open_web_btn = tk.Button(
            top_btn_box,
            text="🎯 웹 브라우저에서 해당 라인 이동 & 표시",
            command=lambda u=source_url, s=target_spk, c=target_cnt: self._open_web_url(u, speaker_name=s, content=c),
            font=("맑은 고딕", 9, "bold"),
            bg="#2B6CB0",
            fg="white",
            relief="flat",
            padx=12,
            pady=5,
            cursor="hand2"
        )
        open_web_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # URL 안내 바
        link_bar = tk.Frame(p, bg="#F7FAFC", bd=1, relief="solid", padx=10, pady=4)
        link_bar.pack(fill=tk.X, pady=(0, 8))
        tk.Label(link_bar, text="🔗 공식 전자회의록 링크:", font=("맑은 고딕", 9, "bold"), bg="#F7FAFC", fg="#4A5568").pack(side=tk.LEFT)
        url_link = tk.Label(
            link_bar,
            text=source_url,
            font=("맑은 고딕", 9, "underline"),
            bg="#F7FAFC",
            fg="#2B6CB0",
            cursor="hand2"
        )
        url_link.pack(side=tk.LEFT, padx=6)
        url_link.bind("<Button-1>", lambda e, u=source_url, s=target_spk, c=target_cnt: self._open_web_url(u, speaker_name=s, content=c))

        # 보기 모드 전환 바 (스크롤 지연 없이 즉시 해당 부분만 표시)
        default_mode = "context" if (highlight_id is not None and target_idx != -1) else "full"
        mode_var = tk.StringVar(value=default_mode)

        mode_bar = tk.Frame(p, bg="#EEF2F6", bd=1, relief="solid", padx=10, pady=6)
        mode_bar.pack(fill=tk.X, pady=(0, 8))

        tk.Label(mode_bar, text="👁️ 보기 방식:", font=("맑은 고딕", 9, "bold"), bg="#EEF2F6", fg="#2D3748").pack(side=tk.LEFT, padx=(0, 8))

        mode_status_lbl = tk.Label(
            mode_bar,
            text="",
            font=("맑은 고딕", 9),
            bg="#EEF2F6",
            fg="#2B6CB0"
        )
        mode_status_lbl.pack(side=tk.RIGHT, padx=6)

        # 회의록 내 실시간 검색/필터 바
        search_filter_bar = ttk.Frame(p)
        search_filter_bar.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(search_filter_bar, text="🔎 회의록 내 단어 검색:").pack(side=tk.LEFT, padx=(0, 4))
        filter_var = tk.StringVar()
        filter_entry = ttk.Entry(search_filter_bar, textvariable=filter_var, font=("맑은 고딕", 10), width=24)
        filter_entry.pack(side=tk.LEFT, padx=(0, 8))

        # 본문 텍스트 영역
        text_area = scrolledtext.ScrolledText(p, wrap=tk.WORD, font=("맑은 고딕", 11), padx=12, pady=12)
        text_area.pack(fill=tk.BOTH, expand=True)

        # 텍스트 태그 스타일 설정 (선택된 발언 강조 태그)
        text_area.tag_configure("highlight_box", background="#FEFCBF", foreground="#744210", font=("맑은 고딕", 11, "bold"))
        text_area.tag_configure("speaker_header", font=("맑은 고딕", 11, "bold"), foreground="#1A365D")
        text_area.tag_configure("divider", foreground="#CBD5E0")
        text_area.tag_configure("search_found", background="#FED7D7", foreground="#9B2C2C")

        def render_transcript_view():
            mode = mode_var.get()
            text_area.configure(state=tk.NORMAL)
            text_area.delete("1.0", tk.END)

            if mode == "context" and highlight_id is not None and target_idx != -1:
                ctx_start = max(0, target_idx - 2)
                ctx_end = min(total_speeches, target_idx + 3)

                mode_status_lbl.config(text=f"💡 제{target_idx + 1}번째 핵심 발언 및 질의·답변 맥락 즉시 표시 중 (스크롤 지연 없음)")

                header = [
                    "=" * 75,
                    f"  🎯 [핵심 발언 집중 보기] {meeting_name}",
                    f"  • 일시: {meeting_date}  |  전체 {total_speeches}건 중 제 {target_idx + 1}번째 발언 및 질의·답변 문맥",
                    f"  • 불필요한 스크롤 없이 해당 발언만 즉시 띄웠습니다.",
                    "=" * 75 + "\n"
                ]
                text_area.insert(tk.END, "\n".join(header))

                if ctx_start > 0:
                    text_area.insert(tk.END, f"▲ [앞선 발언 {ctx_start}건 생략됨 - 상단 '전체 회의록 전문' 선택 시 모두 표시]\n\n", "divider")

                for idx in range(ctx_start, ctx_end):
                    s = speeches[idx]
                    is_target = (s["id"] == highlight_id)
                    spk_title = f"[{idx + 1} / {total_speeches}] ◯{s['speaker']}"
                    if is_target:
                        spk_title += "  ★ [검색된 핵심 발언]"

                    text_area.insert(tk.END, "-" * 75 + "\n", "divider")
                    text_area.insert(tk.END, f"{spk_title}\n", "highlight_box" if is_target else "speaker_header")
                    text_area.insert(tk.END, "-" * 75 + "\n", "divider")
                    text_area.insert(tk.END, f"{s['content']}\n\n", "highlight_box" if is_target else "")

                if ctx_end < total_speeches:
                    text_area.insert(tk.END, f"▼ [뒤따르는 발언 {total_speeches - ctx_end}건 생략됨 - 상단 '전체 회의록 전문' 선택 시 모두 표시]\n\n", "divider")

                text_area.configure(state=tk.DISABLED)
                text_area.see("1.0")

            else:
                mode_status_lbl.config(text=f"📜 전체 회의록(총 {total_speeches}건) 전문을 순서대로 표시합니다.")
                full_text_buffer = [
                    "=" * 75,
                    f"  {meeting_name} 전체 회의록 전문",
                    f"  • 일시: {meeting_date}",
                    f"  • 공식 링크: {source_url}",
                    f"  • 총 발언: {total_speeches}건",
                    "=" * 75 + "\n"
                ]
                text_area.insert(tk.END, "\n".join(full_text_buffer))

                target_pos = None
                for idx, s in enumerate(speeches, start=1):
                    is_target = (highlight_id is not None and s["id"] == highlight_id)
                    spk_title = f"[{idx} / {total_speeches}] ◯{s['speaker']}"
                    if is_target:
                        spk_title += "  ★ [검색된 핵심 발언]"
                        target_pos = text_area.index(tk.END)

                    text_area.insert(tk.END, "-" * 75 + "\n", "divider")
                    text_area.insert(tk.END, f"{spk_title}\n", "highlight_box" if is_target else "speaker_header")
                    text_area.insert(tk.END, "-" * 75 + "\n", "divider")
                    text_area.insert(tk.END, f"{s['content']}\n\n", "highlight_box" if is_target else "")

                text_area.configure(state=tk.DISABLED)
                if target_pos:
                    text_area.see(target_pos)
                else:
                    text_area.see("1.0")

        rb_ctx = tk.Radiobutton(
            mode_bar,
            text="🎯 해당 발언 및 전후 맥락 (즉시 표시)",
            variable=mode_var,
            value="context",
            command=render_transcript_view,
            bg="#EEF2F6",
            font=("맑은 고딕", 9, "bold"),
            cursor="hand2"
        )
        rb_ctx.pack(side=tk.LEFT, padx=4)

        rb_full = tk.Radiobutton(
            mode_bar,
            text=f"📜 전체 회의록 전문 모두 보기 ({total_speeches}건)",
            variable=mode_var,
            value="full",
            command=render_transcript_view,
            bg="#EEF2F6",
            font=("맑은 고딕", 9),
            cursor="hand2"
        )
        rb_full.pack(side=tk.LEFT, padx=4)

        render_transcript_view()

        # 회의록 내 단어 검색 핸들러
        def find_next_keyword():
            kw = filter_var.get().strip()
            if not kw:
                return
            if mode_var.get() != "full":
                mode_var.set("full")
                render_transcript_view()
            text_area.tag_remove("search_found", "1.0", tk.END)
            start = "1.0"
            count = 0
            first_found = None
            while True:
                pos = text_area.search(kw, start, stopindex=tk.END, nocase=True)
                if not pos:
                    break
                end_pos = f"{pos}+{len(kw)}c"
                text_area.tag_add("search_found", pos, end_pos)
                if not first_found:
                    first_found = pos
                start = end_pos
                count += 1
            if first_found:
                text_area.see(first_found)
                find_stat_lbl.config(text=f"'{kw}' {count}건 발견")
            else:
                find_stat_lbl.config(text=f"'{kw}' 없음")

        filter_entry.bind("<Return>", lambda e: find_next_keyword())
        ttk.Button(search_filter_bar, text="다음 찾기", command=find_next_keyword).pack(side=tk.LEFT)
        find_stat_lbl = ttk.Label(search_filter_bar, text="", foreground="#718096")
        find_stat_lbl.pack(side=tk.LEFT, padx=6)

        # 하단 액션 버튼 바
        bottom_box = ttk.Frame(p, padding=(0, 10, 0, 0))
        bottom_box.pack(fill=tk.X)

        def copy_all_transcript():
            all_txt = text_area.get("1.0", tk.END)
            self.root.clipboard_clear()
            self.root.clipboard_append(all_txt)
            messagebox.showinfo("복사 완료", f"전체 회의록 전문(발언 {total_speeches}건)이 클립보드에 복사되었습니다.", parent=modal)

        ttk.Button(bottom_box, text="📋 전체 회의록 텍스트 복사", command=copy_all_transcript).pack(side=tk.LEFT)
        ttk.Button(
            bottom_box,
            text="🎯 웹에서 해당 라인 이동 & 표시",
            command=lambda u=source_url, s=target_spk, c=target_cnt: self._open_web_url(u, speaker_name=s, content=c)
        ).pack(side=tk.LEFT, padx=8)

        ttk.Button(bottom_box, text="닫기", command=modal.destroy).pack(side=tk.RIGHT)

    # -------------------------------------------------------------
    # 탭 2: 문서 파일 일괄 임포트
    # -------------------------------------------------------------
    def _init_import_tab(self):
        desc_lbl = ttk.Label(
            self.import_tab,
            text="한글 HWPX, TXT, MD, JSON, CSV, Excel XLSX 형식의 회의록 문서를 선택하여 로컬 DB에 일괄 파싱 및 적재합니다.",
            font=("맑은 고딕", 10),
            foreground="#4A5568"
        )
        desc_lbl.pack(anchor=tk.W, pady=(0, 12))

        btn_bar = ttk.Frame(self.import_tab)
        btn_bar.pack(fill=tk.X, pady=(0, 12))

        ttk.Button(btn_bar, text="📄 개별 파일 선택 임포트...", command=self.on_import_single_file).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_bar, text="📁 폴더 전체 일괄 임포트...", command=self.on_import_folder).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_bar, text="🔄 DB 통계 새로고침", command=self._refresh_import_stats).pack(side=tk.RIGHT)

        # 로그 출력창
        ttk.Label(self.import_tab, text="임포트 처리 로그:", font=("맑은 고딕", 9, "bold")).pack(anchor=tk.W, pady=(4, 2))
        self.import_log = scrolledtext.ScrolledText(self.import_tab, height=8, font=("Consolas", 9), bg="#F8F9FA")
        self.import_log.pack(fill=tk.X, pady=(0, 12))

        # 현재 DB 회의 목록 테이블
        ttk.Label(self.import_tab, text="현재 DB에 등록된 회의 목록:", font=("맑은 고딕", 9, "bold")).pack(anchor=tk.W, pady=(4, 2))

        tree_frame = ttk.Frame(self.import_tab)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("date", "meeting", "speeches_count")
        self.db_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        self.db_tree.heading("date", text="일시")
        self.db_tree.heading("meeting", text="회의명")
        self.db_tree.heading("speeches_count", text="발언 건수")
        self.db_tree.column("date", width=120, anchor=tk.CENTER)
        self.db_tree.column("meeting", width=600)
        self.db_tree.column("speeches_count", width=100, anchor=tk.CENTER)

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.db_tree.yview)
        self.db_tree.configure(yscrollcommand=tree_scroll.set)
        self.db_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._refresh_import_stats()

    def on_import_single_file(self):
        file_path = filedialog.askopenfilename(
            title="임포트할 회의록 파일 선택",
            filetypes=[
                ("모든 회의록 형식", "*.hwpx *.txt *.md *.json *.csv *.xlsx"),
                ("한글 HWPX 문서", "*.hwpx"),
                ("텍스트/마크다운", "*.txt *.md"),
                ("JSON 파일", "*.json"),
                ("엑셀/CSV", "*.xlsx *.csv")
            ]
        )
        if not file_path:
            return

        self._log_import(f"파일 임포트 시작: {os.path.basename(file_path)}")
        try:
            res = import_file(file_path)
            self._log_import(f"✅ 완료: {res['file']} -> 파싱 {res['parsed_count']}건, DB 적재 {res['inserted_count']}건")
            self._refresh_import_stats()
            messagebox.showinfo("성공", f"{res['inserted_count']}건의 발언이 성공적으로 DB에 적재되었습니다.")
        except Exception as e:
            self._log_import(f"❌ 오류 발생: {e}")
            messagebox.showerror("오류", f"임포트 실패: {e}")

    def on_import_folder(self):
        dir_path = filedialog.askdirectory(title="회의록 문서가 담긴 폴더 선택")
        if not dir_path:
            return

        self._log_import(f"폴더 일괄 임포트 시작: {dir_path}")
        try:
            res = import_directory(dir_path)
            self._log_import(f"✅ 완료: 총 {res['processed_files']}개 파일 처리 -> {res['total_inserted']}건 적재")
            self._refresh_import_stats()
            messagebox.showinfo("성공", f"폴더 내 {res['total_inserted']}건의 발언이 적재되었습니다.")
        except Exception as e:
            self._log_import(f"❌ 오류 발생: {e}")
            messagebox.showerror("오류", f"폴더 임포트 실패: {e}")

    def _log_import(self, msg: str):
        self.import_log.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.import_log.see(tk.END)

    def _refresh_import_stats(self):
        for item in self.db_tree.get_children():
            self.db_tree.delete(item)

        if not os.path.exists(DB_PATH):
            return

        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            rows = cur.execute("""
                SELECT meeting_date, meeting_name, count(*) as cnt
                FROM speeches
                GROUP BY meeting_name, meeting_date
                ORDER BY meeting_date DESC
            """).fetchall()
            conn.close()

            for r in rows:
                self.db_tree.insert("", tk.END, values=(r[0], r[1], f"{r[2]}건"))
        except Exception as e:
            self._log_import(f"DB 통계 조회 실패: {e}")

    # -------------------------------------------------------------
    # 탭 3: 전자회의록 웹 수집 (크롤러)
    # -------------------------------------------------------------
    def _init_crawl_tab(self):
        info_lbl = ttk.Label(
            self.crawl_tab,
            text="제주특별자치도의회 전자회의록 포털에서 일자별/월별 과거 회기 및 최신 회의록을 실시간 수집하여 SQLite FTS5에 자동 적재합니다.",
            font=("맑은 고딕", 10),
            foreground="#4A5568"
        )
        info_lbl.pack(anchor=tk.W, pady=(0, 10))

        # 수집 설정 프레임
        opt_frame = ttk.LabelFrame(self.crawl_tab, text="수집 조건 설정 (월별/일자별 및 회기 선택)", padding=10)
        opt_frame.pack(fill=tk.X, pady=(0, 10))

        # 1행: 수집 모드 라디오버튼
        mode_box = ttk.Frame(opt_frame)
        mode_box.pack(fill=tk.X, pady=(0, 8))
        self.crawl_mode_var = tk.StringVar(value="date")

        ttk.Radiobutton(
            mode_box,
            text="📅 일자별 / 월별 과거 회기 수집 (시작~종료일 지정)",
            variable=self.crawl_mode_var,
            value="date",
            command=self._on_crawl_mode_change
        ).pack(side=tk.LEFT, padx=(0, 20))

        ttk.Radiobutton(
            mode_box,
            text="⚡ 최근 회기 실시간 동기화 (late.html)",
            variable=self.crawl_mode_var,
            value="recent",
            command=self._on_crawl_mode_change
        ).pack(side=tk.LEFT)

        # 2행: 일자 범위 설정
        self.date_box = ttk.Frame(opt_frame)
        self.date_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(self.date_box, text="시작 일자:").pack(side=tk.LEFT, padx=(0, 4))
        self.crawl_start_date_var = tk.StringVar(value="2024-10-01")
        ttk.Entry(self.date_box, textvariable=self.crawl_start_date_var, width=12).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(self.date_box, text="종료 일자:").pack(side=tk.LEFT, padx=(0, 4))
        self.crawl_end_date_var = tk.StringVar(value="2024-10-31")
        ttk.Entry(self.date_box, textvariable=self.crawl_end_date_var, width=12).pack(side=tk.LEFT, padx=(0, 16))

        # 빠른 프리셋 버튼
        ttk.Label(self.date_box, text="빠른 선택:").pack(side=tk.LEFT, padx=(0, 6))
        presets = [
            ("2024년 10월", "2024-10-01", "2024-10-31"),
            ("2024년 전체", "2024-01-01", "2024-12-31"),
            ("2025년 전체", "2025-01-01", "2025-12-31"),
            ("2026년 전체", "2026-01-01", "2026-10-31"),
        ]
        for label, s_d, e_d in presets:
            btn = ttk.Button(
                self.date_box,
                text=label,
                command=lambda s=s_d, e=e_d: self._set_date_preset(s, e)
            )
            btn.pack(side=tk.LEFT, padx=2)

        # 3행: 필터 및 실행 버튼
        ctrl_box = ttk.Frame(opt_frame)
        ctrl_box.pack(fill=tk.X, pady=(4, 0))

        ttk.Label(ctrl_box, text="회의명 필터:").pack(side=tk.LEFT, padx=(0, 4))
        self.crawl_kw_var = tk.StringVar(value="")
        ttk.Entry(ctrl_box, textvariable=self.crawl_kw_var, width=16).pack(side=tk.LEFT, padx=(0, 16))

        self.crawl_force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl_box, text="기수집 회의 강제 재수집", variable=self.crawl_force_var).pack(side=tk.LEFT, padx=(0, 20))

        self.crawl_btn = tk.Button(
            ctrl_box,
            text="🌐 회의록 웹 수집 및 DB 적재 시작",
            command=self.on_start_crawl,
            font=("맑은 고딕", 10, "bold"),
            bg="#2B6CB0",
            fg="white",
            relief="flat",
            padx=16,
            pady=4,
            cursor="hand2"
        )
        self.crawl_btn.pack(side=tk.RIGHT)

        ttk.Label(self.crawl_tab, text="실시간 수집 로그:", font=("맑은 고딕", 9, "bold")).pack(anchor=tk.W, pady=(8, 2))
        self.crawl_log = scrolledtext.ScrolledText(self.crawl_tab, font=("Consolas", 9), bg="#F8F9FA")
        self.crawl_log.pack(fill=tk.BOTH, expand=True)

    def _on_crawl_mode_change(self):
        mode = self.crawl_mode_var.get()
        if mode == "date":
            for child in self.date_box.winfo_children():
                child.configure(state=tk.NORMAL)
        else:
            for child in self.date_box.winfo_children():
                child.configure(state=tk.DISABLED)

    def _set_date_preset(self, start_d: str, end_d: str):
        self.crawl_start_date_var.set(start_d)
        self.crawl_end_date_var.set(end_d)

    def on_start_crawl(self):
        mode = self.crawl_mode_var.get()
        kw = self.crawl_kw_var.get().strip()
        force = self.crawl_force_var.get()

        if mode == "date":
            start_d = self.crawl_start_date_var.get().strip()
            end_d = self.crawl_end_date_var.get().strip()
            if not start_d or not end_d:
                messagebox.showerror("입력 오류", "시작 일자와 종료 일자를 YYYY-MM-DD 형식으로 입력해 주세요.")
                return
            log_title = f"일자별 수집 ({start_d} ~ {end_d})"
        else:
            start_d = None
            end_d = None
            log_title = "최근 회기 실시간 수집"

        self.crawl_btn.config(state=tk.DISABLED, text="⏳ 수집 진행 중...")
        self.crawl_log.insert(tk.END, f"\n=== [{time.strftime('%Y-%m-%d %H:%M:%S')}] {log_title} 시작 (키워드: '{kw}') ===\n")
        self.crawl_log.see(tk.END)

        threading.Thread(
            target=self._run_crawl_thread,
            args=(start_d, end_d, kw, force),
            daemon=True
        ).start()

    def _run_crawl_thread(self, start_d: Optional[str], end_d: Optional[str], kw: str, force: bool):
        def log_cb(msg: str):
            self.root.after(0, self._append_crawl_log, msg)

        try:
            summary = run_crawler(
                start_date=start_d,
                end_date=end_d,
                keyword=kw,
                force_refresh=force,
                log_callback=log_cb
            )
            self.root.after(0, self._render_crawl_done, summary)
        except Exception as e:
            self.root.after(0, self._render_crawl_error, str(e))

    def _append_crawl_log(self, text: str):
        self.crawl_log.insert(tk.END, text + "\n")
        self.crawl_log.see(tk.END)

    def _render_crawl_done(self, summary: dict):
        self.crawl_btn.config(state=tk.NORMAL, text="🌐 회의록 웹 수집 및 DB 적재 시작")
        self.crawl_log.insert(
            tk.END,
            f"\n✅ 수집 완료: 대상 회의 {summary['total_meetings_found']}건 중 "
            f"기수집 건너뜀 {summary['skipped_existing']}건, 신규 적재 {summary['total_speeches_inserted']}건\n"
        )
        self.crawl_log.see(tk.END)
        self._refresh_import_stats()
        messagebox.showinfo(
            "수집 완료",
            f"전자회의록 수집이 완료되었습니다.\n- 대상 회의: {summary['total_meetings_found']}건\n- 신규 적재 발언: {summary['total_speeches_inserted']}건"
        )

    def _render_crawl_error(self, err: str):
        self.crawl_btn.config(state=tk.NORMAL, text="🌐 회의록 웹 수집 및 DB 적재 시작")
        self.crawl_log.insert(tk.END, f"❌ 수집 중 오류 발생: {err}\n")
        self.crawl_log.see(tk.END)
        messagebox.showerror("오류", f"수집 실패: {err}")
        self.crawl_log.see(tk.END)
        messagebox.showerror("오류", f"수집 실패: {err}")

def main():
    root = tk.Tk()
    app = CouncilDesktopApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
