"""启动：python -m agentdesk [项目目录]。启动本地服务并用 Edge 应用窗口打开界面。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from agentdesk.server import App, make_server

APP_DIR = Path(__file__).resolve().parent.parent
EDGE_PATHS = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
              Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]


def find_edge() -> str | None:
    return shutil.which("msedge") or next((str(p) for p in EDGE_PATHS if p.is_file()), None)


def open_window(url: str) -> None:
    edge = find_edge()
    if edge:
        subprocess.Popen([edge, f"--app={url}"])
    else:
        webbrowser.open(url)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    app = App(APP_DIR)
    project = argv[0] if argv else next(iter(app.recent()), None)
    if project:
        try:
            app.open_project(project)
        except Exception as e:  # 打不开就让用户在界面里重选
            print(f"打开项目失败：{e}")
    server = make_server(app)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={app.token}"
    print(f"Agent 指挥台已启动：{url}\n关闭这个窗口即退出。")
    open_window(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for engine in app.engines.values():
            engine.close()


if __name__ == "__main__":
    main()
