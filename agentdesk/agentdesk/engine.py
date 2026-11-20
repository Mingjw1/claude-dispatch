"""调度和流转：派活、并行与依赖、提交、验收、审查、重派、停止、合并。"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from agentdesk import gitops, merger
from agentdesk.commander import CommanderError, check_acyclic
from agentdesk.config import AppConfig, ConfigError, build_command, decorate_prompt
from agentdesk.proc import Proc, RunResult, run_capture
from agentdesk.store import (CONFIRM_MERGE, CONFIRM_PLAN, DISCARDED, DONE, DRAFT, FAILED, IN_PROGRESS,
                             INTERRUPTED, MERGED, PASSED, PLANNING, QUEUED, REJECTED, REQ_MERGED, REVIEWING, RUNNING,
                             STOPPED, Requirement, Store, Task)

RETRYABLE = (FAILED, REJECTED, STOPPED, INTERRUPTED, DONE, PASSED)
DRAFT_FIELDS = {"title", "prompt", "agent", "model", "level", "accept", "files", "after"}
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
            if t.status == RUNNING:
                self._set(t.id, status=INTERRUPTED, error=INTERRUPTED_NOTE, pid=None)
            elif t.status == REVIEWING:  # 执行者早已跑完、改动已提交，只差审查
                self._set(t.id, status=DONE, error="应用重启时正在审查，请重新交给 Claude 审查或自己确认合格")
        for r in self.store.requirements():
            if r.status == PLANNING:
                self._set_req(r.id, status=CONFIRM_PLAN,
                              error="应用重启时 Claude 还在拆任务，请重新提交需求或手动添加任务")

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
        with self._lock:
            self._stop_requested.discard(task_id)
        self._set(task_id, status=FAILED, error=message, pid=None)
        self._wake.set()

    def _cleanup(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        gitops.remove_worktree(self.repo, Path(t.worktree) if t.worktree else None, t.branch)
        self._set(task_id, worktree="", branch="")

    def _after_verdict(self, task: Task) -> None:
        if task.requirement:
            self._check_requirement(task.requirement)
        self._wake.set()

    def _check_requirement(self, req_id: str) -> None:
        """需求里的任务都合格（或丢弃、已合并）且至少一个合格时，写总结并进入合并确认。"""
        with self._lock:
            r = self.store.get_requirement(req_id)
            tasks = [self.store.get_task(i) for i in r.tasks]
            if r.status != IN_PROGRESS:
                return
            if not all(t.status in (PASSED, DISCARDED, MERGED) for t in tasks):
                return
            passed = [t for t in tasks if t.status == PASSED]
            if not passed:
                return
            self._set_req(req_id, status=CONFIRM_MERGE, summary="Claude 正在写总结…")
        items = []
        for t in passed:
            label = self.cfg.agents[t.agent].label if t.agent in self.cfg.agents else t.agent
            reasons = "；".join((t.review or {}).get("reasons", []))
            items.append(f"- {t.title}（{label} / {t.model}）：{reasons}\n{gitops.diff_stat(self.repo, t.base, t.commit)}")
        try:
            summary = self.commander.summarize(items)
        except CommanderError as e:
            summary = f"Claude 写总结失败：{e}"
        self._set_req(req_id, summary=summary)

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
        with self._lock:
            self._stop_requested.discard(task_id)
        wt = Path(t.worktree) if t.worktree else None
        if keep_changes and wt is not None and wt.is_dir():
            stat = gitops.diff_stat(self.repo, t.base, t.commit) if t.commit else ""
            prompt = f"上一轮的改动已经在工作区里，在此基础上继续。改动统计：\n{stat or '（上一轮没有改动）'}\n\n{prompt}"
        else:
            self._cleanup(task_id)
            self._set(task_id, base="", commit="")
        self._set(task_id, prompt=prompt, agent=agent, model=model, level=level, status=QUEUED, review=None,
                  error="", exit_code=None, accept_code=None, accept_output="")
        if t.requirement and self.store.get_requirement(t.requirement).status == CONFIRM_MERGE:
            self._set_req(t.requirement, status=IN_PROGRESS)
        self._wake.set()

    # ---------- 需求（Claude 指挥）
    def submit_requirement(self, text: str) -> Requirement:
        if not text.strip():
            raise EngineError("需求不能为空")
        r = self.store.new_requirement(text.strip())
        self.emit({"type": "requirement", "requirement": asdict(r)})
        threading.Thread(target=self._plan, args=(r.id,), daemon=True).start()
        return r

    def _plan(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        try:
            planned = self.commander.plan(r.text, self.cfg)
        except Exception as e:  # 线程顶层：不能停在“拆任务中”
            self._set_req(req_id, status=CONFIRM_PLAN, plan_raw=getattr(e, "raw", ""),
                          error=f"Claude 拆任务失败：{e}。可以手动添加任务。")
            return
        id_map = {}
        for p in planned:
            t = self.store.new_task(title=p["title"], prompt=p["prompt"], agent=p["agent"], model=p["model"],
                                    level=p["level"], accept=p["accept"], files=p["files"],
                                    requirement=req_id, problems=p["problems"])
            id_map[p["id"]] = t.id
        for p in planned:
            self._set(id_map[p["id"]], after=[id_map[a] for a in p["after"]])
        self._set_req(req_id, status=CONFIRM_PLAN, tasks=list(id_map.values()), error="")

    def _draft(self, task_id: str) -> Task:
        t = self.store.get_task(task_id)
        if t.status != DRAFT:
            raise EngineError("只能修改还没开始的任务")
        return t

    def add_draft(self, req_id: str, title: str, prompt: str, agent: str, model: str, level: str,
                  accept: str = "", files=(), after=()) -> Task:
        r = self.store.get_requirement(req_id)
        if r.status != CONFIRM_PLAN:
            raise EngineError("只能在确认拆分时添加任务")
        title = title.strip() or (prompt.strip().splitlines() or ["新任务"])[0][:40]
        t = self.store.new_task(title=title, prompt=prompt, agent=agent, model=model, level=level,
                                accept=accept.strip(), files=list(files), after=list(after), requirement=req_id)
        self.emit({"type": "task", "task": asdict(t)})
        self._set_req(req_id, tasks=[*r.tasks, t.id])
        return t

    def update_draft(self, task_id: str, **fields) -> Task:
        bad = set(fields) - DRAFT_FIELDS
        if bad:
            raise EngineError(f"不能修改 {'、'.join(sorted(bad))}")
        self._draft(task_id)
        return self._set(task_id, problems=[], **fields)

    def delete_draft(self, task_id: str) -> None:
        t = self._draft(task_id)
        if t.requirement:
            r = self.store.get_requirement(t.requirement)
            for other in r.tasks:
                if other != task_id and task_id in self.store.get_task(other).after:
                    self._set(other, after=[a for a in self.store.get_task(other).after if a != task_id])
            self._set_req(r.id, tasks=[i for i in r.tasks if i != task_id])
        self.store.delete_task(task_id)
        self.emit({"type": "deleted", "task": task_id})

    def start_requirement(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        if r.status != CONFIRM_PLAN:
            raise EngineError("这个需求不在等待确认拆分")
        tasks = [self.store.get_task(i) for i in r.tasks]
        if not tasks:
            raise EngineError("还没有任务")
        errors = []
        for t in tasks:
            if not t.prompt.strip():
                errors.append(f"{t.title}：任务说明为空")
            try:
                self._check(t.agent, t.level, t.model)
            except EngineError as e:
                errors.append(f"{t.title}：{e}")
            unknown = [a for a in t.after if a not in r.tasks]
            if unknown:
                errors.append(f"{t.title}：依赖的任务不存在 {'、'.join(unknown)}")
        try:
            check_acyclic({t.id: t.after for t in tasks})
        except ValueError as e:
            errors.append(str(e))
        if errors:
            raise EngineError("；".join(errors))
        for t in tasks:
            self._set(t.id, status=QUEUED, problems=[])
        self._set_req(req_id, status=IN_PROGRESS, error="")
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
                if any(d is None or d.status not in (PASSED, MERGED, DISCARDED) for d in deps):
                    continue
                self._set(t.id, status=RUNNING, error="", attempts=t.attempts + 1, started=time.time())
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
        except Exception as e:  # 线程顶层：任何错误都要让任务离开“运行中”
            self._fail(task_id, str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e))
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
        except Exception as e:  # 线程顶层
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
        if t.accept and self._accept(task_id, t.accept, wt).stopped:
            self._set(task_id, status=STOPPED)
            self._wake.set()
            return
        self._set(task_id, status=DONE)
        if t.requirement:
            self._review(task_id)
        else:
            self._wake.set()

    def _accept(self, task_id: str, command: str, wt: Path) -> RunResult:
        """执行验收命令；登记进程，让“停止”在验收阶段也有效。"""
        lines: list[str] = []
        with self._lock:
            proc = Proc(command, wt, None, lines.append, shell=True)
            self._procs[task_id] = proc
            stop_now = task_id in self._stop_requested
            self._stop_requested.discard(task_id)
        if stop_now:
            proc.stop()
        result = proc.wait(self._timeout())
        with self._lock:
            self._procs.pop(task_id, None)
        out = "\n".join(lines) + ("\n[超时，已结束]" if result.timed_out else "")
        self._set(task_id, accept_code=result.code, accept_output=out[-OUTPUT_TAIL:])
        return result

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
        except Exception as e:  # 线程顶层：不能停在“审查中”
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

    # ---------- 合并
    def merge_task(self, task_id: str) -> None:
        with self._merge_lock:  # 状态检查也在锁里：连点两次合并只合一次
            t = self.store.get_task(task_id)
            if t.requirement:
                raise EngineError("需求里的任务要在需求页整体合并")
            if t.status not in (DONE, PASSED):
                raise EngineError("只有待审查或合格的任务可以合并")
            outcome = merger.merge_tasks(self.repo, [t], self.cfg.test_command, self._timeout())
            self._apply_merge([t], outcome)
        extra = {"error": outcome["message"]} if outcome["state"] == "refused" else {}
        self._set(task_id, merge=outcome, **extra)

    def merge_requirement(self, req_id: str) -> None:
        with self._merge_lock:  # 状态检查也在锁里：连点两次合并只合一次
            r = self.store.get_requirement(req_id)
            if r.status != CONFIRM_MERGE:
                raise EngineError("这个需求还没到合并这一步")
            tasks = merger.order([t for t in map(self.store.get_task, r.tasks) if t.status == PASSED])
            outcome = merger.merge_tasks(self.repo, tasks, self.cfg.test_command, self._timeout())
            self._apply_merge(tasks, outcome)
        if outcome["state"] in ("ok", "test_failed"):
            self._set_req(req_id, status=REQ_MERGED, merge=outcome, error="")
        elif outcome["state"] == "refused":
            self._set_req(req_id, merge=outcome, error=outcome["message"])
        else:
            self._set_req(req_id, merge=outcome, error=self._merge_stop_message(outcome))

    @staticmethod
    def _merge_stop_message(outcome: dict) -> str:
        done = "、".join(outcome["merged"])
        head = f"{done} 已合并进当前分支；" if done else ""
        if outcome["state"] == "conflict":
            return (f"{head}任务 {outcome['conflict_task']} 合并冲突（{'、'.join(outcome['conflicts'])}），"
                    "这一个已撤回；可以选“从头重做”在最新代码上重派")
        return f"{head}任务 {outcome['conflict_task']} 没能合并：{outcome['message'][-500:]}"

    def _apply_merge(self, tasks: list[Task], outcome: dict) -> None:
        for t in tasks:
            if t.id in outcome["merged"]:
                self._set(t.id, status=MERGED, error="")
                if outcome["state"] != "test_failed":
                    self._cleanup(t.id)
            elif t.id == outcome.get("conflict_task"):
                self._set(t.id, error=self._merge_stop_message(outcome))

    def _undo(self, merge: dict | None, task_ids: list[str], back_to: str) -> None:
        if not merge or merge.get("state") != "test_failed":
            raise EngineError("只有全量测试失败的合并可以撤销")
        try:
            merger.undo(self.repo, merge)
        except merger.MergeError as e:
            raise EngineError(str(e)) from e
        for task_id in task_ids:
            self._set(task_id, status=back_to)

    def _keep(self, merge: dict | None, task_ids: list[str]) -> None:
        if not merge or merge.get("state") != "test_failed":
            raise EngineError("没有等待处理的合并")
        for task_id in task_ids:
            self._cleanup(task_id)

    def undo_task(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        self._undo(t.merge, [task_id], DONE)
        self._set(task_id, merge={**t.merge, "state": "undone"})

    def keep_task_merge(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        self._keep(t.merge, [task_id])
        self._set(task_id, merge={**t.merge, "state": "kept"})

    def undo_requirement(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        self._undo(r.merge, (r.merge or {}).get("merged", []), PASSED)
        self._set_req(req_id, status=CONFIRM_MERGE, merge={**r.merge, "state": "undone"})

    def keep_requirement_merge(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        self._keep(r.merge, (r.merge or {}).get("merged", []))
        self._set_req(req_id, merge={**r.merge, "state": "kept"})
