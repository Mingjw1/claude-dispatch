import threading
import urllib.request

from agentdesk import __main__ as entry
from helpers import wait_for


def test_main_starts_server_and_opens_the_project(tmp_path, repo, monkeypatch):
    app_dir = tmp_path / "app"   # 不碰真实应用目录里的 recent.json
    app_dir.mkdir()
    (app_dir / "agents.toml").write_text((entry.APP_DIR / "agents.toml").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    monkeypatch.setattr(entry, "APP_DIR", app_dir)
    opened = []
    monkeypatch.setattr(entry, "open_window", opened.append)
    servers = []
    started = threading.Event()
    real = entry.make_server

    def capture(app, port=0):
        srv = real(app, port)
        servers.append((srv, app))
        started.set()
        return srv

    monkeypatch.setattr(entry, "make_server", capture)
    threading.Thread(target=entry.main, args=([str(repo)],), daemon=True).start()
    assert started.wait(10)
    srv, app = servers[0]
    try:
        wait_for(lambda: opened)
        assert opened[0].startswith("http://127.0.0.1:") and f"t={app.token}" in opened[0]
        assert app.current is not None and app.current.repo.name == "仓库"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        assert opener.open(opened[0], timeout=10).status == 200
    finally:
        srv.shutdown()
        for engine in app.engines.values():
            engine.close()
