"""BridgeDocs 웹 화면: Front/dist/를 이 PC 안(127.0.0.1)에서만 열고 Edge 앱 창으로 띄운다. 창을 닫으면 끝난다.

화면의 버튼은 /api/<이름>(web_api.Api의 메서드)을 부른다. 다른 사이트가 이 서버를 부르지 못하게
Host가 127.0.0.1인지와 화면만 붙이는 X-BD 머리글을 확인한다(머리글이 붙은 다른 출처 요청은 브라우저가 막음).
"""
import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]   # 프로젝트 맨 위
DIST = ROOT / "Front" / "dist"
sys.path[:0] = [str(ROOT), str(ROOT / "SERVER"), str(ROOT / "Fuc")]   # DB·Fuc 폴더, 서버 파일, 엔진(import bridgedocs)
EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def find_browser():
    for p in EDGE_PATHS:
        if os.path.exists(p):
            return p
    return shutil.which("msedge") or shutil.which("chrome")


class Handler(http.server.SimpleHTTPRequestHandler):
    api = None      # web_api.Api (start_server가 넣음). None이면 화면 파일만 준다

    def log_message(self, *args):
        pass

    def _reply(self, body: dict, code: int = 200) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        host = self.headers.get("Host", "").split(":")[0]
        if host != "127.0.0.1" or self.headers.get("X-BD") != "1" or not self.path.startswith("/api/") or not self.api:
            return self._reply({"error": "허용하지 않는 요청"}, 403)
        name = self.path[5:].split("?")[0]
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            if name == "upload":
                result = self.api.save_upload(self.headers.get("X-Batch", ""), unquote(self.headers.get("X-Name", "")), body)
            else:
                fn = getattr(self.api, name, None)
                if name.startswith("_") or not callable(fn):
                    return self._reply({"error": f"없는 기능 — {name}"}, 404)
                result = fn(**(json.loads(body or b"{}")))
        except ValueError as err:          # 엔진의 약속: 화면에 그대로 보여 줄 한국어 문구
            return self._reply({"error": str(err)})
        except Exception as err:
            traceback.print_exc()
            return self._reply({"error": f"처리 중 오류: {err}"})
        self._reply({"ok": result})


def start_server(api=None):
    Handler.api = api
    handler = functools.partial(Handler, directory=str(DIST))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)  # 0: 빈 포트를 고른다
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def make_api():
    import offline
    offline.ensure_offline()      # torch·paddle을 불러오기 전에
    from service import DesktopService
    from web_api import Api
    return Api(DesktopService())


def main():
    if not (DIST / "index.html").exists():
        sys.exit("Front/dist/index.html이 없습니다. Front 폴더에서 먼저 npm run build 를 실행하세요.")
    browser = find_browser()
    if not browser:
        sys.exit("Edge나 Chrome을 찾지 못했습니다.")
    api = make_api()
    server = start_server(api)
    url = f"http://127.0.0.1:{server.server_port}/index.html"
    # 따로 쓰는 프로필이라야 창을 닫을 때까지 기다린다(이미 열린 Edge에 붙지 않음)
    profile = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "BridgeDocs" / "edge-profile"
    subprocess.run([browser, f"--app={url}", f"--user-data-dir={profile}",
                    "--no-first-run", "--start-maximized"])
    api.cancel_all()      # 창을 닫으면 읽던 작업을 멈춘다
    server.shutdown()


if __name__ == "__main__":
    main()
