"""执行者配置：全局 agents.toml，项目里可选的 .agentdesk.toml 覆盖其中的项。"""
from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LEVELS = {"read": "只读", "edit": "改代码", "full": "改代码+联网"}
READ_PREFIX = "只读任务：不要修改、新建或删除任何文件。\n\n"
EDIT_SUFFIX = "\n\n完成后不要 git commit、push 或 reset，只改工作区文件。"
SCOPE_SUFFIX = "只读写当前目录之内的文件，不要访问上级目录或其他目录。"


class ConfigError(Exception):
    pass


@dataclass
class Agent:
    key: str
    label: str
    models: list[str]
    default_model: str
    commands: dict[str, list[str]]

    def levels(self) -> list[str]:
        return [level for level in LEVELS if level in self.commands]


@dataclass
class AppConfig:
    agents: dict[str, Agent]
    max_parallel: int = 3
    task_timeout_minutes: float = 30.0
    commander_model: str = "opus"
    worktree_setup: list[str] = field(default_factory=list)
    test_command: str = ""


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} 格式错误：{e}") from e


def load_config(global_path: Path, project_dir: Path | None = None) -> AppConfig:
    data = _read(global_path)
    if project_dir is not None and (project_dir / ".agentdesk.toml").is_file():
        data = _merge(data, _read(project_dir / ".agentdesk.toml"))
    agents = {}
    for key, raw in data.get("agents", {}).items():
        models = list(raw.get("models", []))
        commands = {level: list(cmd) for level, cmd in raw.get("commands", {}).items()}
        if not models or not commands:
            raise ConfigError(f"执行者 {key} 缺少 models 或 commands")
        unknown = sorted(set(commands) - set(LEVELS))
        if unknown:
            raise ConfigError(f"执行者 {key} 有未知档位：{', '.join(unknown)}")
        default = raw.get("default_model", models[0])
        if default not in models:
            raise ConfigError(f"执行者 {key} 的 default_model {default} 不在 models 里")
        agents[key] = Agent(key, raw.get("label", key), models, default, commands)
    return AppConfig(
        agents=agents,
        max_parallel=int(data.get("max_parallel", 3)),
        task_timeout_minutes=float(data.get("task_timeout_minutes", 30)),
        commander_model=str(data.get("commander_model", "opus")),
        worktree_setup=list(data.get("worktree_setup", [])),
        test_command=str(data.get("test_command", "")),
    )


def build_command(agent: Agent, level: str, model: str) -> list[str]:
    """拼出启动命令。任务说明不放进命令行，由调用方从标准输入传。"""
    if level not in agent.commands:
        raise ConfigError(f"{agent.label} 不支持“{LEVELS.get(level, level)}”档位")
    if model not in agent.models:
        raise ConfigError(f"{agent.label} 没有模型 {model}")
    argv = [part.replace("{model}", model) for part in agent.commands[level]]
    exe = shutil.which(argv[0])
    if exe is None:
        raise ConfigError(f"找不到命令 {argv[0]}，请确认已安装并在 PATH 里")
    return [exe, *argv[1:]]


def decorate_prompt(level: str, prompt: str) -> str:
    if level == "read":
        return READ_PREFIX + prompt
    return prompt + EDIT_SUFFIX + ("\n" + SCOPE_SUFFIX if level == "edit" else "")
