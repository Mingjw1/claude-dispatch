"""把任务分支依次合并进当前分支，跑全量测试；测试失败时可以撤销。"""
from __future__ import annotations

from pathlib import Path

from agentdesk import gitops
from agentdesk.proc import run_capture
from agentdesk.store import Task

OUTPUT_TAIL = 4000


class MergeError(Exception):
    pass


def order(tasks: list[Task]) -> list[Task]:
    by_id = {t.id: t for t in tasks}
    out: list[Task] = []
    seen: set[str] = set()

    def visit(t: Task) -> None:
        if t.id in seen:
            return
        seen.add(t.id)
        for dep in t.after:
            if dep in by_id:
                visit(by_id[dep])
        out.append(t)

    for t in tasks:
        visit(t)
    return out


def merge_tasks(repo: Path, tasks: list[Task], test_command: str, timeout_s: float) -> dict:
    if not gitops.is_clean(repo):
        return {"state": "refused", "message": "主工作区有未提交的改动，请先提交或清理后再合并", "merged": []}
    pre = gitops.head(repo)
    merged: list[str] = []
    for t in tasks:
        if t.commit and t.commit != t.base:
            r = gitops.merge(repo, t.branch, f"agentdesk: {t.title}（{t.id}）")
            if not r.ok:  # 没有冲突文件时是别的原因（例如会覆盖未跟踪的文件）
                return {"state": "conflict" if r.conflicts else "failed", "message": r.message[-2000:],
                        "merged": merged, "pre": pre, "post": gitops.head(repo), "conflict_task": t.id,
                        "conflicts": r.conflicts}
        merged.append(t.id)
    out = {"state": "ok", "message": "", "merged": merged, "pre": pre, "post": gitops.head(repo),
           "test_code": None, "test_output": ""}
    if test_command:
        code, text = run_capture(test_command, repo, timeout_s)
        out.update(test_code=code, test_output=text[-OUTPUT_TAIL:])
        if code != 0:
            out.update(state="test_failed", message="全量测试没有通过")
    return out


def undo(repo: Path, merge: dict) -> None:
    if gitops.head(repo) != merge["post"]:
        raise MergeError("合并之后当前分支又有了新提交，不能自动撤销，请自己处理")
    if not gitops.is_clean(repo):
        raise MergeError("主工作区有未提交的改动，不能撤销")
    gitops.reset_hard(repo, merge["pre"])
