import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def pytest_configure(config):
    (ROOT / "runs").mkdir(exist_ok=True)   # basetemp 的父目录必须存在


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "仓库"   # 真实项目路径里有中文
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "tester")
    _git(path, "config", "user.email", "tester@example.com")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


from agentdesk import gitops  # noqa: E402
from agentdesk.engine import Engine  # noqa: E402
from agentdesk.store import Store  # noqa: E402
from helpers import FakeCommander, fake_config  # noqa: E402


@pytest.fixture
def make_engine(repo):
    engines = []

    def make(**cfg):
        engine = Engine(repo, fake_config(**cfg), Store(gitops.store_dir(repo)), FakeCommander())
        engine.start()
        engines.append(engine)
        return engine

    yield make
    for engine in engines:
        engine.close()
