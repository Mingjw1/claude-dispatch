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
