import http.client
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agentdesk import gitops
from agentdesk.server import App, make_server
from helpers import FAKES, FakeCommander, wait_for

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 公司代理不能拦 127.0.0.1


@pytest.fixture
def server(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    py, fa = Path(sys.executable).as_posix(), (FAKES / "fake_agent.py").as_posix()
    (app_dir / "agents.toml").write_text(f'''max_parallel = 2
[agents.fake]
label = "Fake"
models = ["m1"]
[agents.fake.commands]
read = ["{py}", "{fa}", "--model", "{{model}}"]
edit = ["{py}", "{fa}", "--model", "{{model}}"]
''', encoding="utf-8")
    app = App(app_dir, make_commander=lambda repo, cfg: FakeCommander())
    srv = make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    for engine in app.engines.values():
        engine.close()


def call(server, path, body=None, token=True):
    app, base = server
    headers = {"Content-Type": "application/json", **({"X-Token": app.token} if token else {})}
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        with NO_PROXY.open(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def state(server):
    return json.loads(call(server, "/api/state")[1])


def test_token_is_required(server):
    assert call(server, "/api/state", token=False)[0] == 403
    assert call(server, "/api/open", {"path": "x"}, token=False)[0] == 403


def test_static_files_are_public_without_traversal(server):
    assert call(server, "/static/index.html", token=False)[0] == 200
    assert call(server, "/static/..%2Fserver.py", token=False)[0] == 404


def test_state_before_any_project(server):
    s = state(server)
    assert s["project"] is None and s["recent"] == [] and s["levels"]["edit"] == "改代码"


def test_open_project_lists_agents_and_remembers_it(server, repo):
    assert call(server, "/api/open", {"path": str(repo)})[0] == 200
    s = state(server)
    assert s["project"] == str(gitops.repo_root(repo)) and s["branch"] == "main"
    assert s["agents"][0]["key"] == "fake" and s["agents"][0]["levels"] == ["read", "edit"]
    assert wait_for(lambda: state(server)["agents"][0]["ok"] is True)
    assert s["recent"] == [str(gitops.repo_root(repo))]


def test_open_non_repo_gives_chinese_error(server, tmp_path):
    code, body = call(server, "/api/open", {"path": str(tmp_path / "nope")})
    assert code == 400 and "不是 git 仓库" in json.loads(body)["error"]


def test_manual_task_through_api_with_log_and_diff(server, repo):
    call(server, "/api/open", {"path": str(repo)})
    code, body = call(server, "/api/tasks", {"title": "t", "prompt": "WRITE a.txt 你好", "agent": "fake",
                                             "model": "m1", "level": "edit", "accept": ""})
    assert code == 200
    tid = json.loads(body)["id"]
    wait_for(lambda: next(t for t in state(server)["tasks"] if t["id"] == tid)["status"] == "done")
    assert "> WRITE a.txt 你好" in call(server, f"/api/tasks/{tid}/log")[1]
    assert "+你好" in call(server, f"/api/tasks/{tid}/diff")[1]


def test_bad_request_returns_400_with_message(server, repo):
    assert call(server, "/api/tasks", {"prompt": "p", "agent": "fake", "model": "m1", "level": "edit"})[0] == 400
    call(server, "/api/open", {"path": str(repo)})
    code, body = call(server, "/api/tasks", {"prompt": "p", "agent": "nope", "model": "m1", "level": "edit"})
    assert code == 400 and "没有执行者 nope" in json.loads(body)["error"]
    code, body = call(server, "/api/tasks/t0099/stop", {})
    assert code == 400 and "没有任务 t0099" in json.loads(body)["error"]


def test_events_stream_reports_task_changes(server, repo):
    app, base = server
    call(server, "/api/open", {"path": str(repo)})
    conn = http.client.HTTPConnection("127.0.0.1", int(base.rsplit(":", 1)[1]), timeout=10)
    conn.request("GET", f"/api/events?t={app.token}")
    resp = conn.getresponse()
    assert resp.status == 200
    call(server, "/api/tasks", {"prompt": "PRINT hi", "agent": "fake", "model": "m1", "level": "read"})
    for _ in range(200):
        line = resp.fp.readline().decode("utf-8")
        if line.startswith("data:") and json.loads(line[5:])["type"] == "task":
            break
    else:
        pytest.fail("没收到任务事件")
    conn.close()


def test_ui_page_and_assets_are_served(server):
    app, _ = server
    code, html = call(server, f"/?t={app.token}", token=False)
    assert code == 200 and "/static/app.js" in html and "新需求给 Claude" in html
    code, js = call(server, "/static/app.js", token=False)
    assert code == 200 and "EventSource" in js
    assert call(server, "/static/style.css", token=False)[0] == 200


def test_pick_folder_returns_chosen_path(server, tmp_path):
    app, _ = server
    app.pick_folder = lambda: str(tmp_path)
    code, body = call(server, "/api/pick-folder", {})
    assert code == 200 and json.loads(body) == {"path": str(tmp_path)}
    app.pick_folder = lambda: ""
    assert json.loads(call(server, "/api/pick-folder", {})[1]) == {"path": ""}
