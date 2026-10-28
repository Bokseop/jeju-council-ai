"""
web_app.py
제주특별자치도의회 AI 회의록 로컬 웹서버 실행기
- 내장 HTTP 서버로 index.html, style.css, app.js, data_web 서빙
- 별도 외부 프레임워크 설치 없이 즉시 구동 가능
"""

import http.server
import socketserver
import webbrowser
import os
import sys

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # Enable CORS and caching headers
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

def run_server(port=PORT):
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    url = f"http://localhost:{port}"
    print("=" * 65)
    print("🏛️ [제주특별자치도의회 AI 전자회의록 로컬 웹 서버 시작]")
    print(f"• 접속 주소: {url}")
    print(f"• 웹 루트: {DIRECTORY}")
    print("• 종료하려면 Ctrl+C를 누르십시오.")
    print("=" * 65)

    webbrowser.open(url)

    with socketserver.TCPServer(("", port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[서버 종료] 웹 서버가 안전하게 종료되었습니다.")

if __name__ == "__main__":
    run_server()
