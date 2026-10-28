@echo off
cd /d "%~dp0"
title 제주특별자치도의회 AI 회의록 검색 시스템
cls
echo ======================================================================
echo   제주특별자치도의회 100%% 로컬 AI 회의록 분석 시스템
echo   - No-Cloud / 완전 오프라인 (API 비용 0원, 외부 유출 0%%)
echo   - SQLite FTS5 고속 색인 + Whisper-small급 AI 판별 엔진
echo ======================================================================
echo.
echo 프로그램을 실행합니다. 잠시만 기다려 주세요...
echo.
python desktop_council_app.py
if errorlevel 1 (
    echo.
    echo [오류] 프로그램 실행 중 오류가 발생했습니다.
    pause
)
