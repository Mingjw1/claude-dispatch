import sys
from pathlib import Path

import pytest

from agentdesk import gitops
from agentdesk.engine import EngineError
from helpers import status, wait_for

PY = sys.executable
FAIL_TESTS = f'"{PY}" -c "raise SystemExit(1)"'


def done_task(e, prompt):
    return status(e, e.create_task("x", prompt, "fake", "m1", "edit").id, "done")


def test_manual_merge_brings_changes_and_cleans_up(make_engine, repo):
    e = make_engine()
    t = done_task(e, "WRITE a.txt one")
    e.merge_task(t.id)
    after = e.store.get_task(t.id)
    assert after.status == "merged" and after.merge["state"] == "ok"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    assert not Path(t.worktree).exists() and gitops.git(repo, "branch", "--list", t.branch) == ""
    assert gitops.diff(repo, after.base, after.commit).count("+one") == 1  # 合并后仍能看改动


def test_merge_refused_when_main_worktree_dirty(make_engine, repo):
    e = make_engine()
    t = done_task(e, "WRITE a.txt one")
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    e.merge_task(t.id)
    t = e.store.get_task(t.id)
    assert t.status == "done" and t.merge["state"] == "refused" and "未提交" in t.error
    assert not (repo / "a.txt").exists()


def test_conflict_is_aborted_and_task_can_be_redone(make_engine, repo):
    e = make_engine()
    a = done_task(e, "WRITE README.md one")
    b = done_task(e, "WRITE README.md two")
    e.merge_task(a.id)
    e.merge_task(b.id)
    b = e.store.get_task(b.id)
    assert b.status == "done" and b.merge["state"] == "conflict" and "README.md" in b.error
    assert gitops.is_clean(repo) and (repo / "README.md").read_text(encoding="utf-8") == "one\n"
    e.retry(b.id, "WRITE README.md two", "fake", "m1", "edit", keep_changes=False)
    status(e, b.id, "done")
    e.merge_task(b.id)
    assert (repo / "README.md").read_text(encoding="utf-8") == "two\n"


def test_failed_tests_keep_worktree_and_allow_undo_once(make_engine, repo):
    e = make_engine(test_command=FAIL_TESTS)
    t = done_task(e, "WRITE a.txt one")
    pre = gitops.head(repo)
    e.merge_task(t.id)
    t = e.store.get_task(t.id)
    assert t.status == "merged" and t.merge["state"] == "test_failed" and Path(t.worktree).exists()
    e.undo_task(t.id)
    t = e.store.get_task(t.id)
    assert t.status == "done" and t.merge["state"] == "undone"
    assert gitops.head(repo) == pre and not (repo / "a.txt").exists()
    with pytest.raises(EngineError):
        e.undo_task(t.id)


def test_undo_refused_after_new_commit(make_engine, repo):
    e = make_engine(test_command=FAIL_TESTS)
    t = done_task(e, "WRITE a.txt one")
    e.merge_task(t.id)
    (repo / "later.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "-m", "later")
    with pytest.raises(EngineError, match="新提交"):
        e.undo_task(t.id)


def test_keep_after_failed_tests_cleans_up(make_engine, repo):
    e = make_engine(test_command=FAIL_TESTS)
    t = done_task(e, "WRITE a.txt one")
    e.merge_task(t.id)
    e.keep_task_merge(t.id)
    after = e.store.get_task(t.id)
    assert after.merge["state"] == "kept" and not Path(t.worktree).exists() and (repo / "a.txt").exists()


def test_requirement_tasks_cannot_be_merged_one_by_one(make_engine):
    e = make_engine()
    e.commander.plans.append({"tasks": [{"id": 1, "title": "a", "agent": "fake", "model": "m1", "level": "edit",
                                         "prompt": "WRITE a.txt one"}]})
    rid = e.submit_requirement("x").id
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_plan")
    with pytest.raises(EngineError, match="整体合并"):
        e.merge_task(e.store.get_requirement(rid).tasks[0])


def test_requirement_merge_in_dependency_order_then_undo(make_engine, repo):
    e = make_engine(test_command=FAIL_TESTS)
    e.commander.plans.append({"tasks": [
        {"id": 2, "title": "二", "agent": "fake", "model": "m1", "level": "edit", "after": [1],
         "prompt": "WRITE b.txt two"},
        {"id": 1, "title": "一", "agent": "fake", "model": "m1", "level": "edit", "prompt": "WRITE a.txt one"},
    ]})
    rid = e.submit_requirement("两步").id
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_plan")
    e.start_requirement(rid)
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_merge")
    pre = gitops.head(repo)
    e.merge_requirement(rid)
    r = e.store.get_requirement(rid)
    assert r.status == "merged" and r.merge["state"] == "test_failed"
    first, second = (e.store.get_task(i) for i in r.merge["merged"])
    assert (first.title, second.title) == ("一", "二")
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    e.undo_requirement(rid)
    r = e.store.get_requirement(rid)
    assert r.status == "confirm_merge" and gitops.head(repo) == pre
    assert all(e.store.get_task(i).status == "passed" for i in r.tasks)
