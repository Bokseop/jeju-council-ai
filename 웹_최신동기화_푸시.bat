@echo off
chcp 949 >nul
title 제주도의회 웹 호스팅 최신 회의록 동기화
cd /d "%~dp0"

echo ======================================================================
echo   제주특별자치도의회 웹 라이브 호스팅 최신 데이터 동기화
echo   1. 도의회 전자회의록 최신 회기 자동 크롤링 (증분 업데이트)
echo   2. 웹용 데이터셋(JSON) 자동 재생성
echo   3. GitHub Pages 자동 푸시 및 웹 라이브 사이트 갱신
echo ======================================================================
echo.

:: 1. 최신 회의록 크롤링
echo [*] 1단계: 도의회 전자회의록 최근 회기 수집 중...
python crawler.py --limit 10
if %errorlevel% neq 0 (
    echo [경고] 크롤링 중 오류가 발생했으나 기존 데이터로 진행합니다.
)

:: 2. 공식 웹 링크 동기화
echo [*] 2단계: 공식 원문 직통 링크 동기화 중...
python sync_meeting_urls.py

:: 3. 웹용 JSON 데이터 추출
echo [*] 3단계: 웹 호스팅용 데이터셋(data_web) 갱신 중...
python export_web_data.py

:: 4. 배포용 압축 파일 재생성
echo [*] 4단계: 배포용 압축 파일 동기화 중...
python build_package.py

:: 5. 깃허브 푸시 (GitHub Pages 자동 재빌드 트리거)
echo [*] 5단계: GitHub Pages로 최신 데이터 전송(Push) 중...
git add data_web/ jeju_council.db README.md index.html style.css app.js
git commit -m "chore: 최신 제주도의회 회의록 데이터 자동 동기화 갱신"
git push origin master

echo.
echo ======================================================================
echo   [성공] 웹 라이브 호스팅 갱신이 완료되었습니다!
echo   접속 주소: https://bokseop.github.io/jeju-council-ai/
echo   (약 30초~1분 후 웹사이트에 최신 회의록이 자동 반영됩니다)
echo ======================================================================
pause
