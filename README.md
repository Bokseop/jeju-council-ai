# 🏛️ 제주특별자치도의회 AI 전자회의록 검색 시스템 (Jeju Council AI)

[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live%20Demo-2563EB?logo=github&style=flat-square)](https://Bokseop.github.io/jeju-council-ai/)
[![Database](https://img.shields.io/badge/SQLite%20FTS5-53%2C697%20Speeches-0D9488?style=flat-square)](https://github.com/Bokseop/jeju-council-ai)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)

> **100% 실제 공식 회의록 데이터 기반 • No-Cloud 로컬 초고속 검색 • 공식 전자회의록 원문 라인 자동 이동 & 하이라이트**

---

## 🌟 웹 라이브 서비스 (GitHub Pages)

👉 **[제주특별자치도의회 AI 전자회의록 웹 검색 바로가기](https://Bokseop.github.io/jeju-council-ai/)**

설치 없이 웹 브라우저에서 53,697건의 발언을 0.05초 만에 검색하고, 클릭 한 번으로 도의회 공식 전자회의록 원문 페이지의 **해당 발언 줄로 화면이 부드럽게 자동 스크롤 & 하이라이트**되는 기능을 즉시 체험하실 수 있습니다.

---

## 📌 주요 특징

### 1. 100% 실제 공식 회의록 데이터 적재
* 가상/샘플 데이터 0건, 제주특별자치도의회 공식 전자회의록 시스템(`https://record.council.jeju.kr`) 전수 크롤링 데이터
* **53,697건 발언록** 및 **164개 회의** (제455회 임시회 본회의/상임위원회 및 연도별 주요 회기)
* SQLite FTS5 전문 역색인 기반 0.002초 초고속 검색

### 2. 🎯 스마트 공식 웹 라인 자동 스크롤 & 하이라이트 브릿지
* 도의회 원문 웹페이지는 수십만 자의 회의록이 모여 있어 원하는 발언을 찾기 어렵습니다.
* 본 시스템은 브라우저 접속 즉시 **해당 발언 위치로 화면을 부드럽게 자동 스크롤(`scrollIntoView`)**하고, **황금빛 테두리 상자와 `★ AI 검색 일치 발언` 알림 뱃지**를 선명하게 표시합니다.

### 3. 📖 이 회의 전체 회의록 보기 (전문 정독 모달)
* 개회부터 산회까지 모든 발언을 시간순으로 정렬하여 출력
* 검색된 발언 위치로 자동 포커싱되어 **"앞뒤로 오간 질문과 답변의 전체 맥락"**을 한눈에 파악 가능
* 회의록 내 실시간 키워드 찾기 기능 지원

### 4. 🖥️ 데스크톱 GUI 프로그램 (`desktop_council_app.py`)
* 파이썬 내장 Tkinter 기반 경량 데스크톱 프로그램
* **탭 1**: 키워드 & 추천 질문 AI 검색, 발언 전문 띄우기, 전체 회의록 뷰어, 웹 라인 이동 브릿지
* **탭 2**: 한글 문서(`.hwpx`), 텍스트(`.txt`), 엑셀(`.xlsx`), `.json`, `.csv` 일괄 임포트
* **탭 3**: 도의회 전자회의록 실시간 크롤러 (월별/일자별 기간 수집 및 증분 업데이트)

### 5. 🔌 Claude Desktop / Cursor 연동 표준 MCP 서버 (`mcp_server.py`)
* JSON-RPC 2.0 표준 stdio 프로토콜 지원
* Claude Desktop, Cursor 등 외부 LLM에서 로컬 회의록을 사실 기반으로 직접 검색·인용 가능

---

## 🚀 빠른 시작 가이드

### 1. 웹 브라우저 모드 (GitHub Pages 또는 로컬 웹서버)
* **온라인 접속**: [https://Bokseop.github.io/jeju-council-ai/](https://Bokseop.github.io/jeju-council-ai/)
* **로컬 웹서버 실행**:
  ```bash
  python web_app.py
  # 브라우저에서 http://localhost:8000 자동 오픈
  ```

### 2. 데스크톱 앱 실행
* **윈도우 원클릭 실행**: `실행하기_제주도의회AI검색.bat` 더블 클릭
* **파이썬 실행**:
  ```bash
  pip install -r requirements.txt
  python desktop_council_app.py
  ```

### 3. Claude Desktop MCP 연동
`claude_desktop_config.json`에 아래 설정을 추가합니다:
```json
{
  "mcpServers": {
    "jeju-council": {
      "command": "python",
      "args": ["<저장경로>/mcp_server.py"]
    }
  }
}
```

---

## 📁 프로젝트 구조

```
jeju-council-ai/
├── index.html                  # GitHub Pages & 웹 검색 메인 페이지
├── style.css                   # 모던 반응형 스타일시트
├── app.js                      # 5.3만건 고속 클라이언트 검색 엔진
├── web_app.py                  # 로컬 내장 HTTP 웹 서버
├── data_web/                   # 웹 서빙용 최적화 JSON 데이터 (5.4MB 압축)
│   ├── meetings.json           # 164개 회의 메타데이터
│   ├── speeches.json           # 53,697건 발언 데이터
│   └── stats.json              # DB 통계 정보
├── jeju_council.db             # 50.8MB SQLite 원본 데이터베이스 (FTS5 색인)
├── desktop_council_app.py      # 데스크톱 GUI 애플리케이션
├── web_highlighter.py          # 웹 라인 자동 스크롤 & 하이라이트 브릿지
├── local_pipeline.py           # FTS5 + Cross-Encoder 로컬 판별 파이프라인
├── keyword_extractor.py        # 로컬 한국어 형태소/조사 분리 토크나이저
├── batch_importer.py           # HWPX, TXT, JSON, CSV, XLSX 파서
├── crawler.py                  # 도의회 전자회의록 실시간 웹 수집기
├── mcp_server.py               # JSON-RPC 2.0 표준 MCP 서버
├── build_package.py            # 배포용 ZIP 패키징 빌더
├── export_web_data.py          # DB -> 웹 JSON 추출기
├── requirements.txt            # 파이썬 필수 의존성
└── 실행하기_제주도의회AI검색.bat # 윈도우 원클릭 실행 배치 스크립트
```

---

## 📜 라이선스 및 데이터 출처
* 회의록 원문 데이터의 모든 저작권은 **제주특별자치도의회**에 있으며, 공공누리 제1유형(출처표시 조건 자유이용)에 따라 공공 데이터로 활용됩니다.
* 프로그램 소스 코드는 MIT License에 따라 자유롭게 사용 및 배포 가능합니다.
