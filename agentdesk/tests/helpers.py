import sys
import time
from pathlib import Path

from agentdesk.config import Agent, AppConfig

FAKES = Path(__file__).resolve().parent / "fakes"
FAKE_AGENT = [sys.executable, str(FAKES / "fake_agent.py")]


def fake_config(**kw) -> AppConfig:
    cmds = {lv: [*FAKE_AGENT, "--model", "{model}", "--level", lv] for lv in ("read", "edit", "full")}
    agents = {
        "fake": Agent("fake", "Fake", ["m1", "m2"], "m1", cmds),
        "readonly": Agent("readonly", "ReadOnly", ["m1"], "m1", {"read": cmds["read"]}),
    }
    return AppConfig(agents=agents, **kw)


def wait_for(pred, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = pred()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("等待超时")


from agentdesk.commander import validate_plan  # noqa: E402


class FakeCommander:
    """代替 Claude：plans / reviews / summaries 里放预置结果（放异常实例则抛出）。"""

    def __init__(self):
        self.plans, self.reviews, self.summaries, self.review_calls = [], [], [], []

    @staticmethod
    def _give(item):
        if isinstance(item, Exception):
            raise item
        return item

    def plan(self, requirement, cfg):
        return validate_plan(self._give(self.plans.pop(0)), cfg)

    def review(self, task, diff, outside):
        self.review_calls.append((task.id, diff, outside))
        default = {"verdict": "pass", "reasons": ["ok"], "retry_prompt": ""}
        return self._give(self.reviews.pop(0) if self.reviews else default)

    def summarize(self, items):
        return self._give(self.summaries.pop(0) if self.summaries else "总结")


def status(engine, task_id, *want, timeout=30.0):
    def check():
        t = engine.store.get_task(task_id)
        return t if t.status in want else None
    return wait_for(check, timeout)
