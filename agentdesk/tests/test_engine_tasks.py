import sys
from pathlib import Path

import pytest

from agentdesk import gitops
from agentdesk.engine import Engine, EngineError
from agentdesk.store import Store
from helpers import FakeCommander, fake_config, status, wait_for

PY = sys.executable


def test_manual_task_runs_in_its_own_worktree_and_commits(make_engine, repo):
    e = make_engine()
    accept = f'"{PY}" -c "import pathlib; print(pathlib.Path(\'中文.txt\').read_text(encoding=\'utf-8\'))"'
    t = status(e, e.create_task("写文件", "WRITE 中文.txt 你好", "fake", "m1", "edit", accept).id, "done")
    assert Path(t.worktree).parent == repo.parent / "仓库.agentdesk-wt"
    assert t.commit != t.base and gitops.changed_files(repo, t.base, t.commit) == ["中文.txt"]
    assert t.accept_code == 0 and "你好" in t.accept_output and t.attempts == 1
    assert not (repo / "中文.txt").exists()
    log = e.store.read_log(t.id)
    assert "> WRITE 中文.txt 你好" in log and "不要 git commit" in log and "'--model', 'm1'" in log


def test_read_level_prompt_says_read_only_and_no_changes_is_fine(make_engine):
    e = make_engine()
    t = status(e, e.create_task("看", "PRINT hi", "fake", "m1", "read").id, "done")
    assert "> 只读任务" in e.store.read_log(t.id) and t.commit == t.base


def test_nonzero_exit_fails_task(make_engine):
    e = make_engine()
    t = status(e, e.create_task("x", "EXIT 2", "fake", "m1", "edit").id, "failed")
    assert "退出码 2" in t.error and t.exit_code == 2


def test_timeout_fails_task(make_engine):
    e = make_engine(task_timeout_minutes=0.02)
    t = status(e, e.create_task("x", "SLEEP 30", "fake", "m1", "edit").id, "failed")
    assert "分钟没有结束" in t.error


def test_stop_running_task(make_engine):
    e = make_engine()
    t = e.create_task("x", "SLEEP 30", "fake", "m1", "edit")
    wait_for(lambda: e.store.get_task(t.id).pid)
    e.stop(t.id)
    assert status(e, t.id, "stopped")


def test_parallel_limit(make_engine):
    e = make_engine(max_parallel=2)
    ids = [e.create_task(f"x{i}", "SLEEP 2", "fake", "m1", "edit").id for i in range(3)]
    wait_for(lambda: sum(e.store.get_task(i).status == "running" for i in ids) == 2)
    assert sum(e.store.get_task(i).status == "queued" for i in ids) == 1
    for i in ids:
        status(e, i, "done")


def test_retry_keeping_changes_builds_on_previous_round(make_engine, repo):
    e = make_engine()
    t = e.create_task("x", "WRITE a.txt one\nEXIT 1", "fake", "m1", "edit")
    status(e, t.id, "failed")
    e.retry(t.id, "WRITE b.txt two", "fake", "m2", "edit", keep_changes=True)
    t = status(e, t.id, "done")
    assert gitops.changed_files(repo, t.base, t.commit) == ["a.txt", "b.txt"]
    assert t.prompt.startswith("上一轮的改动已经在工作区里") and t.model == "m2" and t.attempts == 2


def test_retry_from_scratch_drops_previous_round(make_engine, repo):
    e = make_engine()
    t = e.create_task("x", "WRITE a.txt one\nEXIT 1", "fake", "m1", "edit")
    status(e, t.id, "failed")
    e.retry(t.id, "WRITE b.txt two", "fake", "m1", "edit", keep_changes=False)
    t = status(e, t.id, "done")
    assert gitops.changed_files(repo, t.base, t.commit) == ["b.txt"]


def test_retry_rejects_running_task(make_engine):
    e = make_engine()
    t = e.create_task("x", "SLEEP 30", "fake", "m1", "edit")
    status(e, t.id, "running")
    with pytest.raises(EngineError, match="不能重派"):
        e.retry(t.id, "p", "fake", "m1", "edit", keep_changes=False)


def test_discard_removes_worktree_and_branch(make_engine, repo):
    e = make_engine()
    t = status(e, e.create_task("x", "WRITE a.txt x", "fake", "m1", "edit").id, "done")
    e.discard(t.id)
    assert not Path(t.worktree).exists() and gitops.git(repo, "branch", "--list", t.branch) == ""
    assert e.store.get_task(t.id).status == "discarded"


def test_create_task_validates_agent_level_and_prompt(make_engine):
    e = make_engine()
    with pytest.raises(EngineError, match="不支持"):
        e.create_task("x", "p", "readonly", "m1", "edit")
    with pytest.raises(EngineError, match="没有执行者"):
        e.create_task("x", "p", "nope", "m1", "edit")
    with pytest.raises(EngineError, match="不能为空"):
        e.create_task("x", "  ", "fake", "m1", "edit")


def test_worktree_setup_commands_run_before_agent(make_engine):
    e = make_engine(worktree_setup=["mkdir runs"])
    t = status(e, e.create_task("x", "PRINT hi", "fake", "m1", "edit").id, "done")
    assert (Path(t.worktree) / "runs").is_dir()


def test_failed_worktree_setup_fails_task(make_engine):
    e = make_engine(worktree_setup=[f'"{PY}" -c "raise SystemExit(3)"'])
    t = status(e, e.create_task("x", "PRINT hi", "fake", "m1", "edit").id, "failed")
    assert "准备命令失败" in t.error


def test_restart_marks_running_tasks_interrupted_without_killing(repo):
    store = Store(gitops.store_dir(repo))
    t = store.new_task(title="x", prompt="p", agent="fake", model="m1", level="edit", status="running",
                       pid=99999999)
    Engine(repo, fake_config(), store, FakeCommander()).recover()
    t = store.get_task(t.id)
    assert t.status == "interrupted" and t.pid is None and "任务管理器" in t.error


def test_manual_review_then_approve(make_engine):
    e = make_engine()
    e.commander.reviews.append({"verdict": "fail", "reasons": ["没写测试"], "retry_prompt": "补测试"})
    t = status(e, e.create_task("x", "WRITE a.txt x", "fake", "m1", "edit").id, "done")
    e.review_task(t.id)
    t = status(e, t.id, "rejected")
    assert t.review["reasons"] == ["没写测试"]
    assert e.commander.review_calls[0][1].count("+x") == 1
    e.approve(t.id)
    assert e.store.get_task(t.id).status == "passed"


def test_running_task_records_start_time(make_engine):
    import time
    e = make_engine()
    before = time.time()
    t = e.create_task("x", "SLEEP 30", "fake", "m1", "edit")
    t = status(e, t.id, "running")
    assert t.started is not None and before <= t.started <= time.time()
    e.stop(t.id)
