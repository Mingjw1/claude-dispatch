import sys

import pytest

from agentdesk import gitops, merger
from agentdesk.store import Task

PY = sys.executable


def task(repo, tid, files, after=()):
    base = gitops.head(repo)
    wt, branch = gitops.create_worktree(repo, tid, base)
    for name, text in files.items():
        (wt / name).write_text(text, encoding="utf-8")
    commit = gitops.commit_all(wt, tid) or base
    return Task(id=tid, title=f"任务{tid}", prompt="p", agent="fake", model="m1", level="edit",
                after=list(after), base=base, branch=branch, worktree=str(wt), commit=commit)


def test_order_puts_dependencies_first():
    a = Task("t1", "a", "p", "fake", "m1", "edit", after=["t2"])
    b = Task("t2", "b", "p", "fake", "m1", "edit")
    c = Task("t3", "c", "p", "fake", "m1", "edit", after=["t9"])  # 依赖不在列表里时忽略
    assert [t.id for t in merger.order([a, b, c])] == ["t2", "t1", "t3"]


def test_merge_and_run_tests(repo):
    t1 = task(repo, "t1", {"a.txt": "a\n"})
    t2 = task(repo, "t2", {"b.txt": "b\n"})
    noop = task(repo, "t3", {})
    pre = gitops.head(repo)
    out = merger.merge_tasks(repo, [t1, t2, noop], f'"{PY}" -c "print(\'全量通过\')"', 60)
    assert out["state"] == "ok" and out["merged"] == ["t1", "t2", "t3"] and out["pre"] == pre
    assert out["post"] == gitops.head(repo) and out["test_code"] == 0 and "全量通过" in out["test_output"]
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    assert gitops.git(repo, "log", "-1", "--format=%s") == "agentdesk: 任务t2（t2）"


def test_refuses_when_main_worktree_dirty(repo):
    t1 = task(repo, "t1", {"a.txt": "a\n"})
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    out = merger.merge_tasks(repo, [t1], "", 60)
    assert out["state"] == "refused" and "未提交" in out["message"] and out["merged"] == []
    assert not (repo / "a.txt").exists()


def test_conflict_stops_and_leaves_repo_clean(repo):
    t1 = task(repo, "t1", {"README.md": "one\n"})
    t2 = task(repo, "t2", {"README.md": "two\n"})
    out = merger.merge_tasks(repo, [t1, t2], "", 60)
    assert out["state"] == "conflict" and out["merged"] == ["t1"]
    assert out["conflict_task"] == "t2" and out["conflicts"] == ["README.md"]
    assert gitops.is_clean(repo) and (repo / "README.md").read_text(encoding="utf-8") == "one\n"


def test_failed_tests_then_undo(repo):
    t1 = task(repo, "t1", {"a.txt": "a\n"})
    out = merger.merge_tasks(repo, [t1], f'"{PY}" -c "raise SystemExit(1)"', 60)
    assert out["state"] == "test_failed" and out["test_code"] == 1
    merger.undo(repo, out)
    assert gitops.head(repo) == out["pre"] and not (repo / "a.txt").exists()


def test_undo_refused_after_new_commit(repo):
    t1 = task(repo, "t1", {"a.txt": "a\n"})
    out = merger.merge_tasks(repo, [t1], f'"{PY}" -c "raise SystemExit(1)"', 60)
    (repo / "later.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "-m", "later")
    with pytest.raises(merger.MergeError, match="新提交"):
        merger.undo(repo, out)
