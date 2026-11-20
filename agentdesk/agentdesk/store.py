"""需求、任务、日志：存在 <git 公共目录>/agentdesk/ 下，不进版本库。"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DRAFT, QUEUED, RUNNING, DONE, REVIEWING = "draft", "queued", "running", "done", "reviewing"
PASSED, REJECTED, FAILED, STOPPED = "passed", "rejected", "failed", "stopped"
INTERRUPTED, DISCARDED, MERGED = "interrupted", "discarded", "merged"

PLANNING, CONFIRM_PLAN, IN_PROGRESS = "planning", "confirm_plan", "running"
CONFIRM_MERGE, REQ_MERGED = "confirm_merge", "merged"


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    agent: str
    model: str
    level: str
    accept: str = ""
    files: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    requirement: str | None = None
    status: str = DRAFT
    problems: list[str] = field(default_factory=list)  # 拆任务时发现的配置问题，确认表格里标红
    base: str = ""            # worktree 起点提交
    branch: str = ""
    worktree: str = ""
    commit: str = ""          # 应用在任务分支上的最新提交
    exit_code: int | None = None
    accept_code: int | None = None
    accept_output: str = ""
    review: dict | None = None   # {"verdict", "reasons", "retry_prompt"}
    attempts: int = 0
    pid: int | None = None
    merge: dict | None = None    # 手动任务的合并结果，格式见 merger.merge_tasks
    error: str = ""
    started: float | None = None  # 最近一次开始运行的时间（time.time()）


@dataclass
class Requirement:
    id: str
    text: str
    status: str = PLANNING
    tasks: list[str] = field(default_factory=list)
    plan_raw: str = ""        # Claude 拆任务失败时的原文
    summary: str = ""
    merge: dict | None = None
    error: str = ""


def _load(cls, path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


class Store:
    def __init__(self, root: Path):
        self.root = root
        for sub in ("tasks", "requirements", "logs"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _write(self, path: Path, obj) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(obj), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _next_id(self, sub: str, prefix: str) -> str:
        nums = [int(p.stem[1:]) for p in (self.root / sub).glob(f"{prefix}*.json")]
        return f"{prefix}{max(nums, default=0) + 1:04d}"

    def _get(self, cls, sub: str, item_id: str, what: str):
        path = self.root / sub / f"{item_id}.json"
        with self._lock:
            if not path.is_file():
                raise KeyError(f"没有{what} {item_id}")
            return _load(cls, path)

    # 任务
    def new_task(self, **kw) -> Task:
        with self._lock:
            task = Task(id=self._next_id("tasks", "t"), **kw)
            self.save_task(task)
            return task

    def save_task(self, task: Task) -> None:
        with self._lock:
            self._write(self.root / "tasks" / f"{task.id}.json", task)

    def get_task(self, task_id: str) -> Task:
        return self._get(Task, "tasks", task_id, "任务")

    def tasks(self) -> list[Task]:
        with self._lock:
            return [_load(Task, p) for p in sorted((self.root / "tasks").glob("t*.json"))]

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            (self.root / "tasks" / f"{task_id}.json").unlink(missing_ok=True)
            self._log(task_id).unlink(missing_ok=True)

    # 需求
    def new_requirement(self, text: str) -> Requirement:
        with self._lock:
            req = Requirement(id=self._next_id("requirements", "r"), text=text)
            self.save_requirement(req)
            return req

    def save_requirement(self, req: Requirement) -> None:
        with self._lock:
            self._write(self.root / "requirements" / f"{req.id}.json", req)

    def get_requirement(self, req_id: str) -> Requirement:
        return self._get(Requirement, "requirements", req_id, "需求")

    def requirements(self) -> list[Requirement]:
        with self._lock:
            return [_load(Requirement, p) for p in sorted((self.root / "requirements").glob("r*.json"))]

    # 日志
    def _log(self, task_id: str) -> Path:
        return self.root / "logs" / f"{task_id}.log"

    def append_log(self, task_id: str, text: str) -> None:
        with self._lock, open(self._log(task_id), "a", encoding="utf-8") as f:
            f.write(text)

    def read_log(self, task_id: str) -> str:
        path = self._log(task_id)
        return path.read_text(encoding="utf-8") if path.is_file() else ""
