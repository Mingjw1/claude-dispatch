import sys

import pytest

from agentdesk import gitops
from agentdesk.commander import CommanderError
from agentdesk.engine import Engine, EngineError
from agentdesk.store import Store
from helpers import FakeCommander, fake_config, status, wait_for

PY = sys.executable
HAS_A = f'"{PY}" -c "import pathlib,sys; sys.exit(0 if pathlib.Path(\'a.txt\').exists() else 1)"'
STEP1 = {"id": 1, "title": "第一步", "level": "edit", "agent": "fake", "model": "m1", "files": ["a.txt"],
         "after": [], "prompt": "WRITE a.txt one", "accept": ""}
STEP2 = {"id": 2, "title": "第二步", "level": "edit", "agent": "fake", "model": "m1", "files": ["b.txt"],
         "after": [1], "prompt": "WRITE b.txt two\nWRITE c.txt three", "accept": HAS_A}


def req_status(e, req_id, *want, timeout=30.0):
    def check():
        r = e.store.get_requirement(req_id)
        return r if r.status in want else None
    return wait_for(check, timeout)


def planned(e, *steps):
    e.commander.plans.append({"tasks": list(steps)})
    return req_status(e, e.submit_requirement("需求").id, "confirm_plan")


def test_plan_becomes_drafts_with_mapped_dependencies(make_engine):
    e = make_engine()
    r = planned(e, STEP1, STEP2)
    t1, t2 = (e.store.get_task(i) for i in r.tasks)
    assert (t1.status, t2.status) == ("draft", "draft")
    assert t2.after == [t1.id] and t2.requirement == r.id and t1.files == ["a.txt"]


def test_dependent_task_sees_previous_changes_but_reviews_only_its_own(make_engine, repo):
    e = make_engine()
    r = planned(e, STEP1, STEP2)
    e.start_requirement(r.id)
    req_status(e, r.id, "confirm_merge")
    r = wait_for(lambda: (x := e.store.get_requirement(r.id)).summary == "总结" and x)  # 总结在进入确认后才写好
    t1, t2 = (e.store.get_task(i) for i in r.tasks)
    assert (t1.status, t2.status) == ("passed", "passed")
    assert t2.accept_code == 0
    assert gitops.changed_files(repo, t2.base, t2.commit) == ["b.txt", "c.txt"]
    outside = {call[0]: call[2] for call in e.commander.review_calls}
    assert outside == {t1.id: [], t2.id: ["c.txt"]}


def test_rejected_task_is_retried_then_requirement_reaches_merge(make_engine):
    e = make_engine()
    e.commander.reviews.append({"verdict": "fail", "reasons": ["不对"], "retry_prompt": "WRITE a.txt fixed"})
    r = planned(e, STEP1)
    e.start_requirement(r.id)
    t = status(e, r.tasks[0], "rejected")
    assert e.store.get_requirement(r.id).status == "running"
    e.retry(t.id, t.review["retry_prompt"], "fake", "m1", "edit", keep_changes=True)
    req_status(e, r.id, "confirm_merge")
    assert e.store.get_task(t.id).status == "passed"


def test_discarding_the_last_open_task_moves_to_merge(make_engine):
    e = make_engine()
    e.commander.reviews.append({"verdict": "fail", "reasons": ["不对"], "retry_prompt": "x"})
    r = planned(e, STEP1, {**STEP2, "after": [], "accept": ""})
    e.start_requirement(r.id)
    rejected = wait_for(lambda: next((t for t in map(e.store.get_task, r.tasks) if t.status == "rejected"), None))
    other = next(i for i in r.tasks if i != rejected.id)
    status(e, other, "passed")
    e.discard(rejected.id)
    req_status(e, r.id, "confirm_merge")


def test_plan_failure_lets_user_add_tasks(make_engine):
    e = make_engine()
    e.commander.plans.append(CommanderError("坏了", raw="原文"))
    r = req_status(e, e.submit_requirement("x").id, "confirm_plan")
    assert "坏了" in r.error and r.plan_raw == "原文"
    t = e.add_draft(r.id, "手动加的", "PRINT hi", "fake", "m1", "read")
    assert e.store.get_requirement(r.id).tasks == [t.id] and t.status == "draft"


def test_start_rejects_problem_drafts_until_fixed(make_engine):
    e = make_engine()
    r = planned(e, {**STEP1, "model": "m9"})
    assert e.store.get_task(r.tasks[0]).problems == ["Fake 没有模型 m9"]
    with pytest.raises(EngineError, match="没有模型 m9"):
        e.start_requirement(r.id)
    e.update_draft(r.tasks[0], model="m1")
    e.start_requirement(r.id)
    req_status(e, r.id, "confirm_merge")


def test_update_draft_only_allows_known_fields_on_drafts(make_engine):
    e = make_engine()
    r = planned(e, STEP1)
    with pytest.raises(EngineError, match="不能修改"):
        e.update_draft(r.tasks[0], status="passed")
    e.start_requirement(r.id)
    with pytest.raises(EngineError, match="还没开始"):
        e.update_draft(r.tasks[0], title="x")


def test_start_rejects_dependency_cycle(make_engine):
    e = make_engine()
    r = planned(e, STEP1)
    t2 = e.add_draft(r.id, "二", "PRINT hi", "fake", "m1", "read", after=[r.tasks[0]])
    e.update_draft(r.tasks[0], after=[t2.id])
    with pytest.raises(EngineError, match="循环"):
        e.start_requirement(r.id)


def test_delete_draft_drops_it_from_dependencies(make_engine):
    e = make_engine()
    r = planned(e, STEP1, STEP2)
    e.delete_draft(r.tasks[0])
    r2 = e.store.get_requirement(r.id)
    assert r2.tasks == [r.tasks[1]] and e.store.get_task(r.tasks[1]).after == []


def test_review_failure_falls_back_to_user_approval(make_engine):
    e = make_engine()
    e.commander.reviews.append(CommanderError("超时"))
    r = planned(e, STEP1)
    e.start_requirement(r.id)
    t = status(e, r.tasks[0], "done")
    wait_for(lambda: "Claude 审查失败" in e.store.get_task(t.id).error)
    e.approve(t.id)
    req_status(e, r.id, "confirm_merge")


def test_restart_during_planning_reopens_requirement(repo):
    store = Store(gitops.store_dir(repo))
    r = store.new_requirement("x")
    Engine(repo, fake_config(), store, FakeCommander()).recover()
    r = store.get_requirement(r.id)
    assert r.status == "confirm_plan" and "重启" in r.error
