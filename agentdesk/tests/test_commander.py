import json
import sys

import pytest

from agentdesk.commander import Commander, CommanderError, validate_plan, validate_review
from agentdesk.store import Task
from helpers import FAKES, fake_config

PLAN = {"tasks": [
    {"id": 1, "title": "改 a", "level": "edit", "agent": "fake", "model": "m1", "files": ["a.py"],
     "after": [], "prompt": "做 a", "accept": "pytest"},
    {"id": 2, "title": "改 a 第二步", "level": "edit", "agent": "fake", "model": "m9", "files": ["a.py"],
     "after": [1], "prompt": "做 b", "accept": ""},
]}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    path = tmp_path / "answers.json"
    monkeypatch.setenv("AGENTDESK_FAKE_CLAUDE", str(path))

    def answers(*items):
        path.write_text(json.dumps(list(items), ensure_ascii=False), encoding="utf-8")

    def prompts():
        return (tmp_path / "answers.json.prompts").read_text(encoding="utf-8").split("\n=====\n")[:-1]

    return answers, prompts


def make(tmp_path):
    return Commander([sys.executable, str(FAKES / "fake_claude.py")], tmp_path, 30)


def test_plan_parses_fenced_json_and_flags_problems(tmp_path, fake):
    answers, prompts = fake
    answers({"result": "好的\n```json\n" + json.dumps(PLAN, ensure_ascii=False) + "\n```"})
    tasks = make(tmp_path).plan("补测试", fake_config())
    assert [t["id"] for t in tasks] == ["1", "2"] and tasks[1]["after"] == ["1"]
    assert tasks[0]["problems"] == [] and tasks[1]["problems"] == ["Fake 没有模型 m9"]
    assert "补测试" in prompts()[0] and "fake: read/edit/full / m1, m2" in prompts()[0]


def test_plan_retries_once_with_the_error(tmp_path, fake):
    answers, prompts = fake
    answers({"result": "我不知道"}, {"result": json.dumps(PLAN)})
    assert len(make(tmp_path).plan("x", fake_config())) == 2
    assert "上一次的回答不符合要求" in prompts()[1]


def test_plan_gives_up_after_second_bad_answer_and_keeps_raw(tmp_path, fake):
    answers, _ = fake
    answers({"result": "不行"}, {"result": "还是不行"})
    with pytest.raises(CommanderError) as e:
        make(tmp_path).plan("x", fake_config())
    assert e.value.raw == "还是不行"


def test_plan_rejects_cycles_and_unknown_dependencies():
    cyc = {"tasks": [{"id": 1, "title": "a", "prompt": "p", "after": [2]},
                     {"id": 2, "title": "b", "prompt": "p", "after": [1]}]}
    with pytest.raises(ValueError, match="循环"):
        validate_plan(cyc, fake_config())
    with pytest.raises(ValueError, match="不存在"):
        validate_plan({"tasks": [{"id": 1, "title": "a", "prompt": "p", "after": [7]}]}, fake_config())


def test_review_requires_retry_prompt_on_fail():
    with pytest.raises(ValueError, match="retry_prompt"):
        validate_review({"verdict": "fail", "reasons": ["x"], "retry_prompt": ""})
    with pytest.raises(ValueError, match="verdict"):
        validate_review({"verdict": "ok", "reasons": []})


def test_review_passes_material_to_claude(tmp_path, fake):
    answers, prompts = fake
    answers({"result": json.dumps({"verdict": "fail", "reasons": ["没测边界"], "retry_prompt": "补边界"},
                                  ensure_ascii=False)})
    task = Task(id="t0001", title="t", prompt="做 a", agent="fake", model="m1", level="edit", files=["a.py"],
                accept="pytest", accept_code=1, accept_output="1 failed")
    assert make(tmp_path).review(task, "+x", ["b.py"]) == {
        "verdict": "fail", "reasons": ["没测边界"], "retry_prompt": "补边界"}
    p = prompts()[0]
    assert "做 a" in p and "b.py" in p and "1 failed" in p and "+x" in p


def test_nonzero_exit_is_commander_error(tmp_path, fake):
    fake[0]({"exit": 1})
    with pytest.raises(CommanderError, match="退出码 1"):
        make(tmp_path).summarize(["a"])


def test_missing_claude_is_commander_error(tmp_path):
    with pytest.raises(CommanderError, match="启动"):
        Commander([str(tmp_path / "no-claude.exe")], tmp_path, 30).summarize(["a"])


def test_summarize_returns_text(tmp_path, fake):
    fake[0]({"result": "  总结  "})
    assert make(tmp_path).summarize(["- 任务"]) == "总结"
