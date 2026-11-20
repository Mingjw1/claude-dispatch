"""最终审查发现的问题：每个测试先复现问题，再验证修复。"""
import sys
import threading

import pytest

from agentdesk import gitops, merger
from agentdesk.engine import Engine, EngineError
from agentdesk.store import Store
from helpers import FakeCommander, fake_config, status, wait_for

PY = sys.executable
SLOW_FAIL = f'"{PY}" -c "import time; time.sleep(2); raise SystemExit(1)"'


def test_concurrent_merge_clicks_merge_only_once(make_engine, repo):
    e = make_engine(test_command=SLOW_FAIL)
    t = status(e, e.create_task("x", "WRITE a.txt one", "fake", "m1", "edit").id, "done")
    pre = gitops.head(repo)
    errors = []

    def click():
        try:
            e.merge_task(t.id)
        except EngineError as err:
            errors.append(str(err))

    threads = [threading.Thread(target=click) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert len(errors) == 1
    t = e.store.get_task(t.id)
    assert t.merge["state"] == "test_failed" and t.merge["pre"] == pre
    e.undo_task(t.id)
    assert gitops.head(repo) == pre


def test_stop_during_acceptance_stops_and_next_retry_runs(make_engine):
    e = make_engine()
    t = e.create_task("x", "PRINT hi", "fake", "m1", "edit", f'"{PY}" -c "import time; time.sleep(30)"')
    wait_for(lambda: (x := e.store.get_task(t.id)).status == "running" and x.exit_code == 0)
    e.stop(t.id)
    status(e, t.id, "stopped", timeout=15)
    e.retry(t.id, "PRINT again", "fake", "m1", "edit", keep_changes=True)
    wait_for(lambda: "> PRINT again" in e.store.read_log(t.id),
             timeout=15)   # 第二轮真的跑起来了，没被上一次残留的停止请求立刻停掉
    assert e.store.get_task(t.id).status == "running"


def test_discarded_predecessor_unblocks_dependent(make_engine):
    e = make_engine()
    e.commander.reviews.append({"verdict": "fail", "reasons": ["不对"], "retry_prompt": "x"})
    e.commander.plans.append({"tasks": [
        {"id": 1, "title": "一", "agent": "fake", "model": "m1", "level": "edit", "prompt": "WRITE a.txt one"},
        {"id": 2, "title": "二", "agent": "fake", "model": "m1", "level": "edit", "after": [1],
         "prompt": "WRITE b.txt two"}]})
    rid = e.submit_requirement("两步").id
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_plan")
    first, second = e.store.get_requirement(rid).tasks
    e.start_requirement(rid)
    status(e, first, "rejected")
    e.discard(first)
    status(e, second, "passed")
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_merge")


def test_interrupted_review_comes_back_as_done_and_can_be_approved(repo):
    store = Store(gitops.store_dir(repo))
    t = store.new_task(title="x", prompt="p", agent="fake", model="m1", level="edit", status="reviewing")
    e = Engine(repo, fake_config(), store, FakeCommander())
    e.recover()
    t = store.get_task(t.id)
    assert t.status == "done" and "审查" in t.error
    e.approve(t.id)
    assert store.get_task(t.id).status == "passed"


def test_unexpected_review_error_does_not_leave_task_reviewing(make_engine):
    e = make_engine()
    e.commander.reviews.append(RuntimeError("意外"))
    t = status(e, e.create_task("x", "WRITE a.txt x", "fake", "m1", "edit").id, "done")
    e.review_task(t.id)
    wait_for(lambda: (x := e.store.get_task(t.id)).status == "done" and "意外" in x.error)


def test_unexpected_plan_error_reopens_requirement(make_engine):
    e = make_engine()
    e.commander.plans.append(RuntimeError("意外"))
    rid = e.submit_requirement("x").id
    r = wait_for(lambda: (x := e.store.get_requirement(rid)).status == "confirm_plan" and x)
    assert "意外" in r.error


def test_plan_with_wrong_types_is_a_format_error():
    from agentdesk.commander import validate_plan
    with pytest.raises(ValueError):
        validate_plan({"tasks": [{"id": 1, "title": "a", "prompt": "p", "after": 2}]}, fake_config())


def test_merge_blocked_by_untracked_file_is_not_called_a_conflict(repo):
    base = gitops.head(repo)
    wt, branch = gitops.create_worktree(repo, "t1", base)
    (wt / "new.txt").write_text("from task\n", encoding="utf-8")
    commit = gitops.commit_all(wt, "t1")
    (repo / "new.txt").write_text("mine\n", encoding="utf-8")   # 未跟踪，合并会覆盖它
    from agentdesk.store import Task
    t = Task(id="t1", title="t", prompt="p", agent="fake", model="m1", level="edit", base=base, branch=branch,
             worktree=str(wt), commit=commit)
    out = merger.merge_tasks(repo, [t], "", 60)
    assert out["state"] == "failed" and out["conflicts"] == [] and "new.txt" in out["message"]
    assert (repo / "new.txt").read_text(encoding="utf-8") == "mine\n"


def test_partial_merge_conflict_message_says_what_was_merged(make_engine, repo):
    e = make_engine()
    e.commander.plans.append({"tasks": [
        {"id": 1, "title": "一", "agent": "fake", "model": "m1", "level": "edit", "prompt": "WRITE a.txt one"},
        {"id": 2, "title": "二", "agent": "fake", "model": "m1", "level": "edit", "prompt": "WRITE README.md two"}]})
    rid = e.submit_requirement("x").id
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_plan")
    e.start_requirement(rid)
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_merge")
    (repo / "README.md").write_text("main changed\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "main changed")
    e.merge_requirement(rid)
    r = e.store.get_requirement(rid)
    assert r.merge["state"] == "conflict" and "已合并" in r.error and "恢复原状" not in r.error
