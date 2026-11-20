import json

import pytest

from agentdesk.store import Store


def new(store, **kw):
    return store.new_task(**{"title": "a", "prompt": "p", "agent": "fake", "model": "m1", "level": "edit", **kw})


def test_task_roundtrip_and_ids(tmp_path):
    s = Store(tmp_path / "ad")
    t1 = new(s)
    t2 = new(s, after=[t1.id], level="read")
    assert (t1.id, t2.id) == ("t0001", "t0002")
    t1.status = "running"
    s.save_task(t1)
    assert s.get_task("t0001").status == "running"
    assert [t.id for t in s.tasks()] == ["t0001", "t0002"]
    assert s.get_task("t0002").after == ["t0001"]


def test_survives_reopen_and_ignores_unknown_fields(tmp_path):
    s = Store(tmp_path / "ad")
    t = new(s, title="中文标题")
    path = tmp_path / "ad" / "tasks" / f"{t.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["future_field"] = 1
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    again = Store(tmp_path / "ad")
    assert again.get_task(t.id).title == "中文标题"
    assert new(again).id == "t0002"


def test_missing_task_raises_keyerror_with_message(tmp_path):
    with pytest.raises(KeyError, match="没有任务 t0009"):
        Store(tmp_path / "ad").get_task("t0009")


def test_requirements_and_logs(tmp_path):
    s = Store(tmp_path / "ad")
    r = s.new_requirement("补测试")
    r.tasks = ["t0001"]
    s.save_requirement(r)
    assert r.id == "r0001" and s.get_requirement("r0001").tasks == ["t0001"]
    assert [x.id for x in s.requirements()] == ["r0001"]
    s.append_log("t0001", "第一行\n")
    s.append_log("t0001", "第二行\n")
    assert s.read_log("t0001") == "第一行\n第二行\n" and s.read_log("t0009") == ""


def test_delete_task_removes_its_log(tmp_path):
    s = Store(tmp_path / "ad")
    t = new(s)
    s.append_log(t.id, "x\n")
    s.delete_task(t.id)
    assert s.tasks() == [] and s.read_log(t.id) == ""
