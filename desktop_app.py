import os
import sys

# Force UTF-8 encoding (stdout is None in a windowed PyInstaller build)
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8')

if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(app_dir)

def unblock_app_files(root):
    """Remove the "downloaded from the internet" mark (Zone.Identifier stream) from the app's files.

    Zips downloaded with a browser (e.g. from a board or a web drive) pass the mark on to every
    extracted file, and .NET then refuses to load the blocked pythonnet / WebView2 assemblies,
    so the window never opens. Must run before webview loads them.
    """
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            try:
                os.remove(os.path.join(dirpath, name) + ':Zone.Identifier')
            except OSError:
                pass  # not blocked, or no permission

unblock_app_files(app_dir)

import time
import threading
import uvicorn
import webview
import socket

EXPORT_FILE_TYPES = {
    'txt': ('텍스트 파일 (*.txt)',),
    'hwpx': ('한글 문서 (*.hwpx)',),
}

class Api:
    """Exposed to the page as window.pywebview.api (WebView2 blocks <a download> links)."""

    def save_export(self, fmt):
        try:
            from app import build_export
            data, filename, _ = build_export(fmt)
            dialog = getattr(getattr(webview, 'FileDialog', None), 'SAVE', None) or webview.SAVE_DIALOG
            result = webview.windows[0].create_file_dialog(
                dialog, save_filename=filename, file_types=EXPORT_FILE_TYPES.get(fmt, ())
            )
            if not result:
                return {'ok': False, 'cancelled': True}
            path = result if isinstance(result, str) else result[0]
            if not path.lower().endswith('.' + fmt):
                path += '.' + fmt
            with open(path, 'wb') as f:
                f.write(data)
            return {'ok': True, 'path': path}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def start_backend(port):
    from app import app
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()

def main():
    # 1. Select port
    port = 8000
    # Use 8000 only if nothing answers there; a bind test alone succeeds on Windows
    # even when another server already listens on 0.0.0.0:8000.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        in_use = s.connect_ex(('127.0.0.1', port)) == 0
    if not in_use:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
        except OSError:
            in_use = True
    if in_use:
        port = find_free_port()

    # 2. Run backend in background daemon thread
    t = threading.Thread(target=start_backend, args=(port,), daemon=True)
    t.start()

    # Wait for server to respond
    url = f"http://127.0.0.1:{port}"
    time.sleep(1.2)

    # 3. Create native standalone desktop window
    webview.settings['ALLOW_DOWNLOADS'] = True
    window = webview.create_window(
        title="🔴 YouTube Live AI 속기 (Standalone)",
        url=url,
        width=1320,
        height=860,
        min_size=(1000, 650),
        text_select=True,
        confirm_close=False,
        js_api=Api()
    )

    # 4. Start native desktop app loop
    print(f"Starting standalone desktop GUI on {url}...")
    webview.start()
    print("Desktop application closed.")
    sys.exit(0)

if __name__ == '__main__':
    main()
