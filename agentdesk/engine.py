"""调度和流转：派活、并行与依赖、提交、验收、审查、重派、停止、合并。"""
from __future__ import annotations

import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from agentdesk import gitops
from agentdesk.commander import CommanderError
from agentdesk.config import AppConfig, ConfigError, build_command, decorate_prompt
from agentdesk.proc import Proc, RunResult, run_capture
from agentdesk.store import (DISCARDED, DONE, FAILED, INTERRUPTED, MERGED, PASSED, QUEUED, REJECTED,
                             REVIEWING, RUNNING, STOPPED, Requirement, Store, Task)

RETRYABLE = (FAILED, REJECTED, STOPPED, INTERRUPTED, DONE, PASSED)
OUTPUT_TAIL = 4000
INTERRUPTED_NOTE = "应用重启时这个任务还在运行；执行者进程可能还在后台，请在任务管理器里确认后再重派"


class EngineError(Exception):
    pass


class Engine:
    def __init__(self, repo: Path, cfg: AppConfig, store: Store, commander,
                 emit: Callable[[dict], None] = lambda event: None):
        self.repo, self.cfg, self.store, self.commander, self.emit = repo, cfg, store, commander, emit
        self._lock = threading.RLock()
        self._merge_lock = threading.Lock()
        self._procs: dict[str, Proc] = {}
        self._stop_requested: set[str] = set()
        self._wake = threading.Event()
        self._closed = False

    # ---------- 生命周期
    def start(self) -> None:
        self.recover()
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            proc.stop()

    def recover(self) -> None:
        """应用重启后：仍标着运行中的任务改为中断。不按记录的 pid 结束进程，pid 可能已被别的程序复用。"""
        for t in self.store.tasks():
            if t.status in (RUNNING, REVIEWING):
                self._set(t.id, status=INTERRUPTED, error=INTERRUPTED_NOTE, pid=None)

    # ---------- 小工具
    def _set(self, task_id: str, **changes) -> Task:
        with self._lock:
            t = self.store.get_task(task_id)
            for key, value in changes.items():
                setattr(t, key, value)
            self.store.save_task(t)
        self.emit({"type": "task", "task": asdict(t)})
        return t

    def _set_req(self, req_id: str, **changes) -> Requirement:
        with self._lock:
            r = self.store.get_requirement(req_id)
            for key, value in changes.items():
                setattr(r, key, value)
            self.store.save_requirement(r)
        self.emit({"type": "requirement", "requirement": asdict(r)})
        return r

    def _check(self, agent: str, level: str, model: str) -> None:
        a = self.cfg.agents.get(agent)
        if a is None:
            raise EngineError(f"没有执行者 {agent}")
        try:
            build_command(a, level, model)
        except ConfigError as e:
            raise EngineError(str(e)) from e

    def _timeout(self) -> float:
        return self.cfg.task_timeout_minutes * 60

    def _line(self, task_id: str, line: str) -> None:
        self.store.append_log(task_id, line + "\n")
        self.emit({"type": "log", "task": task_id, "line": line})

    def _fail(self, task_id: str, message: str) -> None:
        self._set(task_id, status=FAILED, error=message, pid=None)
        self._wake.set()

    def _cleanup(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        gitops.remove_worktree(self.repo, Path(t.worktree) if t.worktree else None, t.branch)
        self._set(task_id, worktree="", branch="")

    def _after_verdict(self, task: Task) -> None:
        self._wake.set()

    # ---------- 手动任务
    def create_task(self, title: str, prompt: str, agent: str, model: str, level: str, accept: str = "") -> Task:
        if not prompt.strip():
            raise EngineError("任务说明不能为空")
        self._check(agent, level, model)
        title = title.strip() or prompt.strip().splitlines()[0][:40]
        t = self.store.new_task(title=title, prompt=prompt, agent=agent, model=model, level=level,
                                accept=accept.strip(), status=QUEUED)
        self.emit({"type": "task", "task": asdict(t)})
        self._wake.set()
        return t

    def stop(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.status == QUEUED:
            self._set(task_id, status=STOPPED)
            return
        if t.status != RUNNING:
            raise EngineError("任务不在运行")
        with self._lock:
            proc = self._procs.get(task_id)
            if proc is None:
                self._stop_requested.add(task_id)  # 还在准备 worktree，启动后立刻停
        if proc is not None:
            proc.stop()

    def discard(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.status in (RUNNING, REVIEWING):
            raise EngineError("任务还在进行，先停止")
        if t.status == MERGED:
            raise EngineError("已合并的任务不能丢弃")
        self._cleanup(task_id)
        self._after_verdict(self._set(task_id, status=DISCARDED))

    def retry(self, task_id: str, prompt: str, agent: str, model: str, level: str, keep_changes: bool) -> None:
        t = self.store.get_task(task_id)
        if t.status not in RETRYABLE:
            raise EngineError("这个状态的任务不能重派")
        if not prompt.strip():
            raise EngineError("任务说明不能为空")
        self._check(agent, level, model)
        wt = Path(t.worktree) if t.worktree else None
        if keep_changes and wt is not None and wt.is_dir():
            stat = gitops.diff_stat(self.repo, t.base, t.commit) if t.commit else ""
            prompt = f"上一轮的改动已经在工作区里，在此基础上继续。改动统计：\n{stat or '（上一轮没有改动）'}\n\n{prompt}"
        else:
            self._cleanup(task_id)
            self._set(task_id, base="", commit="")
        self._set(task_id, prompt=prompt, agent=agent, model=model, level=level, status=QUEUED, review=None,
                  error="", exit_code=None, accept_code=None, accept_output="")
        self._wake.set()

    # ---------- 调度
    def _loop(self) -> None:
        while not self._closed:
            self._wake.wait(1.0)
            self._wake.clear()
            try:
                self._schedule()
            except Exception as e:  # 调度线程不能退出
                self.emit({"type": "error", "message": f"调度出错：{e}"})

    def _schedule(self) -> None:
        with self._lock:
            tasks = self.store.tasks()
            by_id = {t.id: t for t in tasks}
            running = sum(t.status == RUNNING for t in tasks)
            for t in tasks:
                if self._closed or running >= self.cfg.max_parallel:
                    break
                if t.status != QUEUED:
                    continue
                deps = [by_id.get(d) for d in t.after]
                if any(d is None or d.status not in (PASSED, MERGED) for d in deps):
                    continue
                self._set(t.id, status=RUNNING, error="", attempts=t.attempts + 1)
                running += 1
                threading.Thread(target=self._run, args=(t.id,), daemon=True).start()

    def _prepare_worktree(self, t: Task) -> Task:
        if t.worktree and Path(t.worktree).is_dir():
            return t  # 在已有改动上继续
        deps = [self.store.get_task(d) for d in t.after]
        unmerged = [d for d in deps if d.status == PASSED and d.commit]
        start = unmerged[0].commit if unmerged else gitops.head(self.repo)
        wt, branch = gitops.create_worktree(self.repo, t.id, start)
        for d in unmerged[1:]:
            r = gitops.merge(wt, d.commit, f"agentdesk: 带上前置任务 {d.id}")
            if not r.ok:
                raise EngineError(f"合并前置任务 {d.id} 时冲突：{'、'.join(r.conflicts)}")
        for cmd in self.cfg.worktree_setup:
            code, out = run_capture(cmd, wt, 300)
            if code != 0:
                raise EngineError(f"worktree 准备命令失败：{cmd}\n{out[-1000:]}")
        return self._set(t.id, worktree=str(wt), branch=branch, base=gitops.head(wt), commit="")

    def _run(self, task_id: str) -> None:
        try:
            t = self._prepare_worktree(self.store.get_task(task_id))
            agent = self.cfg.agents[t.agent]
            argv = build_command(agent, t.level, t.model)
            self.store.append_log(task_id, f"===== 第 {t.attempts} 次：{agent.label} / {t.model} / {t.level} =====\n")
            proc = Proc(argv, Path(t.worktree), decorate_prompt(t.level, t.prompt),
                        lambda line: self._line(task_id, line))
        except (ConfigError, EngineError, gitops.GitError, OSError, KeyError) as e:
            self._fail(task_id, str(e.args[0]) if isinstance(e, KeyError) else str(e))
            return
        with self._lock:
            self._procs[task_id] = proc
            stop_now = task_id in self._stop_requested
            self._stop_requested.discard(task_id)
        self._set(task_id, pid=proc.pid)
        if stop_now:
            proc.stop()
        result = proc.wait(self._timeout())
        with self._lock:
            self._procs.pop(task_id, None)
        try:
            self._after_run(task_id, result)
        except (gitops.GitError, OSError) as e:
            self._fail(task_id, f"收尾出错：{e}")

    def _after_run(self, task_id: str, result: RunResult) -> None:
        t = self.store.get_task(task_id)
        wt = Path(t.worktree)
        commit = gitops.commit_all(wt, f"agentdesk {t.id}: {t.title}") or gitops.head(wt)
        self._set(task_id, commit=commit, pid=None, exit_code=result.code)
        if result.stopped:
            self._set(task_id, status=STOPPED)
            self._wake.set()
            return
        if result.timed_out:
            self._fail(task_id, f"超过 {self.cfg.task_timeout_minutes:g} 分钟没有结束，已结束执行者进程")
            return
        if result.code != 0:
            self._fail(task_id, f"执行者退出码 {result.code}，详见实时输出")
            return
        if t.accept:
            code, out = run_capture(t.accept, wt, self._timeout())
            self._set(task_id, accept_code=code, accept_output=out[-OUTPUT_TAIL:])
        self._set(task_id, status=DONE)
        if t.requirement:
            self._review(task_id)
        else:
            self._wake.set()

    # ---------- 审查
    def review_task(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.status not in (DONE, REJECTED):
            raise EngineError("任务还没完成，不能审查")
        threading.Thread(target=self._review, args=(task_id,), daemon=True).start()

    def _review(self, task_id: str) -> None:
        t = self._set(task_id, status=REVIEWING, error="")
        try:
            changed = gitops.changed_files(self.repo, t.base, t.commit)
            outside = [f for f in changed if t.files and f not in t.files]
            verdict = self.commander.review(t, gitops.diff(self.repo, t.base, t.commit), outside)
        except (CommanderError, gitops.GitError) as e:
            t = self._set(task_id, status=DONE,
                          error=f"Claude 审查失败：{e}。请自己看改动后点“我确认合格”或重派")
        else:
            t = self._set(task_id, status=PASSED if verdict["verdict"] == "pass" else REJECTED, review=verdict)
        self._after_verdict(t)

    def approve(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.status not in (DONE, REJECTED):
            raise EngineError("只有待审查或不合格的任务可以确认合格")
        t = self._set(task_id, status=PASSED, error="",
                      review={"verdict": "pass", "reasons": ["用户确认合格"], "retry_prompt": ""})
        self._after_verdict(t)
