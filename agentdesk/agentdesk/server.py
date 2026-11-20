"""本地 HTTP 服务：只听 127.0.0.1，口令校验，JSON 接口和 SSE 推送。"""
from __future__ import annotations

import json
import queue
import re
import secrets
import shutil
import subprocess
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentdesk import gitops, merger
from agentdesk.commander import Commander, CommanderError, commander_argv
from agentdesk.config import LEVELS, AppConfig, ConfigError, load_config
from agentdesk.engine import Engine, EngineError
from agentdesk.proc import NO_WINDOW, run_io
from agentdesk.store import Store

WEB = Path(__file__).resolve().parent / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8"}
PICK_FOLDER_PS = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true};"
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
    "$d.Description = '选择一个 git 仓库目录';"
    "if ($d.ShowDialog($owner) -eq 'OK') { [Console]::OutputEncoding = [Text.Encoding]::UTF8; $d.SelectedPath }")


def pick_folder() -> str:
    """弹出系统的选择文件夹对话框，返回选中的路径；取消时返回空字符串。"""
    r = subprocess.run(["powershell", "-STA", "-NoProfile", "-Command", PICK_FOLDER_PS], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return r.stdout.strip()


USER_ERRORS = (EngineError, ConfigError, CommanderError, gitops.GitError, merger.MergeError, KeyError, ValueError)


class Bus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # 页面太久没读，丢掉；页面重连后会整体刷新

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=10000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)


class App:
    """当前打开的项目和它的引擎。切换项目时保留已打开项目的引擎，后台任务继续跑。"""

    def __init__(self, app_dir: Path, make_commander=None):
        self.app_dir = app_dir
        self.token = secrets.token_urlsafe(16)
        self.bus = Bus()
        self.engines: dict[Path, Engine] = {}
        self.current: Engine | None = None
        self.probes: dict[str, dict] = {}
        self.pick_folder = pick_folder
        self._make_commander = make_commander or (
            lambda repo, cfg: Commander(commander_argv(cfg.commander_model), repo))
        self._lock = threading.Lock()

    def recent(self) -> list[str]:
        path = self.app_dir / "recent.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def _remember(self, repo: Path) -> None:
        items = [str(repo)] + [p for p in self.recent() if p != str(repo)]
        (self.app_dir / "recent.json").write_text(json.dumps(items[:10], ensure_ascii=False), encoding="utf-8")

    def open_project(self, path: str) -> Engine:
        try:
            repo = gitops.repo_root(Path(path.strip().strip('"')))
        except (gitops.GitError, OSError) as e:
            raise EngineError(f"{path} 不是 git 仓库") from e
        with self._lock:
            engine = self.engines.get(repo)
            if engine is None:
                cfg = load_config(self.app_dir / "agents.toml", repo)
                engine = Engine(repo, cfg, Store(gitops.store_dir(repo)), self._make_commander(repo, cfg),
                                lambda event, r=repo: self.bus.publish({**event, "project": str(r)}))
                engine.start()
                self.engines[repo] = engine
                threading.Thread(target=self._probe, args=(cfg,), daemon=True).start()
            self.current = engine
        self._remember(repo)
        self.bus.publish({"type": "reload"})
        return engine

    def _probe(self, cfg: AppConfig) -> None:
        def one(key: str, first_command: list[str]) -> None:
            exe = shutil.which(first_command[0])
            if exe is None:
                result = {"ok": False, "reason": f"找不到命令 {first_command[0]}"}
            else:
                try:
                    code, out, err = run_io([exe, "--version"], self.app_dir, "", 120)
                    result = {"ok": code == 0, "reason": "" if code == 0 else (err or out).strip()[-200:]}
                except (TimeoutError, OSError) as e:
                    result = {"ok": False, "reason": str(e)}
            self.probes[key] = result
            self.bus.publish({"type": "reload"})

        for key, agent in cfg.agents.items():
            self.probes.setdefault(key, {"ok": None, "reason": "检测中"})
            threading.Thread(target=one, args=(key, next(iter(agent.commands.values()))), daemon=True).start()

    def state(self) -> dict:
        base = {"recent": self.recent(), "levels": LEVELS}
        e = self.current
        if e is None:
            return {**base, "project": None}
        agents = [{"key": a.key, "label": a.label, "models": a.models, "default_model": a.default_model,
                   "levels": a.levels(), **self.probes.get(a.key, {"ok": None, "reason": "检测中"})}
                  for a in e.cfg.agents.values()]
        return {**base, "project": str(e.repo), "branch": gitops.branch(e.repo), "max_parallel": e.cfg.max_parallel,
                "agents": agents, "requirements": [asdict(r) for r in e.store.requirements()],
                "tasks": [asdict(t) for t in e.store.tasks()]}


