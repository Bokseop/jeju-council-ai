# 🏛️ 제주특별자치도의회 AI 전자회의록 웹 검색 시스템

[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live%20Service-2563EB?logo=github&style=for-the-badge)](https://Bokseop.github.io/jeju-council-ai/)
[![Database](https://img.shields.io/badge/Official%20Records-53%2C697%20Speeches-0D9488?style=for-the-badge)](https://Bokseop.github.io/jeju-council-ai/)
[![Web App](https://img.shields.io/badge/Platform-100%25%20Pure%20Web-orange?style=for-the-badge)](https://Bokseop.github.io/jeju-council-ai/)

> **별도 프로그램 설치 없이 웹 브라우저에서 53,697건의 제주특별자치도의회 실제 회의록을 0.05초 만에 검색하고, 원문 페이지 줄 이동 및 발언 선택 복사를 지원하는 100% 웹 전용 AI 회의록 분석 시스템입니다.**

---

## 🌟 웹 라이브 서비스 접속

👉 **[https://Bokseop.github.io/jeju-council-ai/](https://Bokseop.github.io/jeju-council-ai/)**

* **PC, 스마트폰, 태블릿 등 모든 브라우저**에서 즉시 사용 가능 (설치 파일 0개)
* 클라우드 유료 API 비용 0원, 브라우저 클라이언트 사이드 초고속 인덱싱

---

## 📌 핵심 기능

### 1. 🎯 검색된 핵심 발언 상단 고정 & 아래로 이어지는 대화 뷰어
* 검색 결과에서 **[📖 이 회의 전체 회의록 보기]**를 누르면, **검색된 핵심 발언 번호가 모달 최상단에 즉시 표시**됩니다.
* 해당 발언 아래로 질의와 답변 대화가 순서대로 자연스럽게 펼쳐져, **아래로 스크롤하며 앞뒤 문맥을 즉시 정독**할 수 있습니다.
* 필요 시 상단의 `[▲ 이 발언 이전의 질의·대화 불러오기]`를 통해 이전 상황도 손쉽게 확인할 수 있습니다.

### 2. ☑️ 발언별 체크박스 및 선택 복사
* 회의록 내 모든 발언에 **체크박스**가 제공됩니다.
* 검색된 핵심 발언은 기본으로 체크되어 있으며, 필요한 질문과 답변들을 복수 선택할 수 있습니다.
* 상단의 **`[☑️ 선택 복사 (N건)]`** 버튼을 누르면 선택한 발언들만 회의명·일시·발언자 정보와 함께 클립보드에 깔끔하게 복사됩니다.
* `[☑️ 전체 선택]`, `[⬜ 선택 해제]`, 개별 `[📋 단일 복사]` 지원

### 3. 🌐 공식 전자회의록 원문 라인 자동 이동 & 하이라이트 브릿지
* **[🎯 공식 원문 라인 이동 & 열기]** 클릭 시, 도의회 공식 전자회의록 웹페이지(`record.council.jeju.kr`)가 열리며 **해당 발언 줄로 화면이 자동 스크롤**됩니다.

### 4. 🔄 GitHub Actions 매일 최신 회의록 자동 수집 및 배포
* `.github/workflows/auto_crawl.yml` 워크플로우가 매일 자동으로 최신 제주도의회 전자회의록을 수집하여 웹 데이터(`data_web/`)를 최신 상태로 유지합니다.

---

## 📁 프로젝트 구조

```
jeju-council-ai/
├── index.html                  # 메인 웹 애플리케이션 인터페이스
├── style.css                   # 모던 반응형 스타일시트
├── app.js                      # 5.3만 건 클라이언트사이드 고속 검색 및 모달 엔진
├── data_web/                   # 브라우저 서빙용 최적화 데이터 (압축 JSON)
│   ├── meetings.json           # 회의 목록 메타데이터
│   ├── speeches.json           # 53,697건 발언 데이터
│   └── stats.json              # DB 통계 정보
├── .github/workflows/
│   └── auto_crawl.yml          # GitHub Actions 매일 자동 크롤링 & 배포
├── crawler.py                  # 도의회 공식 전자회의록 수집 스크립트
├── export_web_data.py          # SQLite DB -> 웹용 JSON 변환기
└── jeju_council.db             # SQLite 백본 데이터베이스 (50.8MB)
```

---

## 💻 로컬 실행 방법 (개발용)

별도의 의존성 없이 Python 내장 서버로 즉시 구동할 수 있습니다:

```bash
# 로컬 웹서버 실행
python -m http.server 8000

# 브라우저에서 접속
http://localhost:8000
```

---

## 📜 데이터 출처 및 라이선스
* 데이터 출처: [제주특별자치도의회 전자회의록 시스템](https://record.council.jeju.kr/) (공공누리 제1유형: 출처표시, 상업적·비상업적 이용 가능)
* 라이선스: MIT License
