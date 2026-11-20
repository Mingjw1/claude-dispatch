import sys
from pathlib import Path

import pytest

from agentdesk.config import ConfigError, build_command, decorate_prompt, load_config

GLOBAL = '''
max_parallel = 2
commander_model = "sonnet"

[agents.copilot]
label = "Copilot"
models = ["auto", "gpt-6-luna"]
default_model = "auto"
[agents.copilot.commands]
read = ["copilot", "-s", "--model", "{model}"]
edit = ["copilot", "-s", "--allow-all-tools", "--model", "{model}"]
'''


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_load_global_config(tmp_path):
    cfg = load_config(write(tmp_path / "agents.toml", GLOBAL))
    assert cfg.max_parallel == 2 and cfg.commander_model == "sonnet"
    agent = cfg.agents["copilot"]
    assert agent.label == "Copilot" and agent.default_model == "auto"
    assert agent.levels() == ["read", "edit"]
    assert cfg.worktree_setup == [] and cfg.test_command == "" and cfg.task_timeout_minutes == 30


def test_project_file_overrides_global(tmp_path):
    g = write(tmp_path / "agents.toml", GLOBAL)
    proj = tmp_path / "proj"
    proj.mkdir()
    write(proj / ".agentdesk.toml",
          'worktree_setup = ["mkdir runs"]\ntest_command = "pytest -q"\n[agents.copilot]\nmodels = ["auto"]\n')
    cfg = load_config(g, proj)
    assert cfg.worktree_setup == ["mkdir runs"] and cfg.test_command == "pytest -q"
    assert cfg.agents["copilot"].models == ["auto"]
    assert cfg.agents["copilot"].label == "Copilot"


def test_unknown_level_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="未知档位"):
        load_config(write(tmp_path / "a.toml", '[agents.x]\nmodels = ["m"]\n[agents.x.commands]\nsuper = ["x"]\n'))


def test_default_model_must_be_listed(tmp_path):
    with pytest.raises(ConfigError, match="default_model"):
        load_config(write(tmp_path / "a.toml",
                          '[agents.x]\nmodels = ["m"]\ndefault_model = "z"\n[agents.x.commands]\nread = ["x"]\n'))


def test_bad_toml_names_the_file(tmp_path):
    with pytest.raises(ConfigError, match="格式错误"):
        load_config(write(tmp_path / "a.toml", "max_parallel = ="))


def test_build_command_fills_model_and_resolves_exe(tmp_path):
    text = GLOBAL.replace('"copilot", "-s"', f'"{Path(sys.executable).as_posix()}", "-s"')
    cfg = load_config(write(tmp_path / "agents.toml", text))
    argv = build_command(cfg.agents["copilot"], "edit", "gpt-6-luna")
    assert Path(argv[0]).samefile(sys.executable)
    assert argv[1:] == ["-s", "--allow-all-tools", "--model", "gpt-6-luna"]


def test_build_command_rejects_unsupported_level_and_model(tmp_path):
    cfg = load_config(write(tmp_path / "agents.toml", GLOBAL))
    with pytest.raises(ConfigError, match="改代码\\+联网"):
        build_command(cfg.agents["copilot"], "full", "auto")
    with pytest.raises(ConfigError, match="没有模型"):
        build_command(cfg.agents["copilot"], "read", "nope")


def test_build_command_reports_missing_exe(tmp_path):
    cfg = load_config(write(tmp_path / "agents.toml", GLOBAL.replace('"copilot", "-s"', '"no-such-cli-xyz", "-s"')))
    with pytest.raises(ConfigError, match="找不到命令 no-such-cli-xyz"):
        build_command(cfg.agents["copilot"], "read", "auto")


def test_decorate_prompt_per_level():
    assert decorate_prompt("read", "看看").startswith("只读任务")
    edit = decorate_prompt("edit", "改")
    assert edit.startswith("改") and "不要 git commit" in edit and "当前目录之内" in edit
    full = decorate_prompt("full", "改")
    assert "不要 git commit" in full and "当前目录之内" not in full


def test_shipped_agents_toml_loads():
    cfg = load_config(Path(__file__).resolve().parent.parent / "agents.toml")
    assert set(cfg.agents) == {"copilot", "gemini", "claude"}
    assert cfg.agents["gemini"].levels() == ["read", "edit"]
    assert cfg.agents["claude"].levels() == ["read", "edit", "full"]