def _message(e: Exception) -> str:
    return str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)


class Handler(BaseHTTPRequestHandler):
    app: App  # make_server 里绑定

    def log_message(self, *args) -> None:
        pass

    def _authorized(self) -> bool:
        given = self.headers.get("X-Token") or (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
        return secrets.compare_digest(given, self.app.token)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _text(self, text: str) -> None:
        self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/static/"):
            f = WEB / path.removeprefix("/static/")
            if f.parent == WEB and f.is_file():
                return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
            return self._send(404, b"", "text/plain")
        if not self._authorized():
            return self._send(403, "口令不对，请从启动窗口打开".encode("utf-8"), "text/plain; charset=utf-8")
        if path == "/":
            return self._send(200, (WEB / "index.html").read_bytes(), TYPES[".html"])
        if path == "/api/state":
            return self._json(200, self.app.state())
        if path == "/api/events":
            return self._events()
        m = re.fullmatch(r"/api/tasks/(t\d+)/(log|diff)", path)
        if m and self.app.current is not None:
            e = self.app.current
            try:
                if m[2] == "log":
                    return self._text(e.store.read_log(m[1]))
                t = e.store.get_task(m[1])
                return self._text(gitops.diff(e.repo, t.base, t.commit) if t.base and t.commit else "")
            except USER_ERRORS as err:
                return self._json(400, {"error": _message(err)})
        self._send(404, b"", "text/plain")

    def do_POST(self) -> None:
        if not self._authorized():
            return self._json(403, {"error": "口令不对"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            result = self._post(urlparse(self.path).path, body)
        except USER_ERRORS as e:
            return self._json(400, {"error": _message(e)})
        self._json(200, result if result is not None else {"ok": True})

    def _post(self, path: str, b: dict):
        app = self.app
        if path == "/api/open":
            app.open_project(b["path"])
            return None
        if path == "/api/pick-folder":
            return {"path": app.pick_folder()}
        e = app.current
        if e is None:
            raise EngineError("请先打开一个项目")
        if path == "/api/requirements":
            return asdict(e.submit_requirement(b["text"]))
        if path == "/api/tasks":
            return asdict(e.create_task(b.get("title", ""), b["prompt"], b["agent"], b["model"], b["level"],
                                        b.get("accept", "")))
        m = re.fullmatch(r"/api/requirements/(r\d+)/(start|merge|undo|keep|tasks)", path)
        if m:
            req_id, action = m.groups()
            if action == "tasks":
                return asdict(e.add_draft(req_id, b.get("title", ""), b["prompt"], b["agent"], b["model"],
                                          b["level"], b.get("accept", ""), b.get("files", []), b.get("after", [])))
            {"start": e.start_requirement, "merge": e.merge_requirement, "undo": e.undo_requirement,
             "keep": e.keep_requirement_merge}[action](req_id)
            return None
        m = re.fullmatch(r"/api/tasks/(t\d+)/(update|delete|stop|discard|review|approve|merge|undo|keep|retry)", path)
        if m:
            task_id, action = m.groups()
            if action == "update":
                return asdict(e.update_draft(task_id, **b))
            if action == "retry":
                e.retry(task_id, b["prompt"], b["agent"], b["model"], b["level"], bool(b.get("keep")))
                return None
            getattr(e, {"delete": "delete_draft", "stop": "stop", "discard": "discard", "review": "review_task",
                        "approve": "approve", "merge": "merge_task", "undo": "undo_task",
                        "keep": "keep_task_merge"}[action])(task_id)
            return None
        raise EngineError("没有这个接口")

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = self.app.bus.subscribe()
        try:
            while True:
                try:
                    data = f"data: {json.dumps(q.get(timeout=15), ensure_ascii=False)}\n\n"
                except queue.Empty:
                    data = ": ping\n\n"
                self.wfile.write(data.encode("utf-8"))
                self.wfile.flush()
        except OSError:
            pass  # 页面关了
        finally:
            self.app.bus.unsubscribe(q)


def make_server(app: App, port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server
