# agentdesk 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做一个本机使用的轻量多 agent 指挥台：Claude 拆任务、审查，Gemini / Copilot / Claude 等命令行 agent 在独立 worktree 里执行，用户在 Edge 应用窗口里确认拆分、重派、合并。

**Architecture:** Python 标准库写的本地 HTTP 服务（只听 127.0.0.1、带口令、SSE 推送），原生 HTML/JS 界面。`engine` 负责调度和状态流转，`proc` 启动子进程（任务说明一律走标准输入），`gitops` 管 worktree 和合并，`commander` 调用 `claude -p` 拆任务和审查，`store` 把状态存成 `.git/agentdesk/` 下的 JSON。

**Tech Stack:** Python 3.13 标准库（http.server、subprocess、tomllib、threading）；pytest（只用于开发）；原生 JS；git；Edge `--app` 窗口。

**Spec:** `docs/superpowers/specs/2026-11-20-agentdesk-design.md`

## Global Constraints

- 只用 Python 3.13 标准库，不引入第三方运行依赖；pytest 只在开发时用。
- 应用自己不连任何模型服务，只调用本机已安装的命令行（claude、gemini、copilot 等）。
- 任务说明一律从标准输入传给执行者和 Claude，命令行参数里不出现任务说明。
- 命令写成参数数组，不经过 shell；只有验收命令、测试命令、`worktree_setup` 用 shell 执行。
- 状态文件放 `<git 公共目录>/agentdesk/`；worktree 放仓库外 `<仓库父目录>/<仓库名>.agentdesk-wt/<任务号>`，分支 `agentdesk/<任务号>`。
- 服务只监听 127.0.0.1；除 `/static/*` 外所有请求必须带口令（请求头 `X-Token` 或查询参数 `t`）。
- 测试不写 C 盘临时目录：`--basetemp=runs/.pytest_tmp`，`conftest.py` 在启动时建好 `runs/`。
- 界面和报错信息用中文。
- 档位固定为 `read`（只读）、`edit`（改代码）、`full`（改代码+联网），显示文字写在代码里，`agents.toml` 里不需要 `[levels]` 表。
- 与设计文档的两处有意差异（已在交付说明里告诉用户）：
  1. 应用重启时只把运行中的任务标为"中断"并提示用户去任务管理器确认，**不按记录的 pid 结束进程**——pid 可能已被别的程序复用，误杀代价太大。
  2. 启动时的可用性检测只检查命令能否执行 `--version`，检测不了"没登录"；没登录会在第一次派活时以任务失败的形式暴露。

## Review Focus

1. 项目路径和文件名含中文（真实项目是 `D:\mingjw\feature验证\...`）：git 输出必须不转义，worktree 能建、改动文件名能原样列出。测试仓库目录就叫 `仓库`，并用 `中文.txt`（Task 4、Task 7）。
2. 任务说明含 `& | % "` 等字符，经 npm 的 `.cmd` 包装后仍原样到达执行者（Task 3）。
3. 合并时主工作区有未提交改动：拒绝合并、不动仓库（Task 9）。
4. 有依赖的任务在前置任务还没合并时启动：必须看到前置任务的改动，审查时的改动只含自己这轮（Task 8）。
5. 应用在任务运行中被关掉再打开：任务标为中断，不误杀进程，需求不会卡在"拆任务中"（Task 7、Task 8）。

## 文件结构

```
agentdesk/
├─ pyproject.toml          pytest 配置
├─ agents.toml             全局执行者配置（随应用发布）
├─ 启动.bat                双击启动
├─ README.md
├─ agentdesk/
│  ├─ __init__.py
│  ├─ __main__.py          启动服务、打开 Edge 应用窗口
│  ├─ config.py            读配置、拼命令、给任务说明加约束
│  ├─ store.py             Task / Requirement 数据和 JSON 存储、日志
│  ├─ proc.py              子进程：标准输入喂说明、逐行收输出、超时、结束进程树
│  ├─ gitops.py            worktree、提交、改动、合并、回退
│  ├─ commander.py         claude -p：拆任务、审查、总结；JSON 提取和校验
│  ├─ merger.py            按依赖顺序合并、跑全量测试、撤销
│  ├─ engine.py            调度、状态流转、重派、停止、合并入口
│  ├─ server.py            HTTP 接口、口令、SSE、项目切换、执行者检测
│  └─ web/                 index.html、app.js、style.css
└─ tests/
   ├─ conftest.py          建 runs/；repo、make_engine 夹具
   ├─ helpers.py           fake_config、FakeCommander、wait_for、status
   ├─ fakes/fake_agent.py  假执行者
   ├─ fakes/fake_claude.py 假 claude
   └─ test_*.py
```

---

### Task 1: 项目骨架和配置

**Files:**
- Create: `pyproject.toml`, `agents.toml`, `agentdesk/__init__.py`, `agentdesk/config.py`
- Create: `tests/conftest.py`, `tests/helpers.py`, `tests/fakes/fake_agent.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `LEVELS: dict[str, str]`；`ConfigError`；`Agent(key, label, models, default_model, commands)` 及 `Agent.levels() -> list[str]`；`AppConfig(agents, max_parallel=3, task_timeout_minutes=30.0, commander_model="opus", worktree_setup=[], test_command="")`；`load_config(global_path: Path, project_dir: Path | None = None) -> AppConfig`；`build_command(agent: Agent, level: str, model: str) -> list[str]`；`decorate_prompt(level: str, prompt: str) -> str`
- Produces（测试辅助）：`tests/helpers.py` 的 `FAKES`、`FAKE_AGENT`、`fake_config(**kw) -> AppConfig`、`wait_for(pred, timeout=30.0)`；`conftest.py` 的 `repo` 夹具（目录名 `仓库`，分支 `main`，有一个提交）

- [ ] **Step 1: 建骨架**

`pyproject.toml`：
```toml
[project]
name = "agentdesk"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = []

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "--basetemp=runs/.pytest_tmp"  # 测试临时文件放项目里，不写 C 盘
```

`agentdesk/__init__.py`：
```python
"""agentdesk：Claude 指挥、多个命令行 agent 执行的本机指挥台。"""
```

`tests/conftest.py`：
```python
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
```

`tests/fakes/fake_agent.py`：
```python
"""假执行者：从标准输入读任务说明，逐行回显（前缀 "> "），并执行其中的指令：
WRITE <文件> <内容> / SLEEP <秒> / EXIT <退出码> / PRINT <文字>"""
import pathlib
import sys
import time

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
prompt = sys.stdin.read()
print(f"got prompt {len(prompt)} chars, args {sys.argv[1:]}", flush=True)
for line in prompt.splitlines():
    print(f"> {line}", flush=True)
    cmd, _, rest = line.partition(" ")
    if cmd == "WRITE":
        name, _, content = rest.partition(" ")
        path = pathlib.Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n", encoding="utf-8")
    elif cmd == "SLEEP":
        time.sleep(float(rest))
    elif cmd == "EXIT":
        sys.exit(int(rest))
    elif cmd == "PRINT":
        print(rest, flush=True)
```

`tests/helpers.py`：
```python
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
```

- [ ] **Step 2: 写失败的测试** `tests/test_config.py`：
```python
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
```

- [ ] **Step 3: 运行，确认失败**

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'agentdesk.config'`

- [ ] **Step 4: 实现** `agentdesk/config.py`：
```python
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
```

`agents.toml`：
```toml
# agentdesk 全局配置。项目根目录可放 .agentdesk.toml 覆盖其中任意项，
# 例如 worktree_setup = ["mkdir runs"]、test_command = "python -m pytest -q"、某个执行者的 models。
# 档位固定为 read（只读）、edit（改代码）、full（改代码+联网）；每个执行者只为自己支持的档位写命令。
# 任务说明一律从标准输入传入，命令里不要写任务说明；{model} 会被替换成所选模型。
max_parallel = 3
task_timeout_minutes = 30
commander_model = "opus"

[agents.copilot]
label = "Copilot"
models = ["auto", "gpt-6-luna", "gpt-6.1-sol"]   # 以 Copilot 里 /model 列出的为准
default_model = "auto"
[agents.copilot.commands]
read = ["copilot", "-s", "--no-ask-user", "--allow-all-tools", "--deny-tool", "write", "--model", "{model}"]
edit = ["copilot", "-s", "--no-ask-user", "--allow-all-tools", "--deny-tool", "shell(git push)", "--deny-tool", "shell(git commit)", "--model", "{model}"]
full = ["copilot", "-s", "--no-ask-user", "--allow-all", "--deny-tool", "shell(git push)", "--deny-tool", "shell(git commit)", "--model", "{model}"]

[agents.gemini]
label = "Gemini"
models = ["auto-gemini-3"]
default_model = "auto-gemini-3"
[agents.gemini.commands]
read = ["gemini", "--approval-mode", "plan", "-m", "{model}"]
edit = ["gemini", "--approval-mode", "yolo", "-m", "{model}"]

[agents.claude]
label = "Claude"
models = ["haiku", "sonnet", "opus"]
default_model = "sonnet"
[agents.claude.commands]
read = ["claude", "-p", "--model", "{model}", "--permission-mode", "plan"]
edit = ["claude", "-p", "--model", "{model}", "--permission-mode", "acceptEdits"]
full = ["claude", "-p", "--model", "{model}", "--permission-mode", "bypassPermissions"]
```

- [ ] **Step 5: 运行，确认通过**

Run: `python -m pytest tests/test_config.py -q`
Expected: 10 passed

- [ ] **Step 6: 提交**
```bash
git add pyproject.toml agents.toml agentdesk tests
git commit -m "feat: 配置读取、命令拼装和测试骨架"
```

### Task 2: 任务和需求的存储

**Files:**
- Create: `agentdesk/store.py`, `tests/test_store.py`

**Interfaces:**
- Produces: 任务状态常量 `DRAFT QUEUED RUNNING DONE REVIEWING PASSED REJECTED FAILED STOPPED INTERRUPTED DISCARDED MERGED`（值为小写英文同名，`DONE="done"` 表示"待审查"）；需求状态常量 `PLANNING CONFIRM_PLAN IN_PROGRESS CONFIRM_MERGE REQ_MERGED`（值 `planning confirm_plan running confirm_merge merged`）
- Produces: `Task`、`Requirement` 数据类（字段见代码）；`Store(root: Path)`，方法 `new_task(**fields) -> Task`、`save_task(task)`、`get_task(id) -> Task`（不存在抛 `KeyError("没有任务 …")`）、`tasks() -> list[Task]`、`delete_task(id)`、`new_requirement(text) -> Requirement`、`save_requirement(r)`、`get_requirement(id)`、`requirements()`、`append_log(task_id, text)`、`read_log(task_id) -> str`

- [ ] **Step 1: 写失败的测试** `tests/test_store.py`：
```python
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
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_store.py -q`
Expected: FAIL，`No module named 'agentdesk.store'`

- [ ] **Step 3: 实现** `agentdesk/store.py`：
```python
"""需求、任务、日志：存在 <git 公共目录>/agentdesk/ 下，不进版本库。"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DRAFT, QUEUED, RUNNING, DONE, REVIEWING = "draft", "queued", "running", "done", "reviewing"
PASSED, REJECTED, FAILED, STOPPED = "passed", "rejected", "failed", "stopped"
INTERRUPTED, DISCARDED, MERGED = "interrupted", "discarded", "merged"

PLANNING, CONFIRM_PLAN, IN_PROGRESS = "planning", "confirm_plan", "running"
CONFIRM_MERGE, REQ_MERGED = "confirm_merge", "merged"


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    agent: str
    model: str
    level: str
    accept: str = ""
    files: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    requirement: str | None = None
    status: str = DRAFT
    problems: list[str] = field(default_factory=list)  # 拆任务时发现的配置问题，确认表格里标红
    base: str = ""            # worktree 起点提交
    branch: str = ""
    worktree: str = ""
    commit: str = ""          # 应用在任务分支上的最新提交
    exit_code: int | None = None
    accept_code: int | None = None
    accept_output: str = ""
    review: dict | None = None   # {"verdict", "reasons", "retry_prompt"}
    attempts: int = 0
    pid: int | None = None
    merge: dict | None = None    # 手动任务的合并结果，格式见 merger.merge_tasks
    error: str = ""


@dataclass
class Requirement:
    id: str
    text: str
    status: str = PLANNING
    tasks: list[str] = field(default_factory=list)
    plan_raw: str = ""        # Claude 拆任务失败时的原文
    summary: str = ""
    merge: dict | None = None
    error: str = ""


def _load(cls, path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


class Store:
    def __init__(self, root: Path):
        self.root = root
        for sub in ("tasks", "requirements", "logs"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _write(self, path: Path, obj) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(obj), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _next_id(self, sub: str, prefix: str) -> str:
        nums = [int(p.stem[1:]) for p in (self.root / sub).glob(f"{prefix}*.json")]
        return f"{prefix}{max(nums, default=0) + 1:04d}"

    def _get(self, cls, sub: str, item_id: str, what: str):
        path = self.root / sub / f"{item_id}.json"
        with self._lock:
            if not path.is_file():
                raise KeyError(f"没有{what} {item_id}")
            return _load(cls, path)

    # 任务
    def new_task(self, **kw) -> Task:
        with self._lock:
            task = Task(id=self._next_id("tasks", "t"), **kw)
            self.save_task(task)
            return task

    def save_task(self, task: Task) -> None:
        with self._lock:
            self._write(self.root / "tasks" / f"{task.id}.json", task)

    def get_task(self, task_id: str) -> Task:
        return self._get(Task, "tasks", task_id, "任务")

    def tasks(self) -> list[Task]:
        with self._lock:
            return [_load(Task, p) for p in sorted((self.root / "tasks").glob("t*.json"))]

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            (self.root / "tasks" / f"{task_id}.json").unlink(missing_ok=True)
            self._log(task_id).unlink(missing_ok=True)

    # 需求
    def new_requirement(self, text: str) -> Requirement:
        with self._lock:
            req = Requirement(id=self._next_id("requirements", "r"), text=text)
            self.save_requirement(req)
            return req

    def save_requirement(self, req: Requirement) -> None:
        with self._lock:
            self._write(self.root / "requirements" / f"{req.id}.json", req)

    def get_requirement(self, req_id: str) -> Requirement:
        return self._get(Requirement, "requirements", req_id, "需求")

    def requirements(self) -> list[Requirement]:
        with self._lock:
            return [_load(Requirement, p) for p in sorted((self.root / "requirements").glob("r*.json"))]

    # 日志
    def _log(self, task_id: str) -> Path:
        return self.root / "logs" / f"{task_id}.log"

    def append_log(self, task_id: str, text: str) -> None:
        with self._lock, open(self._log(task_id), "a", encoding="utf-8") as f:
            f.write(text)

    def read_log(self, task_id: str) -> str:
        path = self._log(task_id)
        return path.read_text(encoding="utf-8") if path.is_file() else ""
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m pytest tests/test_store.py -q`
Expected: 5 passed

- [ ] **Step 5: 提交**
```bash
git add agentdesk/store.py tests/test_store.py
git commit -m "feat: 任务、需求和日志的 JSON 存储"
```

### Task 3: 子进程

**Files:**
- Create: `agentdesk/proc.py`, `tests/test_proc.py`

**Interfaces:**
- Produces: `NO_WINDOW`（Windows 上的 `CREATE_NO_WINDOW`，其他系统为 0）；`child_env() -> dict`（加 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`）；`kill_tree(pid)`
- Produces: `RunResult(code: int | None, timed_out=False, stopped=False)`；`Proc(argv: list[str] | str, cwd: Path, stdin_text: str | None, on_line: Callable[[str], None], shell=False)`，属性 `pid`，方法 `stop()`、`wait(timeout_s) -> RunResult`
- Produces: `run_capture(command: str, cwd, timeout_s) -> tuple[int | None, str]`（shell 执行，合并输出，超时码为 None）；`run_io(argv, cwd, stdin_text, timeout_s) -> tuple[int, str, str]`（标准输出、错误分开；超时抛 `TimeoutError`）

- [ ] **Step 1: 写失败的测试** `tests/test_proc.py`：
```python
import os
import sys
import threading

import pytest

from agentdesk.proc import Proc, run_capture, run_io
from helpers import FAKE_AGENT, FAKES

SPECIAL = 'A&B|C %PATH% "引号" 中文'


def test_prompt_with_special_chars_reaches_agent_via_stdin(tmp_path):
    lines = []
    r = Proc(FAKE_AGENT, tmp_path, f"PRINT {SPECIAL}\nWRITE out.txt 完成", lines.append).wait(30)
    assert r.code == 0 and SPECIAL in lines
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "完成\n"


@pytest.mark.skipif(os.name != "nt", reason="npm 的 .cmd 包装只在 Windows 上有")
def test_cmd_shim_like_npm_keeps_stdin_intact(tmp_path):
    shim = tmp_path / "fake.cmd"
    shim.write_text(f'@"{sys.executable}" "{FAKES / "fake_agent.py"}" %*\r\n', encoding="utf-8")
    lines = []
    r = Proc([str(shim), "--model", "m1"], tmp_path, f"PRINT {SPECIAL}", lines.append).wait(30)
    assert r.code == 0 and SPECIAL in lines


def test_exit_code_and_line_streaming(tmp_path):
    lines = []
    r = Proc(FAKE_AGENT, tmp_path, "PRINT one\nPRINT two\nEXIT 3", lines.append).wait(30)
    assert r.code == 3 and [l for l in lines if not l.startswith((">", "got"))] == ["one", "two"]


def test_timeout_kills_the_process(tmp_path):
    r = Proc(FAKE_AGENT, tmp_path, "SLEEP 30", lambda line: None).wait(1)
    assert r.timed_out and r.code is None


def test_stop(tmp_path):
    p = Proc(FAKE_AGENT, tmp_path, "SLEEP 30", lambda line: None)
    threading.Timer(0.5, p.stop).start()
    assert p.wait(20).stopped


def test_run_capture_uses_shell_and_utf8(tmp_path):
    code, out = run_capture(f'"{sys.executable}" -c "print(\'你好\'); raise SystemExit(2)"', tmp_path, 30)
    assert code == 2 and "你好" in out


def test_run_io_separates_streams(tmp_path):
    code, out, err = run_io([sys.executable, "-c",
                             "import sys; print(sys.stdin.read().upper()); print('warn', file=sys.stderr)"],
                            tmp_path, "abc", 30)
    assert (code, out.strip(), err.strip()) == (0, "ABC", "warn")


def test_run_io_timeout(tmp_path):
    with pytest.raises(TimeoutError):
        run_io([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, "", 1)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_proc.py -q`
Expected: FAIL，`No module named 'agentdesk.proc'`

- [ ] **Step 3: 实现** `agentdesk/proc.py`：
```python
"""子进程：从标准输入喂任务说明，逐行收输出，超时或停止时结束整个进程树。"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_SESSION = os.name != "nt"


def child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@dataclass
class RunResult:
    code: int | None
    timed_out: bool = False
    stopped: bool = False


class Proc:
    def __init__(self, argv: list[str] | str, cwd: Path, stdin_text: str | None,
                 on_line: Callable[[str], None], shell: bool = False):
        self._stopped = False
        self._p = subprocess.Popen(
            argv, cwd=cwd, shell=shell, env=child_env(),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW, start_new_session=NEW_SESSION)
        if stdin_text is not None:
            threading.Thread(target=self._feed, args=(stdin_text,), daemon=True).start()
        self._reader = threading.Thread(target=self._read, args=(on_line,), daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int:
        return self._p.pid

    def _feed(self, text: str) -> None:
        try:
            self._p.stdin.write(text)
            self._p.stdin.close()
        except OSError:
            pass  # 进程已经退出

    def _read(self, on_line: Callable[[str], None]) -> None:
        for line in self._p.stdout:
            on_line(line.rstrip("\r\n"))

    def stop(self) -> None:
        self._stopped = True
        kill_tree(self._p.pid)

    def wait(self, timeout_s: float | None = None) -> RunResult:
        try:
            code = self._p.wait(timeout_s)
        except subprocess.TimeoutExpired:
            kill_tree(self._p.pid)
            self._p.wait()
            self._reader.join(5)
            return RunResult(None, timed_out=True)
        self._reader.join(5)  # 孙进程还拿着输出管道时最多等 5 秒
        return RunResult(None, stopped=True) if self._stopped else RunResult(code)


def run_capture(command: str, cwd: Path, timeout_s: float) -> tuple[int | None, str]:
    """用 shell 执行验收或测试命令，返回退出码（超时为 None）和全部输出。"""
    lines: list[str] = []
    result = Proc(command, cwd, None, lines.append, shell=True).wait(timeout_s)
    return result.code, "\n".join(lines) + ("\n[超时，已结束]" if result.timed_out else "")


def run_io(argv: list[str], cwd: Path, stdin_text: str, timeout_s: float) -> tuple[int, str, str]:
    """标准输出和错误分开收（解析 JSON 用）；超时结束进程树并抛 TimeoutError。"""
    p = subprocess.Popen(argv, cwd=cwd, env=child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW, start_new_session=NEW_SESSION)
    try:
        out, err = p.communicate(stdin_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        p.communicate()
        raise TimeoutError(f"{Path(argv[0]).name} 超过 {timeout_s:.0f} 秒没有结束")
    return p.returncode, out, err
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m pytest tests/test_proc.py -q`
Expected: 8 passed

- [ ] **Step 5: 提交**
```bash
git add agentdesk/proc.py tests/test_proc.py
git commit -m "feat: 子进程启动、逐行输出、超时和结束进程树"
```

### Task 4: git 操作

**Files:**
- Create: `agentdesk/gitops.py`, `tests/test_gitops.py`

**Interfaces:**
- Consumes: `proc.NO_WINDOW`
- Produces: `GitError`；`git(cwd, *args, check=True) -> str`（总带 `-c core.quotepath=false`，只去掉末尾换行）；`repo_root(path) -> Path`；`store_dir(repo) -> Path`；`head(cwd)`；`branch(cwd)`；`is_clean(cwd) -> bool`；`worktree_dir(repo, task_id) -> Path`；`create_worktree(repo, task_id, start) -> tuple[Path, str]`；`remove_worktree(repo, path: Path | None, branch: str | None)`；`commit_all(wt, message) -> str | None`；`diff(cwd, base, target="HEAD", limit=200_000) -> str`；`diff_stat(cwd, base, target="HEAD")`；`changed_files(cwd, base, target="HEAD") -> list[str]`；`MergeResult(ok, conflicts, message)`；`merge(cwd, ref, message) -> MergeResult`（冲突时自动 `merge --abort`）；`reset_hard(cwd, sha)`

- [ ] **Step 1: 写失败的测试** `tests/test_gitops.py`：
```python
from agentdesk import gitops


def test_store_dir_is_inside_git_dir_even_from_a_worktree(repo):
    assert gitops.store_dir(repo) == (repo / ".git" / "agentdesk").resolve()
    wt, _ = gitops.create_worktree(repo, "t0001", gitops.head(repo))
    assert gitops.store_dir(wt) == gitops.store_dir(repo)


def test_worktree_lifecycle_with_chinese_names(repo):
    base = gitops.head(repo)
    wt, branch = gitops.create_worktree(repo, "t0001", base)
    assert wt == repo.parent / "仓库.agentdesk-wt" / "t0001" and branch == "agentdesk/t0001"
    assert gitops.commit_all(wt, "nothing") is None
    (wt / "中文.txt").write_text("x\n", encoding="utf-8")
    sha = gitops.commit_all(wt, "add")
    assert sha == gitops.head(wt) != base
    assert gitops.changed_files(repo, base, sha) == ["中文.txt"]
    assert "+x" in gitops.diff(repo, base, sha) and "中文.txt" in gitops.diff_stat(repo, base, sha)
    gitops.remove_worktree(repo, wt, branch)
    assert not wt.exists() and gitops.git(repo, "branch", "--list", branch) == ""


def test_create_worktree_replaces_a_stale_directory(repo):
    wt, branch = gitops.create_worktree(repo, "t0001", gitops.head(repo))
    gitops.git(repo, "worktree", "remove", "--force", str(wt))
    wt.mkdir(parents=True)
    (wt / "leftover.txt").write_text("x", encoding="utf-8")
    again, _ = gitops.create_worktree(repo, "t0001", gitops.head(repo))
    assert again == wt and not (wt / "leftover.txt").exists()


def test_huge_diff_falls_back_to_stat(repo):
    base = gitops.head(repo)
    wt, _ = gitops.create_worktree(repo, "t0001", base)
    (wt / "big.txt").write_text("y\n" * 5000, encoding="utf-8")
    sha = gitops.commit_all(wt, "big")
    text = gitops.diff(repo, base, sha, limit=1000)
    assert text.startswith("改动超过") and "big.txt" in text


def test_merge_conflict_is_aborted(repo):
    base = gitops.head(repo)
    wt1, b1 = gitops.create_worktree(repo, "t1", base)
    (wt1 / "README.md").write_text("one\n", encoding="utf-8")
    gitops.commit_all(wt1, "one")
    wt2, b2 = gitops.create_worktree(repo, "t2", base)
    (wt2 / "README.md").write_text("two\n", encoding="utf-8")
    gitops.commit_all(wt2, "two")
    assert gitops.merge(repo, b1, "m1").ok
    r = gitops.merge(repo, b2, "m2")
    assert not r.ok and r.conflicts == ["README.md"]
    assert gitops.is_clean(repo) and (repo / "README.md").read_text(encoding="utf-8") == "one\n"


def test_is_clean_and_reset_hard(repo):
    base = gitops.head(repo)
    (repo / "x.txt").write_text("x", encoding="utf-8")
    assert not gitops.is_clean(repo)
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "-m", "x")
    gitops.reset_hard(repo, base)
    assert gitops.head(repo) == base and gitops.is_clean(repo) and gitops.branch(repo) == "main"


def test_repo_root_rejects_non_repo(tmp_path):
    import pytest
    with pytest.raises(gitops.GitError):
        gitops.repo_root(tmp_path / "nope")
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_gitops.py -q`
Expected: FAIL，`cannot import name 'gitops'`

- [ ] **Step 3: 实现** `agentdesk/gitops.py`：
```python
"""对 git 的最小封装。中文路径和文件名原样处理（core.quotepath=false）。"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentdesk.proc import NO_WINDOW


class GitError(Exception):
    pass


def _run(cwd: Path, args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = _run(cwd, args)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} 失败：{(r.stderr or r.stdout).strip()}")
    return r.stdout.rstrip("\n")


def repo_root(path: Path) -> Path:
    if not path.is_dir():
        raise GitError(f"目录不存在：{path}")
    return Path(git(path, "rev-parse", "--show-toplevel")).resolve()


def store_dir(repo: Path) -> Path:
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    return (common if common.is_absolute() else repo / common).resolve() / "agentdesk"


def head(cwd: Path) -> str:
    return git(cwd, "rev-parse", "HEAD")


def branch(cwd: Path) -> str:
    return git(cwd, "rev-parse", "--abbrev-ref", "HEAD")


def is_clean(cwd: Path) -> bool:
    return git(cwd, "status", "--porcelain") == ""


def worktree_dir(repo: Path, task_id: str) -> Path:
    return repo.parent / f"{repo.name}.agentdesk-wt" / task_id


def remove_worktree(repo: Path, path: Path | None, branch_name: str | None) -> None:
    if path is not None and path.exists():
        git(repo, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)
    if branch_name:
        git(repo, "branch", "-D", branch_name, check=False)


def create_worktree(repo: Path, task_id: str, start: str) -> tuple[Path, str]:
    path, name = worktree_dir(repo, task_id), f"agentdesk/{task_id}"
    remove_worktree(repo, path, None)  # 清掉上次留下的目录
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-f", "-B", name, str(path), start)
    return path, name


def commit_all(wt: Path, message: str) -> str | None:
    git(wt, "add", "-A")
    if git(wt, "status", "--porcelain") == "":
        return None
    git(wt, "commit", "-q", "--no-verify", "-m", message)
    return head(wt)


def diff_stat(cwd: Path, base: str, target: str = "HEAD") -> str:
    return git(cwd, "diff", "--stat", base, target)


def diff(cwd: Path, base: str, target: str = "HEAD", limit: int = 200_000) -> str:
    text = git(cwd, "diff", base, target)
    if len(text) > limit:
        return f"改动超过 {limit // 1000}KB，只给统计：\n" + diff_stat(cwd, base, target)
    return text


def changed_files(cwd: Path, base: str, target: str = "HEAD") -> list[str]:
    out = git(cwd, "diff", "--name-only", base, target)
    return out.splitlines() if out else []


@dataclass
class MergeResult:
    ok: bool
    conflicts: list[str]
    message: str


def merge(cwd: Path, ref: str, message: str) -> MergeResult:
    r = _run(cwd, ["merge", "--no-ff", "--no-edit", "-m", message, ref])
    if r.returncode == 0:
        return MergeResult(True, [], "")
    conflicts = git(cwd, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()
    git(cwd, "merge", "--abort", check=False)
    return MergeResult(False, conflicts, (r.stdout + r.stderr).strip())


def reset_hard(cwd: Path, sha: str) -> None:
    git(cwd, "reset", "--hard", "-q", sha)
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m pytest tests/test_gitops.py -q`
Expected: 7 passed

- [ ] **Step 5: 提交**
```bash
git add agentdesk/gitops.py tests/test_gitops.py
git commit -m "feat: worktree、提交、改动和合并的 git 封装"
```

### Task 5: Claude 指挥（拆任务、审查、总结）

**Files:**
- Create: `agentdesk/commander.py`, `tests/fakes/fake_claude.py`, `tests/test_commander.py`

**Interfaces:**
- Consumes: `AppConfig`、`Task`、`proc.run_io`
- Produces: `CommanderError(message, raw="")`（`.raw` 是 Claude 原文）；`extract_json(text) -> dict`；`check_acyclic(graph: dict[str, list[str]])`（有环抛 `ValueError`）；`validate_plan(obj, cfg) -> list[dict]`（每项含 `id title prompt agent model level files after accept problems`，`id`/`after` 转为字符串）；`validate_review(obj) -> dict`；`commander_argv(model) -> list[str]`；`Commander(argv, cwd, timeout_s=1800)`，方法 `plan(requirement, cfg) -> list[dict]`、`review(task, diff, outside) -> dict`、`summarize(items: list[str]) -> str`

- [ ] **Step 1: 写假 claude** `tests/fakes/fake_claude.py`：
```python
"""假 claude：按 AGENTDESK_FAKE_CLAUDE 指向的 JSON 文件依次返回预置回答，收到的提示追加到 <文件>.prompts。
文件内容是数组，每项是 {"result": "回答文字"} 或 {"exit": 退出码}。"""
import json
import os
import sys
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
path = Path(os.environ["AGENTDESK_FAKE_CLAUDE"])
prompt = sys.stdin.read()
with open(f"{path}.prompts", "a", encoding="utf-8") as f:
    f.write(prompt + "\n=====\n")
queue = json.loads(path.read_text(encoding="utf-8"))
item = queue.pop(0)
path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
if "exit" in item:
    print("boom", file=sys.stderr)
    sys.exit(item["exit"])
print(json.dumps({"type": "result", "is_error": False, "result": item["result"]}, ensure_ascii=False))
```

- [ ] **Step 2: 写失败的测试** `tests/test_commander.py`：
```python
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
```

- [ ] **Step 3: 运行，确认失败**

Run: `python -m pytest tests/test_commander.py -q`
Expected: FAIL，`No module named 'agentdesk.commander'`

- [ ] **Step 4: 实现** `agentdesk/commander.py`：
```python
"""Claude 指挥：拆任务、审查、总结。Claude 只读（--permission-mode plan），提示从标准输入传。"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from agentdesk.config import AppConfig
from agentdesk.proc import run_io


class CommanderError(Exception):
    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


PLAN_PROMPT = """你是开发任务的指挥。读当前仓库的代码，把下面的需求拆成可以交给其他编码 agent 独立完成的任务。

需求：
{requirement}

可用的执行者（名字: 档位 / 模型）：
{agents}
档位：read=只读分析，edit=只改代码，full=改代码并可联网。

规则：
- 每个任务只改一组文件，在 files 里列出相对路径；会改同一个文件的任务不能并行，后面的任务在 after 里写上前面任务的 id。
- prompt 要让执行者不看我们的对话也能独立完成：目标、原因、涉及的文件和函数、约束、验收标准。
- accept 是在仓库根目录执行的验收命令（例如测试命令），没有就写空字符串。
- 只输出一个 JSON 对象，不要其他文字，格式：
{{"tasks": [{{"id": 1, "title": "简短标题", "level": "edit", "agent": "执行者名字", "model": "模型名",
  "files": ["相对路径"], "after": [], "prompt": "任务说明", "accept": "验收命令"}}]}}"""

REVIEW_PROMPT = """你在审查一个编码 agent 完成的任务。只根据下面的材料判断是否合格，不要修改任何文件。

任务说明：
{prompt}

允许改动的文件：{files}
改到允许范围以外的文件：{outside}

验收命令：{accept}
验收退出码：{accept_code}
验收输出（末尾）：
{accept_output}

相对起点的改动：
{diff}

只输出一个 JSON 对象，不要其他文字：
{{"verdict": "pass 或 fail", "reasons": ["理由"], "retry_prompt": "不合格时给执行者的新任务说明（完整、能独立看懂）；合格时为空字符串"}}"""

SUMMARY_PROMPT = """下面是一组已经审查合格、即将合并的任务。用中文写一段给用户看的合并前总结：每个任务做了什么、谁做的、有哪些风险需要用户注意。直接输出总结文字。

{items}"""

FIX_SUFFIX = "\n\n上一次的回答不符合要求：{error}\n只输出符合格式的 JSON 对象，不要其他文字。"


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("回答里没有 JSON 对象")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("回答不是 JSON 对象")
    return obj


def check_acyclic(graph: dict[str, list[str]]) -> None:
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 1:
            raise ValueError(f"after 有循环依赖（任务 {node}）")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in graph.get(node, []):
            visit(dep)
        state[node] = 2

    for node in graph:
        visit(node)


def validate_plan(obj: dict, cfg: AppConfig) -> list[dict]:
    tasks = obj.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks 必须是非空数组")
    for t in tasks:
        if not isinstance(t, dict):
            raise ValueError("tasks 的每一项必须是对象")
        for key in ("id", "title", "prompt"):
            if t.get(key) in (None, ""):
                raise ValueError(f"任务缺少 {key}")
    ids = [str(t["id"]) for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("任务 id 重复")
    for t in tasks:
        t["after"] = [str(a) for a in t.get("after") or []]
        missing = [a for a in t["after"] if a not in ids]
        if missing:
            raise ValueError(f"任务 {t['id']} 的 after 引用了不存在的任务 {missing}")
    check_acyclic({str(t["id"]): t["after"] for t in tasks})
    out = []
    for t in tasks:
        agent_key, level, model = str(t.get("agent", "")), str(t.get("level", "edit")), str(t.get("model", ""))
        agent = cfg.agents.get(agent_key)
        problems = []
        if agent is None:
            problems.append(f"没有执行者 {agent_key}")
        else:
            if level not in agent.commands:
                problems.append(f"{agent.label} 不支持档位 {level}")
            if model not in agent.models:
                problems.append(f"{agent.label} 没有模型 {model}")
        out.append({"id": str(t["id"]), "title": str(t["title"]), "prompt": str(t["prompt"]), "agent": agent_key,
                    "model": model, "level": level, "files": [str(f) for f in t.get("files") or []],
                    "after": t["after"], "accept": str(t.get("accept") or ""), "problems": problems})
    return out


def validate_review(obj: dict) -> dict:
    if obj.get("verdict") not in ("pass", "fail"):
        raise ValueError("verdict 必须是 pass 或 fail")
    reasons = obj.get("reasons")
    if not isinstance(reasons, list):
        raise ValueError("reasons 必须是数组")
    retry = str(obj.get("retry_prompt") or "")
    if obj["verdict"] == "fail" and not retry.strip():
        raise ValueError("不合格时必须给出 retry_prompt")
    return {"verdict": obj["verdict"], "reasons": [str(r) for r in reasons], "retry_prompt": retry}


def commander_argv(model: str) -> list[str]:
    exe = shutil.which("claude") or "claude"
    return [exe, "-p", "--output-format", "json", "--permission-mode", "plan", "--model", model]


class Commander:
    def __init__(self, argv: list[str], cwd: Path, timeout_s: float = 1800):
        self.argv, self.cwd, self.timeout_s = argv, cwd, timeout_s

    def _call(self, prompt: str) -> str:
        try:
            code, out, err = run_io(self.argv, self.cwd, prompt, self.timeout_s)
        except TimeoutError as e:
            raise CommanderError(str(e)) from e
        except OSError as e:
            raise CommanderError(f"无法启动 claude：{e}") from e
        if code != 0:
            raise CommanderError(f"claude 退出码 {code}：{err.strip()[-500:]}", out)
        try:
            envelope = json.loads(out)
        except json.JSONDecodeError as e:
            raise CommanderError("claude 的输出不是 JSON", out) from e
        if envelope.get("is_error"):
            raise CommanderError(f"claude 报错：{envelope.get('result')}", out)
        return str(envelope.get("result", ""))

    def _ask_json(self, prompt: str, validate):
        text = self._call(prompt)
        try:
            return validate(extract_json(text))
        except ValueError as first:
            text = self._call(prompt + FIX_SUFFIX.format(error=first))
            try:
                return validate(extract_json(text))
            except ValueError as second:
                raise CommanderError(f"Claude 的回答格式不对：{second}", text) from second

    def plan(self, requirement: str, cfg: AppConfig) -> list[dict]:
        agents = "\n".join(f"- {a.key}: {'/'.join(a.levels())} / {', '.join(a.models)}" for a in cfg.agents.values())
        prompt = PLAN_PROMPT.format(requirement=requirement, agents=agents)
        return self._ask_json(prompt, lambda obj: validate_plan(obj, cfg))

    def review(self, task, diff: str, outside: list[str]) -> dict:
        prompt = REVIEW_PROMPT.format(
            prompt=task.prompt, files="、".join(task.files) or "未限定", outside="、".join(outside) or "无",
            accept=task.accept or "无", accept_code="无" if task.accept_code is None else task.accept_code,
            accept_output=task.accept_output[-4000:] or "无", diff=diff or "无改动")
        return self._ask_json(prompt, validate_review)

    def summarize(self, items: list[str]) -> str:
        return self._call(SUMMARY_PROMPT.format(items="\n".join(items))).strip()
```

- [ ] **Step 5: 运行，确认通过**

Run: `python -m pytest tests/test_commander.py -q`
Expected: 9 passed

- [ ] **Step 6: 提交**
```bash
git add agentdesk/commander.py tests/fakes/fake_claude.py tests/test_commander.py
git commit -m "feat: Claude 拆任务、审查、总结和返回格式校验"
```

### Task 6: 合并与撤销

**Files:**
- Create: `agentdesk/merger.py`, `tests/test_merger.py`

**Interfaces:**
- Consumes: `gitops`、`proc.run_capture`、`store.Task`
- Produces: `MergeError`；`OUTPUT_TAIL = 4000`；`order(tasks: list[Task]) -> list[Task]`（列表内按 `after` 拓扑排序，否则保持原顺序）；`merge_tasks(repo, tasks, test_command, timeout_s) -> dict`；`undo(repo, merge: dict)`
- 合并结果 dict 的格式（engine、server、界面都用）：
  - `state`: `"ok" | "test_failed" | "conflict" | "refused"`（engine 之后还会改成 `"undone"`、`"kept"`）
  - `message`: 说明文字；`merged`: 已合并的任务号列表；`pre` / `post`: 合并前后的 HEAD
  - `conflict_task`、`conflicts`: 冲突的任务号和文件；`test_code`、`test_output`: 全量测试结果

- [ ] **Step 1: 写失败的测试** `tests/test_merger.py`：
```python
import sys

import pytest

from agentdesk import gitops, merger
from agentdesk.store import Task

PY = sys.executable


def task(repo, tid, files, after=()):
    base = gitops.head(repo)
    wt, branch = gitops.create_worktree(repo, tid, base)
    for name, text in files.items():
        (wt / name).write_text(text, encoding="utf-8")
    commit = gitops.commit_all(wt, tid) or base
    return Task(id=tid, title=f"任务{tid}", prompt="p", agent="fake", model="m1", level="edit",
                after=list(after), base=base, branch=branch, worktree=str(wt), commit=commit)


def test_order_puts_dependencies_first():
    a = Task("t1", "a", "p", "fake", "m1", "edit", after=["t2"])
    b = Task("t2", "b", "p", "fake", "m1", "edit")
    c = Task("t3", "c", "p", "fake", "m1", "edit", after=["t9"])  # 依赖不在列表里时忽略
    assert [t.id for t in merger.order([a, b, c])] == ["t2", "t1", "t3"]


def test_merge_and_run_tests(repo):
    t1 = task(repo, "t1", {"a.txt": "a\n"})
    t2 = task(repo, "t2", {"b.txt": "b\n"})
    noop = task(repo, "t3", {})
    pre = gitops.head(repo)
    out = merger.merge_tasks(repo, [t1, t2, noop], f'"{PY}" -c "print(\'全量通过\')"', 60)
    assert out["state"] == "ok" and out["merged"] == ["t1", "t2", "t3"] and out["pre"] == pre
    assert out["post"] == gitops.head(repo) and out["test_code"] == 0 and "全量通过" in out["test_output"]
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    assert gitops.git(repo, "log", "-1", "--format=%s") == "agentdesk: 任务t2（t2）"


def test_refuses_when_main_worktree_dirty(repo):
    t1 = task(repo, "t1", {"a.txt": "a\n"})
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    out = merger.merge_tasks(repo, [t1], "", 60)
    assert out["state"] == "refused" and "未提交" in out["message"] and out["merged"] == []
    assert not (repo / "a.txt").exists()


def test_conflict_stops_and_leaves_repo_clean(repo):
    t1 = task(repo, "t1", {"README.md": "one\n"})
    t2 = task(repo, "t2", {"README.md": "two\n"})
    out = merger.merge_tasks(repo, [t1, t2], "", 60)
    assert out["state"] == "conflict" and out["merged"] == ["t1"]
    assert out["conflict_task"] == "t2" and out["conflicts"] == ["README.md"]
    assert gitops.is_clean(repo) and (repo / "README.md").read_text(encoding="utf-8") == "one\n"


def test_failed_tests_then_undo(repo):
    t1 = task(repo, "t1", {"a.txt": "a\n"})
    out = merger.merge_tasks(repo, [t1], f'"{PY}" -c "raise SystemExit(1)"', 60)
    assert out["state"] == "test_failed" and out["test_code"] == 1
    merger.undo(repo, out)
    assert gitops.head(repo) == out["pre"] and not (repo / "a.txt").exists()


def test_undo_refused_after_new_commit(repo):
    t1 = task(repo, "t1", {"a.txt": "a\n"})
    out = merger.merge_tasks(repo, [t1], f'"{PY}" -c "raise SystemExit(1)"', 60)
    (repo / "later.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "-m", "later")
    with pytest.raises(merger.MergeError, match="新提交"):
        merger.undo(repo, out)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_merger.py -q`
Expected: FAIL，`cannot import name 'merger'`

- [ ] **Step 3: 实现** `agentdesk/merger.py`：
```python
"""把任务分支依次合并进当前分支，跑全量测试；测试失败时可以撤销。"""
from __future__ import annotations

from pathlib import Path

from agentdesk import gitops
from agentdesk.proc import run_capture
from agentdesk.store import Task

OUTPUT_TAIL = 4000


class MergeError(Exception):
    pass


def order(tasks: list[Task]) -> list[Task]:
    by_id = {t.id: t for t in tasks}
    out: list[Task] = []
    seen: set[str] = set()

    def visit(t: Task) -> None:
        if t.id in seen:
            return
        seen.add(t.id)
        for dep in t.after:
            if dep in by_id:
                visit(by_id[dep])
        out.append(t)

    for t in tasks:
        visit(t)
    return out


def merge_tasks(repo: Path, tasks: list[Task], test_command: str, timeout_s: float) -> dict:
    if not gitops.is_clean(repo):
        return {"state": "refused", "message": "主工作区有未提交的改动，请先提交或清理后再合并", "merged": []}
    pre = gitops.head(repo)
    merged: list[str] = []
    for t in tasks:
        if t.commit and t.commit != t.base:
            r = gitops.merge(repo, t.branch, f"agentdesk: {t.title}（{t.id}）")
            if not r.ok:
                return {"state": "conflict", "message": r.message[-2000:], "merged": merged, "pre": pre,
                        "post": gitops.head(repo), "conflict_task": t.id, "conflicts": r.conflicts}
        merged.append(t.id)
    out = {"state": "ok", "message": "", "merged": merged, "pre": pre, "post": gitops.head(repo),
           "test_code": None, "test_output": ""}
    if test_command:
        code, text = run_capture(test_command, repo, timeout_s)
        out.update(test_code=code, test_output=text[-OUTPUT_TAIL:])
        if code != 0:
            out.update(state="test_failed", message="全量测试没有通过")
    return out


def undo(repo: Path, merge: dict) -> None:
    if gitops.head(repo) != merge["post"]:
        raise MergeError("合并之后当前分支又有了新提交，不能自动撤销，请自己处理")
    if not gitops.is_clean(repo):
        raise MergeError("主工作区有未提交的改动，不能撤销")
    gitops.reset_hard(repo, merge["pre"])
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m pytest tests/test_merger.py -q`
Expected: 6 passed

- [ ] **Step 5: 提交**
```bash
git add agentdesk/merger.py tests/test_merger.py
git commit -m "feat: 按依赖顺序合并、全量测试和撤销合并"
```

### Task 7: 调度引擎——手动任务

**Files:**
- Create: `agentdesk/engine.py`, `tests/test_engine_tasks.py`
- Modify: `tests/helpers.py`（加 `FakeCommander`、`status`）、`tests/conftest.py`（加 `make_engine` 夹具）

**Interfaces:**
- Consumes: Task 1–6 的全部接口
- Produces: `EngineError`；`Engine(repo, cfg, store, commander, emit=lambda event: None)`；方法 `start()`、`close()`、`recover()`、`create_task(title, prompt, agent, model, level, accept="") -> Task`、`stop(task_id)`、`discard(task_id)`、`retry(task_id, prompt, agent, model, level, keep_changes: bool)`、`review_task(task_id)`、`approve(task_id)`
- Produces（事件）：`emit({"type": "task", "task": <Task 的 dict>})`、`emit({"type": "log", "task": id, "line": 文本})`、`emit({"type": "error", "message": 文本})`
- Produces（给 Task 8 覆盖的钩子）：`_after_verdict(self, task: Task)`——任务得到审查结论、被确认合格或被丢弃后调用；本任务里只唤醒调度
- `commander` 只需要有 `plan(requirement, cfg)`、`review(task, diff, outside)`、`summarize(items)` 三个方法

- [ ] **Step 1: 补测试辅助**

在 `tests/helpers.py` 末尾追加：
```python
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
```

在 `tests/conftest.py` 末尾追加：
```python
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
```

- [ ] **Step 2: 写失败的测试** `tests/test_engine_tasks.py`：
```python
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
```

- [ ] **Step 3: 运行，确认失败**

Run: `python -m pytest tests/test_engine_tasks.py -q`
Expected: FAIL，`No module named 'agentdesk.engine'`

- [ ] **Step 4: 实现** `agentdesk/engine.py`：
```python
"""调度和流转：派活、并行与依赖、提交、验收、审查、重派、停止、合并。"""
from __future__ import annotations

import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from agentdesk import gitops
from agentdesk.commander import CommanderError
from agentdesk.config import AppConfig, ConfigError, build_command, decorate_prompt
from agentdesk.proc import Proc, RunResult, run_capture
from agentdesk.store import (DISCARDED, DONE, FAILED, INTERRUPTED, MERGED, PASSED, QUEUED, REJECTED,
                             REVIEWING, RUNNING, STOPPED, Requirement, Store, Task)

RETRYABLE = (FAILED, REJECTED, STOPPED, INTERRUPTED, DONE, PASSED)
OUTPUT_TAIL = 4000
INTERRUPTED_NOTE = "应用重启时这个任务还在运行；执行者进程可能还在后台，请在任务管理器里确认后再重派"


class EngineError(Exception):
    pass


class Engine:
    def __init__(self, repo: Path, cfg: AppConfig, store: Store, commander,
                 emit: Callable[[dict], None] = lambda event: None):
        self.repo, self.cfg, self.store, self.commander, self.emit = repo, cfg, store, commander, emit
        self._lock = threading.RLock()
        self._merge_lock = threading.Lock()
        self._procs: dict[str, Proc] = {}
        self._stop_requested: set[str] = set()
        self._wake = threading.Event()
        self._closed = False

    # ---------- 生命周期
    def start(self) -> None:
        self.recover()
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            proc.stop()

    def recover(self) -> None:
        """应用重启后：仍标着运行中的任务改为中断。不按记录的 pid 结束进程，pid 可能已被别的程序复用。"""
        for t in self.store.tasks():
            if t.status in (RUNNING, REVIEWING):
                self._set(t.id, status=INTERRUPTED, error=INTERRUPTED_NOTE, pid=None)

    # ---------- 小工具
    def _set(self, task_id: str, **changes) -> Task:
        with self._lock:
            t = self.store.get_task(task_id)
            for key, value in changes.items():
                setattr(t, key, value)
            self.store.save_task(t)
        self.emit({"type": "task", "task": asdict(t)})
        return t

    def _set_req(self, req_id: str, **changes) -> Requirement:
        with self._lock:
            r = self.store.get_requirement(req_id)
            for key, value in changes.items():
                setattr(r, key, value)
            self.store.save_requirement(r)
        self.emit({"type": "requirement", "requirement": asdict(r)})
        return r

    def _check(self, agent: str, level: str, model: str) -> None:
        a = self.cfg.agents.get(agent)
        if a is None:
            raise EngineError(f"没有执行者 {agent}")
        try:
            build_command(a, level, model)
        except ConfigError as e:
            raise EngineError(str(e)) from e

    def _timeout(self) -> float:
        return self.cfg.task_timeout_minutes * 60

    def _line(self, task_id: str, line: str) -> None:
        self.store.append_log(task_id, line + "\n")
        self.emit({"type": "log", "task": task_id, "line": line})

    def _fail(self, task_id: str, message: str) -> None:
        self._set(task_id, status=FAILED, error=message, pid=None)
        self._wake.set()

    def _cleanup(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        gitops.remove_worktree(self.repo, Path(t.worktree) if t.worktree else None, t.branch)
        self._set(task_id, worktree="", branch="")

    def _after_verdict(self, task: Task) -> None:
        self._wake.set()

    # ---------- 手动任务
    def create_task(self, title: str, prompt: str, agent: str, model: str, level: str, accept: str = "") -> Task:
        if not prompt.strip():
            raise EngineError("任务说明不能为空")
        self._check(agent, level, model)
        title = title.strip() or prompt.strip().splitlines()[0][:40]
        t = self.store.new_task(title=title, prompt=prompt, agent=agent, model=model, level=level,
                                accept=accept.strip(), status=QUEUED)
        self.emit({"type": "task", "task": asdict(t)})
        self._wake.set()
        return t

    def stop(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.status == QUEUED:
            self._set(task_id, status=STOPPED)
            return
        if t.status != RUNNING:
            raise EngineError("任务不在运行")
        with self._lock:
            proc = self._procs.get(task_id)
            if proc is None:
                self._stop_requested.add(task_id)  # 还在准备 worktree，启动后立刻停
        if proc is not None:
            proc.stop()

    def discard(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.status in (RUNNING, REVIEWING):
            raise EngineError("任务还在进行，先停止")
        if t.status == MERGED:
            raise EngineError("已合并的任务不能丢弃")
        self._cleanup(task_id)
        self._after_verdict(self._set(task_id, status=DISCARDED))

    def retry(self, task_id: str, prompt: str, agent: str, model: str, level: str, keep_changes: bool) -> None:
        t = self.store.get_task(task_id)
        if t.status not in RETRYABLE:
            raise EngineError("这个状态的任务不能重派")
        if not prompt.strip():
            raise EngineError("任务说明不能为空")
        self._check(agent, level, model)
        wt = Path(t.worktree) if t.worktree else None
        if keep_changes and wt is not None and wt.is_dir():
            stat = gitops.diff_stat(self.repo, t.base, t.commit) if t.commit else ""
            prompt = f"上一轮的改动已经在工作区里，在此基础上继续。改动统计：\n{stat or '（上一轮没有改动）'}\n\n{prompt}"
        else:
            self._cleanup(task_id)
            self._set(task_id, base="", commit="")
        self._set(task_id, prompt=prompt, agent=agent, model=model, level=level, status=QUEUED, review=None,
                  error="", exit_code=None, accept_code=None, accept_output="")
        self._wake.set()

    # ---------- 调度
    def _loop(self) -> None:
        while not self._closed:
            self._wake.wait(1.0)
            self._wake.clear()
            try:
                self._schedule()
            except Exception as e:  # 调度线程不能退出
                self.emit({"type": "error", "message": f"调度出错：{e}"})

    def _schedule(self) -> None:
        with self._lock:
            tasks = self.store.tasks()
            by_id = {t.id: t for t in tasks}
            running = sum(t.status == RUNNING for t in tasks)
            for t in tasks:
                if self._closed or running >= self.cfg.max_parallel:
                    break
                if t.status != QUEUED:
                    continue
                deps = [by_id.get(d) for d in t.after]
                if any(d is None or d.status not in (PASSED, MERGED) for d in deps):
                    continue
                self._set(t.id, status=RUNNING, error="", attempts=t.attempts + 1)
                running += 1
                threading.Thread(target=self._run, args=(t.id,), daemon=True).start()

    def _prepare_worktree(self, t: Task) -> Task:
        if t.worktree and Path(t.worktree).is_dir():
            return t  # 在已有改动上继续
        deps = [self.store.get_task(d) for d in t.after]
        unmerged = [d for d in deps if d.status == PASSED and d.commit]
        start = unmerged[0].commit if unmerged else gitops.head(self.repo)
        wt, branch = gitops.create_worktree(self.repo, t.id, start)
        for d in unmerged[1:]:
            r = gitops.merge(wt, d.commit, f"agentdesk: 带上前置任务 {d.id}")
            if not r.ok:
                raise EngineError(f"合并前置任务 {d.id} 时冲突：{'、'.join(r.conflicts)}")
        for cmd in self.cfg.worktree_setup:
            code, out = run_capture(cmd, wt, 300)
            if code != 0:
                raise EngineError(f"worktree 准备命令失败：{cmd}\n{out[-1000:]}")
        return self._set(t.id, worktree=str(wt), branch=branch, base=gitops.head(wt), commit="")

    def _run(self, task_id: str) -> None:
        try:
            t = self._prepare_worktree(self.store.get_task(task_id))
            agent = self.cfg.agents[t.agent]
            argv = build_command(agent, t.level, t.model)
            self.store.append_log(task_id, f"===== 第 {t.attempts} 次：{agent.label} / {t.model} / {t.level} =====\n")
            proc = Proc(argv, Path(t.worktree), decorate_prompt(t.level, t.prompt),
                        lambda line: self._line(task_id, line))
        except (ConfigError, EngineError, gitops.GitError, OSError, KeyError) as e:
            self._fail(task_id, str(e.args[0]) if isinstance(e, KeyError) else str(e))
            return
        with self._lock:
            self._procs[task_id] = proc
            stop_now = task_id in self._stop_requested
            self._stop_requested.discard(task_id)
        self._set(task_id, pid=proc.pid)
        if stop_now:
            proc.stop()
        result = proc.wait(self._timeout())
        with self._lock:
            self._procs.pop(task_id, None)
        try:
            self._after_run(task_id, result)
        except (gitops.GitError, OSError) as e:
            self._fail(task_id, f"收尾出错：{e}")

    def _after_run(self, task_id: str, result: RunResult) -> None:
        t = self.store.get_task(task_id)
        wt = Path(t.worktree)
        commit = gitops.commit_all(wt, f"agentdesk {t.id}: {t.title}") or gitops.head(wt)
        self._set(task_id, commit=commit, pid=None, exit_code=result.code)
        if result.stopped:
            self._set(task_id, status=STOPPED)
            self._wake.set()
            return
        if result.timed_out:
            self._fail(task_id, f"超过 {self.cfg.task_timeout_minutes:g} 分钟没有结束，已结束执行者进程")
            return
        if result.code != 0:
            self._fail(task_id, f"执行者退出码 {result.code}，详见实时输出")
            return
        if t.accept:
            code, out = run_capture(t.accept, wt, self._timeout())
            self._set(task_id, accept_code=code, accept_output=out[-OUTPUT_TAIL:])
        self._set(task_id, status=DONE)
        if t.requirement:
            self._review(task_id)
        else:
            self._wake.set()

    # ---------- 审查
    def review_task(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.status not in (DONE, REJECTED):
            raise EngineError("任务还没完成，不能审查")
        threading.Thread(target=self._review, args=(task_id,), daemon=True).start()

    def _review(self, task_id: str) -> None:
        t = self._set(task_id, status=REVIEWING, error="")
        try:
            changed = gitops.changed_files(self.repo, t.base, t.commit)
            outside = [f for f in changed if t.files and f not in t.files]
            verdict = self.commander.review(t, gitops.diff(self.repo, t.base, t.commit), outside)
        except (CommanderError, gitops.GitError) as e:
            t = self._set(task_id, status=DONE,
                          error=f"Claude 审查失败：{e}。请自己看改动后点“我确认合格”或重派")
        else:
            t = self._set(task_id, status=PASSED if verdict["verdict"] == "pass" else REJECTED, review=verdict)
        self._after_verdict(t)

    def approve(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.status not in (DONE, REJECTED):
            raise EngineError("只有待审查或不合格的任务可以确认合格")
        t = self._set(task_id, status=PASSED, error="",
                      review={"verdict": "pass", "reasons": ["用户确认合格"], "retry_prompt": ""})
        self._after_verdict(t)
```

- [ ] **Step 5: 运行，确认通过**

Run: `python -m pytest tests/test_engine_tasks.py -q`
Expected: 15 passed

- [ ] **Step 6: 全量回归并提交**

Run: `python -m pytest -q`
Expected: 全部通过
```bash
git add agentdesk/engine.py tests
git commit -m "feat: 调度引擎——手动任务的派出、并行、提交、验收、停止、重派、审查"
```

### Task 8: 调度引擎——Claude 指挥的需求流程

**Files:**
- Modify: `agentdesk/engine.py`（导入、`recover`、`_after_verdict`，新增需求相关方法）
- Create: `tests/test_engine_requirements.py`

**Interfaces:**
- Consumes: Task 7 的 `Engine`；`commander.check_acyclic`
- Produces: `submit_requirement(text) -> Requirement`、`add_draft(req_id, title, prompt, agent, model, level, accept="", files=(), after=()) -> Task`、`update_draft(task_id, **fields) -> Task`（只允许 `title prompt agent model level accept files after`）、`delete_draft(task_id)`、`start_requirement(req_id)`
- Produces（事件）：`emit({"type": "requirement", "requirement": <dict>})`、`emit({"type": "deleted", "task": id})`
- 需求状态流转：`planning → confirm_plan →（start）running →（全部合格或丢弃）confirm_merge →（Task 9 合并）merged`

- [ ] **Step 1: 写失败的测试** `tests/test_engine_requirements.py`：
```python
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
    assert "Claude 审查失败" in t.error
    wait_for(lambda: "Claude 审查失败" in e.store.get_task(t.id).error)
    e.approve(t.id)
    req_status(e, r.id, "confirm_merge")


def test_restart_during_planning_reopens_requirement(repo):
    store = Store(gitops.store_dir(repo))
    r = store.new_requirement("x")
    Engine(repo, fake_config(), store, FakeCommander()).recover()
    r = store.get_requirement(r.id)
    assert r.status == "confirm_plan" and "重启" in r.error
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_engine_requirements.py -q`
Expected: FAIL，`AttributeError: 'Engine' object has no attribute 'submit_requirement'`

- [ ] **Step 3: 实现**

`agentdesk/engine.py` 的导入改为：
```python
from agentdesk.commander import CommanderError, check_acyclic
from agentdesk.store import (CONFIRM_MERGE, CONFIRM_PLAN, DISCARDED, DONE, DRAFT, FAILED, IN_PROGRESS,
                             INTERRUPTED, MERGED, PASSED, PLANNING, QUEUED, REJECTED, REVIEWING, RUNNING,
                             STOPPED, Requirement, Store, Task)
```
在 `RETRYABLE` 下面加：
```python
DRAFT_FIELDS = {"title", "prompt", "agent", "model", "level", "accept", "files", "after"}
```

`recover` 方法末尾追加：
```python
        for r in self.store.requirements():
            if r.status == PLANNING:
                self._set_req(r.id, status=CONFIRM_PLAN,
                              error="应用重启时 Claude 还在拆任务，请重新提交需求或手动添加任务")
```

把 `_after_verdict` 整个替换为：
```python
    def _after_verdict(self, task: Task) -> None:
        if task.requirement:
            self._check_requirement(task.requirement)
        self._wake.set()

    def _check_requirement(self, req_id: str) -> None:
        """需求里的任务都合格（或丢弃、已合并）且至少一个合格时，写总结并进入合并确认。"""
        with self._lock:
            r = self.store.get_requirement(req_id)
            tasks = [self.store.get_task(i) for i in r.tasks]
            if r.status != IN_PROGRESS:
                return
            if not all(t.status in (PASSED, DISCARDED, MERGED) for t in tasks):
                return
            passed = [t for t in tasks if t.status == PASSED]
            if not passed:
                return
            self._set_req(req_id, status=CONFIRM_MERGE, summary="Claude 正在写总结…")
        items = []
        for t in passed:
            label = self.cfg.agents[t.agent].label if t.agent in self.cfg.agents else t.agent
            reasons = "；".join((t.review or {}).get("reasons", []))
            items.append(f"- {t.title}（{label} / {t.model}）：{reasons}\n{gitops.diff_stat(self.repo, t.base, t.commit)}")
        try:
            summary = self.commander.summarize(items)
        except CommanderError as e:
            summary = f"Claude 写总结失败：{e}"
        self._set_req(req_id, summary=summary)
```

在 `retry` 方法里、`self._wake.set()` 之前加（合并冲突后重派时，需求退回执行中）：
```python
        if t.requirement and self.store.get_requirement(t.requirement).status == CONFIRM_MERGE:
            self._set_req(t.requirement, status=IN_PROGRESS)
```

在 `# ---------- 调度` 之前新增：
```python
    # ---------- 需求（Claude 指挥）
    def submit_requirement(self, text: str) -> Requirement:
        if not text.strip():
            raise EngineError("需求不能为空")
        r = self.store.new_requirement(text.strip())
        self.emit({"type": "requirement", "requirement": asdict(r)})
        threading.Thread(target=self._plan, args=(r.id,), daemon=True).start()
        return r

    def _plan(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        try:
            planned = self.commander.plan(r.text, self.cfg)
        except CommanderError as e:
            self._set_req(req_id, status=CONFIRM_PLAN, plan_raw=e.raw,
                          error=f"Claude 拆任务失败：{e}。可以手动添加任务。")
            return
        id_map = {}
        for p in planned:
            t = self.store.new_task(title=p["title"], prompt=p["prompt"], agent=p["agent"], model=p["model"],
                                    level=p["level"], accept=p["accept"], files=p["files"],
                                    requirement=req_id, problems=p["problems"])
            id_map[p["id"]] = t.id
        for p in planned:
            self._set(id_map[p["id"]], after=[id_map[a] for a in p["after"]])
        self._set_req(req_id, status=CONFIRM_PLAN, tasks=list(id_map.values()), error="")

    def _draft(self, task_id: str) -> Task:
        t = self.store.get_task(task_id)
        if t.status != DRAFT:
            raise EngineError("只能修改还没开始的任务")
        return t

    def add_draft(self, req_id: str, title: str, prompt: str, agent: str, model: str, level: str,
                  accept: str = "", files=(), after=()) -> Task:
        r = self.store.get_requirement(req_id)
        if r.status != CONFIRM_PLAN:
            raise EngineError("只能在确认拆分时添加任务")
        title = title.strip() or (prompt.strip().splitlines() or ["新任务"])[0][:40]
        t = self.store.new_task(title=title, prompt=prompt, agent=agent, model=model, level=level,
                                accept=accept.strip(), files=list(files), after=list(after), requirement=req_id)
        self.emit({"type": "task", "task": asdict(t)})
        self._set_req(req_id, tasks=[*r.tasks, t.id])
        return t

    def update_draft(self, task_id: str, **fields) -> Task:
        bad = set(fields) - DRAFT_FIELDS
        if bad:
            raise EngineError(f"不能修改 {'、'.join(sorted(bad))}")
        self._draft(task_id)
        return self._set(task_id, problems=[], **fields)

    def delete_draft(self, task_id: str) -> None:
        t = self._draft(task_id)
        if t.requirement:
            r = self.store.get_requirement(t.requirement)
            for other in r.tasks:
                if other != task_id and task_id in self.store.get_task(other).after:
                    self._set(other, after=[a for a in self.store.get_task(other).after if a != task_id])
            self._set_req(r.id, tasks=[i for i in r.tasks if i != task_id])
        self.store.delete_task(task_id)
        self.emit({"type": "deleted", "task": task_id})

    def start_requirement(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        if r.status != CONFIRM_PLAN:
            raise EngineError("这个需求不在等待确认拆分")
        tasks = [self.store.get_task(i) for i in r.tasks]
        if not tasks:
            raise EngineError("还没有任务")
        errors = []
        for t in tasks:
            if not t.prompt.strip():
                errors.append(f"{t.title}：任务说明为空")
            try:
                self._check(t.agent, t.level, t.model)
            except EngineError as e:
                errors.append(f"{t.title}：{e}")
            unknown = [a for a in t.after if a not in r.tasks]
            if unknown:
                errors.append(f"{t.title}：依赖的任务不存在 {'、'.join(unknown)}")
        try:
            check_acyclic({t.id: t.after for t in tasks})
        except ValueError as e:
            errors.append(str(e))
        if errors:
            raise EngineError("；".join(errors))
        for t in tasks:
            self._set(t.id, status=QUEUED, problems=[])
        self._set_req(req_id, status=IN_PROGRESS, error="")
        self._wake.set()
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m pytest tests/test_engine_requirements.py tests/test_engine_tasks.py -q`
Expected: 全部通过（11 + 15）

- [ ] **Step 5: 提交**
```bash
git add agentdesk/engine.py tests/test_engine_requirements.py
git commit -m "feat: Claude 指挥的需求流程——拆任务、确认拆分、依赖调度、审查、进入合并确认"
```

### Task 9: 调度引擎——合并、撤销、保留

**Files:**
- Modify: `agentdesk/engine.py`（导入 `merger`、`REQ_MERGED`，新增合并方法）
- Create: `tests/test_engine_merge.py`

**Interfaces:**
- Consumes: `merger.order`、`merger.merge_tasks`、`merger.undo`、`merger.MergeError`
- Produces: `merge_task(task_id)`、`undo_task(task_id)`、`keep_task_merge(task_id)`（手动任务）；`merge_requirement(req_id)`、`undo_requirement(req_id)`、`keep_requirement_merge(req_id)`（需求）
- 规则：合并成功或冲突时，已合并任务的 worktree 和分支立即清理；全量测试失败时保留，等用户选"撤销"（回到合并前，任务退回合格/待审查）或"保留"（清理）；拒绝合并和冲突时把原因写进 `error`

- [ ] **Step 1: 写失败的测试** `tests/test_engine_merge.py`：
```python
import sys
from pathlib import Path

import pytest

from agentdesk import gitops
from agentdesk.engine import EngineError
from helpers import status, wait_for

PY = sys.executable
FAIL_TESTS = f'"{PY}" -c "raise SystemExit(1)"'


def done_task(e, prompt):
    return status(e, e.create_task("x", prompt, "fake", "m1", "edit").id, "done")


def test_manual_merge_brings_changes_and_cleans_up(make_engine, repo):
    e = make_engine()
    t = done_task(e, "WRITE a.txt one")
    e.merge_task(t.id)
    after = e.store.get_task(t.id)
    assert after.status == "merged" and after.merge["state"] == "ok"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    assert not Path(t.worktree).exists() and gitops.git(repo, "branch", "--list", t.branch) == ""
    assert gitops.diff(repo, after.base, after.commit).count("+one") == 1  # 合并后仍能看改动


def test_merge_refused_when_main_worktree_dirty(make_engine, repo):
    e = make_engine()
    t = done_task(e, "WRITE a.txt one")
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    e.merge_task(t.id)
    t = e.store.get_task(t.id)
    assert t.status == "done" and t.merge["state"] == "refused" and "未提交" in t.error
    assert not (repo / "a.txt").exists()


def test_conflict_is_aborted_and_task_can_be_redone(make_engine, repo):
    e = make_engine()
    a = done_task(e, "WRITE README.md one")
    b = done_task(e, "WRITE README.md two")
    e.merge_task(a.id)
    e.merge_task(b.id)
    b = e.store.get_task(b.id)
    assert b.status == "done" and b.merge["state"] == "conflict" and "README.md" in b.error
    assert gitops.is_clean(repo) and (repo / "README.md").read_text(encoding="utf-8") == "one\n"
    e.retry(b.id, "WRITE README.md two", "fake", "m1", "edit", keep_changes=False)
    status(e, b.id, "done")
    e.merge_task(b.id)
    assert (repo / "README.md").read_text(encoding="utf-8") == "two\n"


def test_failed_tests_keep_worktree_and_allow_undo_once(make_engine, repo):
    e = make_engine(test_command=FAIL_TESTS)
    t = done_task(e, "WRITE a.txt one")
    pre = gitops.head(repo)
    e.merge_task(t.id)
    t = e.store.get_task(t.id)
    assert t.status == "merged" and t.merge["state"] == "test_failed" and Path(t.worktree).exists()
    e.undo_task(t.id)
    t = e.store.get_task(t.id)
    assert t.status == "done" and t.merge["state"] == "undone"
    assert gitops.head(repo) == pre and not (repo / "a.txt").exists()
    with pytest.raises(EngineError):
        e.undo_task(t.id)


def test_undo_refused_after_new_commit(make_engine, repo):
    e = make_engine(test_command=FAIL_TESTS)
    t = done_task(e, "WRITE a.txt one")
    e.merge_task(t.id)
    (repo / "later.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "-m", "later")
    with pytest.raises(EngineError, match="新提交"):
        e.undo_task(t.id)


def test_keep_after_failed_tests_cleans_up(make_engine, repo):
    e = make_engine(test_command=FAIL_TESTS)
    t = done_task(e, "WRITE a.txt one")
    e.merge_task(t.id)
    e.keep_task_merge(t.id)
    after = e.store.get_task(t.id)
    assert after.merge["state"] == "kept" and not Path(t.worktree).exists() and (repo / "a.txt").exists()


def test_requirement_tasks_cannot_be_merged_one_by_one(make_engine):
    e = make_engine()
    e.commander.plans.append({"tasks": [{"id": 1, "title": "a", "agent": "fake", "model": "m1", "level": "edit",
                                         "prompt": "WRITE a.txt one"}]})
    rid = e.submit_requirement("x").id
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_plan")
    with pytest.raises(EngineError, match="整体合并"):
        e.merge_task(e.store.get_requirement(rid).tasks[0])


def test_requirement_merge_in_dependency_order_then_undo(make_engine, repo):
    e = make_engine(test_command=FAIL_TESTS)
    e.commander.plans.append({"tasks": [
        {"id": 2, "title": "二", "agent": "fake", "model": "m1", "level": "edit", "after": [1],
         "prompt": "WRITE b.txt two"},
        {"id": 1, "title": "一", "agent": "fake", "model": "m1", "level": "edit", "prompt": "WRITE a.txt one"},
    ]})
    rid = e.submit_requirement("两步").id
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_plan")
    e.start_requirement(rid)
    wait_for(lambda: e.store.get_requirement(rid).status == "confirm_merge")
    pre = gitops.head(repo)
    e.merge_requirement(rid)
    r = e.store.get_requirement(rid)
    assert r.status == "merged" and r.merge["state"] == "test_failed"
    first, second = (e.store.get_task(i) for i in r.merge["merged"])
    assert (first.title, second.title) == ("一", "二")
    assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
    e.undo_requirement(rid)
    r = e.store.get_requirement(rid)
    assert r.status == "confirm_merge" and gitops.head(repo) == pre
    assert all(e.store.get_task(i).status == "passed" for i in r.tasks)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_engine_merge.py -q`
Expected: FAIL，`AttributeError: 'Engine' object has no attribute 'merge_task'`

- [ ] **Step 3: 实现**

`agentdesk/engine.py` 导入改为：
```python
from agentdesk import gitops, merger
```
并在 store 导入里加上 `REQ_MERGED`。

在文件末尾（`approve` 之后）新增：
```python
    # ---------- 合并
    def merge_task(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        if t.requirement:
            raise EngineError("需求里的任务要在需求页整体合并")
        if t.status not in (DONE, PASSED):
            raise EngineError("只有待审查或合格的任务可以合并")
        with self._merge_lock:
            outcome = merger.merge_tasks(self.repo, [t], self.cfg.test_command, self._timeout())
            self._apply_merge([t], outcome)
        extra = {"error": outcome["message"]} if outcome["state"] == "refused" else {}
        self._set(task_id, merge=outcome, **extra)

    def merge_requirement(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        if r.status != CONFIRM_MERGE:
            raise EngineError("这个需求还没到合并这一步")
        tasks = merger.order([t for t in map(self.store.get_task, r.tasks) if t.status == PASSED])
        with self._merge_lock:
            outcome = merger.merge_tasks(self.repo, tasks, self.cfg.test_command, self._timeout())
            self._apply_merge(tasks, outcome)
        if outcome["state"] in ("ok", "test_failed"):
            self._set_req(req_id, status=REQ_MERGED, merge=outcome, error="")
        elif outcome["state"] == "refused":
            self._set_req(req_id, merge=outcome, error=outcome["message"])
        else:
            self._set_req(req_id, merge=outcome,
                          error="合并冲突，已恢复原状；冲突的任务可以选“从头重做”在最新代码上重派")

    def _apply_merge(self, tasks: list[Task], outcome: dict) -> None:
        for t in tasks:
            if t.id in outcome["merged"]:
                self._set(t.id, status=MERGED, error="")
                if outcome["state"] != "test_failed":
                    self._cleanup(t.id)
            elif t.id == outcome.get("conflict_task"):
                self._set(t.id, error=f"合并冲突：{'、'.join(outcome['conflicts'])}。"
                                      "可以选“从头重做”在最新代码上重派")

    def _undo(self, merge: dict | None, task_ids: list[str], back_to: str) -> None:
        if not merge or merge.get("state") != "test_failed":
            raise EngineError("只有全量测试失败的合并可以撤销")
        try:
            merger.undo(self.repo, merge)
        except merger.MergeError as e:
            raise EngineError(str(e)) from e
        for task_id in task_ids:
            self._set(task_id, status=back_to)

    def _keep(self, merge: dict | None, task_ids: list[str]) -> None:
        if not merge or merge.get("state") != "test_failed":
            raise EngineError("没有等待处理的合并")
        for task_id in task_ids:
            self._cleanup(task_id)

    def undo_task(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        self._undo(t.merge, [task_id], DONE)
        self._set(task_id, merge={**t.merge, "state": "undone"})

    def keep_task_merge(self, task_id: str) -> None:
        t = self.store.get_task(task_id)
        self._keep(t.merge, [task_id])
        self._set(task_id, merge={**t.merge, "state": "kept"})

    def undo_requirement(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        self._undo(r.merge, (r.merge or {}).get("merged", []), PASSED)
        self._set_req(req_id, status=CONFIRM_MERGE, merge={**r.merge, "state": "undone"})

    def keep_requirement_merge(self, req_id: str) -> None:
        r = self.store.get_requirement(req_id)
        self._keep(r.merge, (r.merge or {}).get("merged", []))
        self._set_req(req_id, merge={**r.merge, "state": "kept"})
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m pytest -q`
Expected: 全部通过

- [ ] **Step 5: 提交**
```bash
git add agentdesk/engine.py tests/test_engine_merge.py
git commit -m "feat: 合并进当前分支、全量测试失败时撤销或保留"
```

### Task 10: 本地服务

**Files:**
- Create: `agentdesk/server.py`, `tests/test_server.py`
- Create: `agentdesk/web/index.html`（本任务先放一个最小页面，Task 11 再写完整界面）
- Modify: `.gitignore`（加 `recent.json`）

**Interfaces:**
- Consumes: `Engine` 的全部公开方法；`load_config`、`Store`、`gitops.repo_root/store_dir/branch`、`Commander`、`commander_argv`、`run_io`
- Produces: `Bus`（`publish(event)`、`subscribe() -> Queue`、`unsubscribe(q)`）；`App(app_dir, make_commander=None)`，属性 `token`、`bus`、`engines`、`current`，方法 `open_project(path) -> Engine`、`recent() -> list[str]`、`state() -> dict`；`make_server(app, port=0) -> ThreadingHTTPServer`
- HTTP 接口（除 `/static/*` 外都要口令；POST 体和返回都是 JSON；出错返回 400 `{"error": 中文说明}`）：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/?t=口令` | 界面 |
| GET | `/static/<文件>` | `web/` 下的静态文件 |
| GET | `/api/state` | 项目、分支、执行者（含检测结果 `ok`/`reason`）、档位、需求、任务、最近项目 |
| GET | `/api/events?t=口令` | SSE：引擎事件（带 `project` 字段）和 `{"type": "reload"}` |
| GET | `/api/tasks/<id>/log`、`/diff` | 纯文本 |
| POST | `/api/open` `{path}` | 打开项目 |
| POST | `/api/requirements` `{text}` | 提交需求 |
| POST | `/api/requirements/<id>/tasks` | 加草稿任务 |
| POST | `/api/requirements/<id>/start` `merge` `undo` `keep` | 需求操作 |
| POST | `/api/tasks` `{title, prompt, agent, model, level, accept}` | 手动派活 |
| POST | `/api/tasks/<id>/update` `delete` `stop` `discard` `review` `approve` `merge` `undo` `keep` | 任务操作 |
| POST | `/api/tasks/<id>/retry` `{prompt, agent, model, level, keep}` | 重派 |

- [ ] **Step 1: 写失败的测试** `tests/test_server.py`：
```python
import http.client
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agentdesk import gitops
from agentdesk.server import App, make_server
from helpers import FAKES, FakeCommander, wait_for

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 公司代理不能拦 127.0.0.1


@pytest.fixture
def server(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    py, fa = Path(sys.executable).as_posix(), (FAKES / "fake_agent.py").as_posix()
    (app_dir / "agents.toml").write_text(f'''max_parallel = 2
[agents.fake]
label = "Fake"
models = ["m1"]
[agents.fake.commands]
read = ["{py}", "{fa}", "--model", "{{model}}"]
edit = ["{py}", "{fa}", "--model", "{{model}}"]
''', encoding="utf-8")
    app = App(app_dir, make_commander=lambda repo, cfg: FakeCommander())
    srv = make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    for engine in app.engines.values():
        engine.close()


def call(server, path, body=None, token=True):
    app, base = server
    headers = {"Content-Type": "application/json", **({"X-Token": app.token} if token else {})}
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        with NO_PROXY.open(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def state(server):
    return json.loads(call(server, "/api/state")[1])


def test_token_is_required(server):
    assert call(server, "/api/state", token=False)[0] == 403
    assert call(server, "/api/open", {"path": "x"}, token=False)[0] == 403


def test_static_files_are_public_without_traversal(server):
    assert call(server, "/static/index.html", token=False)[0] == 200
    assert call(server, "/static/..%2Fserver.py", token=False)[0] == 404


def test_state_before_any_project(server):
    s = state(server)
    assert s["project"] is None and s["recent"] == [] and s["levels"]["edit"] == "改代码"


def test_open_project_lists_agents_and_remembers_it(server, repo):
    assert call(server, "/api/open", {"path": str(repo)})[0] == 200
    s = state(server)
    assert s["project"] == str(gitops.repo_root(repo)) and s["branch"] == "main"
    assert s["agents"][0]["key"] == "fake" and s["agents"][0]["levels"] == ["read", "edit"]
    assert wait_for(lambda: state(server)["agents"][0]["ok"] is True)
    assert s["recent"] == [str(gitops.repo_root(repo))]


def test_open_non_repo_gives_chinese_error(server, tmp_path):
    code, body = call(server, "/api/open", {"path": str(tmp_path / "nope")})
    assert code == 400 and "不是 git 仓库" in json.loads(body)["error"]


def test_manual_task_through_api_with_log_and_diff(server, repo):
    call(server, "/api/open", {"path": str(repo)})
    code, body = call(server, "/api/tasks", {"title": "t", "prompt": "WRITE a.txt 你好", "agent": "fake",
                                             "model": "m1", "level": "edit", "accept": ""})
    assert code == 200
    tid = json.loads(body)["id"]
    wait_for(lambda: next(t for t in state(server)["tasks"] if t["id"] == tid)["status"] == "done")
    assert "> WRITE a.txt 你好" in call(server, f"/api/tasks/{tid}/log")[1]
    assert "+你好" in call(server, f"/api/tasks/{tid}/diff")[1]


def test_bad_request_returns_400_with_message(server, repo):
    assert call(server, "/api/tasks", {"prompt": "p", "agent": "fake", "model": "m1", "level": "edit"})[0] == 400
    call(server, "/api/open", {"path": str(repo)})
    code, body = call(server, "/api/tasks", {"prompt": "p", "agent": "nope", "model": "m1", "level": "edit"})
    assert code == 400 and "没有执行者 nope" in json.loads(body)["error"]
    code, body = call(server, "/api/tasks/t0099/stop", {})
    assert code == 400 and "没有任务 t0099" in json.loads(body)["error"]


def test_events_stream_reports_task_changes(server, repo):
    app, base = server
    call(server, "/api/open", {"path": str(repo)})
    conn = http.client.HTTPConnection("127.0.0.1", int(base.rsplit(":", 1)[1]), timeout=10)
    conn.request("GET", f"/api/events?t={app.token}")
    resp = conn.getresponse()
    assert resp.status == 200
    call(server, "/api/tasks", {"prompt": "PRINT hi", "agent": "fake", "model": "m1", "level": "read"})
    for _ in range(200):
        line = resp.fp.readline().decode("utf-8")
        if line.startswith("data:") and json.loads(line[5:])["type"] == "task":
            break
    else:
        pytest.fail("没收到任务事件")
    conn.close()
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_server.py -q`
Expected: FAIL，`No module named 'agentdesk.server'`

- [ ] **Step 3: 实现**

`.gitignore` 加一行：
```
recent.json
```

`agentdesk/web/index.html`（占位，Task 11 替换）：
```html
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>Agent 指挥台</title></head><body>Agent 指挥台</body></html>
```

`agentdesk/server.py`：
```python
"""本地 HTTP 服务：只听 127.0.0.1，口令校验，JSON 接口和 SSE 推送。"""
from __future__ import annotations

import json
import queue
import re
import secrets
import shutil
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentdesk import gitops, merger
from agentdesk.commander import Commander, CommanderError, commander_argv
from agentdesk.config import LEVELS, AppConfig, ConfigError, load_config
from agentdesk.engine import Engine, EngineError
from agentdesk.proc import run_io
from agentdesk.store import Store

WEB = Path(__file__).resolve().parent / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8"}
USER_ERRORS = (EngineError, ConfigError, CommanderError, gitops.GitError, merger.MergeError, KeyError, ValueError)


class Bus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # 页面太久没读，丢掉；页面重连后会整体刷新

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=10000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)


class App:
    """当前打开的项目和它的引擎。切换项目时保留已打开项目的引擎，后台任务继续跑。"""

    def __init__(self, app_dir: Path, make_commander=None):
        self.app_dir = app_dir
        self.token = secrets.token_urlsafe(16)
        self.bus = Bus()
        self.engines: dict[Path, Engine] = {}
        self.current: Engine | None = None
        self.probes: dict[str, dict] = {}
        self._make_commander = make_commander or (
            lambda repo, cfg: Commander(commander_argv(cfg.commander_model), repo))
        self._lock = threading.Lock()

    def recent(self) -> list[str]:
        path = self.app_dir / "recent.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def _remember(self, repo: Path) -> None:
        items = [str(repo)] + [p for p in self.recent() if p != str(repo)]
        (self.app_dir / "recent.json").write_text(json.dumps(items[:10], ensure_ascii=False), encoding="utf-8")

    def open_project(self, path: str) -> Engine:
        try:
            repo = gitops.repo_root(Path(path.strip().strip('"')))
        except (gitops.GitError, OSError) as e:
            raise EngineError(f"{path} 不是 git 仓库") from e
        with self._lock:
            engine = self.engines.get(repo)
            if engine is None:
                cfg = load_config(self.app_dir / "agents.toml", repo)
                engine = Engine(repo, cfg, Store(gitops.store_dir(repo)), self._make_commander(repo, cfg),
                                lambda event, r=repo: self.bus.publish({**event, "project": str(r)}))
                engine.start()
                self.engines[repo] = engine
                threading.Thread(target=self._probe, args=(cfg,), daemon=True).start()
            self.current = engine
        self._remember(repo)
        self.bus.publish({"type": "reload"})
        return engine

    def _probe(self, cfg: AppConfig) -> None:
        def one(key: str, first_command: list[str]) -> None:
            exe = shutil.which(first_command[0])
            if exe is None:
                result = {"ok": False, "reason": f"找不到命令 {first_command[0]}"}
            else:
                try:
                    code, out, err = run_io([exe, "--version"], self.app_dir, "", 120)
                    result = {"ok": code == 0, "reason": "" if code == 0 else (err or out).strip()[-200:]}
                except (TimeoutError, OSError) as e:
                    result = {"ok": False, "reason": str(e)}
            self.probes[key] = result
            self.bus.publish({"type": "reload"})

        for key, agent in cfg.agents.items():
            self.probes.setdefault(key, {"ok": None, "reason": "检测中"})
            threading.Thread(target=one, args=(key, next(iter(agent.commands.values()))), daemon=True).start()

    def state(self) -> dict:
        base = {"recent": self.recent(), "levels": LEVELS}
        e = self.current
        if e is None:
            return {**base, "project": None}
        agents = [{"key": a.key, "label": a.label, "models": a.models, "default_model": a.default_model,
                   "levels": a.levels(), **self.probes.get(a.key, {"ok": None, "reason": "检测中"})}
                  for a in e.cfg.agents.values()]
        return {**base, "project": str(e.repo), "branch": gitops.branch(e.repo), "max_parallel": e.cfg.max_parallel,
                "agents": agents, "requirements": [asdict(r) for r in e.store.requirements()],
                "tasks": [asdict(t) for t in e.store.tasks()]}


def _message(e: Exception) -> str:
    return str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)


class Handler(BaseHTTPRequestHandler):
    app: App  # make_server 里绑定

    def log_message(self, *args) -> None:
        pass

    def _authorized(self) -> bool:
        given = self.headers.get("X-Token") or (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
        return secrets.compare_digest(given, self.app.token)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _text(self, text: str) -> None:
        self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/static/"):
            f = WEB / path.removeprefix("/static/")
            if f.parent == WEB and f.is_file():
                return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
            return self._send(404, b"", "text/plain")
        if not self._authorized():
            return self._send(403, "口令不对，请从启动窗口打开".encode("utf-8"), "text/plain; charset=utf-8")
        if path == "/":
            return self._send(200, (WEB / "index.html").read_bytes(), TYPES[".html"])
        if path == "/api/state":
            return self._json(200, self.app.state())
        if path == "/api/events":
            return self._events()
        m = re.fullmatch(r"/api/tasks/(t\d+)/(log|diff)", path)
        if m and self.app.current is not None:
            e = self.app.current
            try:
                if m[2] == "log":
                    return self._text(e.store.read_log(m[1]))
                t = e.store.get_task(m[1])
                return self._text(gitops.diff(e.repo, t.base, t.commit) if t.base and t.commit else "")
            except USER_ERRORS as err:
                return self._json(400, {"error": _message(err)})
        self._send(404, b"", "text/plain")

    def do_POST(self) -> None:
        if not self._authorized():
            return self._json(403, {"error": "口令不对"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            result = self._post(urlparse(self.path).path, body)
        except USER_ERRORS as e:
            return self._json(400, {"error": _message(e)})
        self._json(200, result if result is not None else {"ok": True})

    def _post(self, path: str, b: dict):
        app = self.app
        if path == "/api/open":
            app.open_project(b["path"])
            return None
        e = app.current
        if e is None:
            raise EngineError("请先打开一个项目")
        if path == "/api/requirements":
            return asdict(e.submit_requirement(b["text"]))
        if path == "/api/tasks":
            return asdict(e.create_task(b.get("title", ""), b["prompt"], b["agent"], b["model"], b["level"],
                                        b.get("accept", "")))
        m = re.fullmatch(r"/api/requirements/(r\d+)/(start|merge|undo|keep|tasks)", path)
        if m:
            req_id, action = m.groups()
            if action == "tasks":
                return asdict(e.add_draft(req_id, b.get("title", ""), b["prompt"], b["agent"], b["model"],
                                          b["level"], b.get("accept", ""), b.get("files", []), b.get("after", [])))
            {"start": e.start_requirement, "merge": e.merge_requirement, "undo": e.undo_requirement,
             "keep": e.keep_requirement_merge}[action](req_id)
            return None
        m = re.fullmatch(r"/api/tasks/(t\d+)/(update|delete|stop|discard|review|approve|merge|undo|keep|retry)", path)
        if m:
            task_id, action = m.groups()
            if action == "update":
                return asdict(e.update_draft(task_id, **b))
            if action == "retry":
                e.retry(task_id, b["prompt"], b["agent"], b["model"], b["level"], bool(b.get("keep")))
                return None
            getattr(e, {"delete": "delete_draft", "stop": "stop", "discard": "discard", "review": "review_task",
                        "approve": "approve", "merge": "merge_task", "undo": "undo_task",
                        "keep": "keep_task_merge"}[action])(task_id)
            return None
        raise EngineError("没有这个接口")

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = self.app.bus.subscribe()
        try:
            while True:
                try:
                    data = f"data: {json.dumps(q.get(timeout=15), ensure_ascii=False)}\n\n"
                except queue.Empty:
                    data = ": ping\n\n"
                self.wfile.write(data.encode("utf-8"))
                self.wfile.flush()
        except OSError:
            pass  # 页面关了
        finally:
            self.app.bus.unsubscribe(q)


def make_server(app: App, port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m pytest tests/test_server.py -q`
Expected: 8 passed

- [ ] **Step 5: 提交**
```bash
git add .gitignore agentdesk/server.py agentdesk/web/index.html tests/test_server.py
git commit -m "feat: 本地服务——口令、JSON 接口、SSE 推送、项目切换和执行者检测"
```

### Task 11: 界面

**Files:**
- Modify: `agentdesk/web/index.html`（替换占位页）
- Create: `agentdesk/web/app.js`, `agentdesk/web/style.css`
- Modify: `tests/test_server.py`（加一个界面资源的冒烟测试）

**Interfaces:**
- Consumes: Task 10 的 HTTP 接口和 `state()` 结构（`project branch max_parallel agents requirements tasks recent levels`）；SSE 事件 `task requirement deleted log error reload`
- 界面没有自动化 UI 测试；行为在 Task 12 的走查里逐项手动验证

- [ ] **Step 1: 写失败的测试**，在 `tests/test_server.py` 末尾追加：
```python
def test_ui_page_and_assets_are_served(server):
    app, _ = server
    code, html = call(server, f"/?t={app.token}", token=False)
    assert code == 200 and "/static/app.js" in html and "新需求给 Claude" in html
    code, js = call(server, "/static/app.js", token=False)
    assert code == 200 and "EventSource" in js
    assert call(server, "/static/style.css", token=False)[0] == 200
```

Run: `python -m pytest tests/test_server.py::test_ui_page_and_assets_are_served -q`
Expected: FAIL（占位页里没有 `/static/app.js`）

- [ ] **Step 2: 写页面** `agentdesk/web/index.html`：
```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Agent 指挥台</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header>
  <input id="project" list="recent" placeholder="项目目录，例如 D:\code\my-repo" size="48">
  <datalist id="recent"></datalist>
  <button id="open">打开</button>
  <span id="branch"></span>
  <span class="grow"></span>
  <button id="new-req" class="primary">＋ 新需求给 Claude</button>
  <button id="new-task">＋ 手动派活</button>
  <span id="running"></span>
</header>
<main>
  <nav id="list"></nav>
  <section id="detail"></section>
</main>
<dialog id="dlg"></dialog>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: 写样式** `agentdesk/web/style.css`：
```css
* { box-sizing: border-box; }
body { margin: 0; font: 14px "Microsoft YaHei UI", system-ui, sans-serif; color: #222; background: #f6f7f9; height: 100vh; display: flex; flex-direction: column; }
header { display: flex; gap: 8px; align-items: center; padding: 8px 12px; background: #fff; border-bottom: 1px solid #ddd; }
.grow { flex: 1; }
main { flex: 1; display: flex; min-height: 0; }
nav { width: 300px; overflow: auto; border-right: 1px solid #ddd; background: #fff; }
section { flex: 1; overflow: auto; padding: 12px 16px; }
.req { border-bottom: 1px solid #eee; padding: 4px 0; }
.head { padding: 6px 10px; color: #666; }
.item { padding: 6px 10px; cursor: pointer; }
.item:hover { background: #eef3ff; }
.item.sel { background: #dbe6ff; }
.item small { color: #666; }
.req > .item + .item { padding-left: 24px; }
button { padding: 4px 10px; border: 1px solid #bbb; border-radius: 4px; background: #fff; cursor: pointer; }
button.primary { background: #2f6fed; color: #fff; border-color: #2f6fed; }
button.on { background: #dbe6ff; }
.err { color: #c62828; white-space: pre-wrap; }
.state { color: #555; }
.actions { margin: 8px 0; }
.tabs { margin-top: 8px; display: flex; gap: 4px; }
pre { background: #fff; border: 1px solid #ddd; padding: 8px; white-space: pre-wrap; word-break: break-all; max-height: 60vh; overflow: auto; }
table.plan { border-collapse: collapse; width: 100%; }
table.plan td, table.plan th { border: 1px solid #ddd; padding: 4px; vertical-align: top; }
table.plan textarea, dialog textarea { width: 100%; }
dialog { width: 640px; border: 1px solid #bbb; border-radius: 6px; }
dialog label { display: block; margin: 6px 0; }
menu { display: flex; gap: 8px; justify-content: flex-end; padding: 0; }
```

- [ ] **Step 4: 写脚本** `agentdesk/web/app.js`：
```javascript
// agentdesk 界面：拉 /api/state 整体重绘，监听 /api/events；实时输出单独追加，不整体重绘。
const token = new URLSearchParams(location.search).get("t");
let S = null;          // 最近一次 /api/state
let sel = null;        // {kind: "req" | "task", id}
let tab = "log";       // 任务详情的页签
let dirty = false;     // 用户正在输入时推迟刷新

const $ = (q) => document.querySelector(q);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const split = (s) => (s || "").split(/[,，\s]+/).filter(Boolean);

const STATUS = { draft: "草稿", queued: "排队", running: "运行中", done: "待审查", reviewing: "审查中", passed: "合格", rejected: "不合格", failed: "失败", stopped: "已停止", interrupted: "中断", discarded: "已丢弃", merged: "已合并" };
const ICON = { draft: "✎", queued: "⏳", running: "▶", done: "👀", reviewing: "🔍", passed: "✅", rejected: "❌", failed: "⚠", stopped: "■", interrupted: "⚡", discarded: "🗑", merged: "🔀" };
const REQ = { planning: "Claude 正在拆任务…", confirm_plan: "⏸ 等你确认：任务拆分", running: "执行中", confirm_merge: "⏸ 等你确认：合并", merged: "已合并" };
const MERGE = { ok: "合并完成", test_failed: "已合并，但全量测试没通过", conflict: "合并冲突，已恢复原状", refused: "没有合并", undone: "已撤销合并", kept: "已保留合并" };

async function api(path, body) {
  const opt = body === undefined
    ? { headers: { "X-Token": token } }
    : { method: "POST", headers: { "X-Token": token, "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = (r.headers.get("Content-Type") || "").includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data.error || data);
  return data;
}
async function act(path, body) {
  try { await api(path, body ?? {}); await refresh(); } catch (e) { alert(e.message); }
}
async function refresh() { S = await api("/api/state"); render(); }

const agentOf = (key) => (S.agents || []).find((a) => a.key === key);
const task = (id) => S.tasks.find((t) => t.id === id);
const req = (id) => S.requirements.find((r) => r.id === id);

function agentSelect(value) {
  return `<select name="agent">${S.agents.map((a) => `<option value="${a.key}" ${a.key === value ? "selected" : ""} ${a.ok === false ? "disabled" : ""}>${esc(a.label)}${a.ok === false ? "（不可用：" + esc(a.reason) + "）" : a.ok === null ? "（检测中）" : ""}</option>`).join("")}</select>`;
}
function modelSelect(agentKey, value) {
  return `<select name="model">${(agentOf(agentKey)?.models || []).map((m) => `<option ${m === value ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>`;
}
function levelSelect(agentKey, value) {
  const a = agentOf(agentKey);
  return `<select name="level">${Object.entries(S.levels).map(([k, v]) => `<option value="${k}" ${k === value ? "selected" : ""} ${a && !a.levels.includes(k) ? "disabled" : ""}>${v}</option>`).join("")}</select>`;
}
function taskFields(t) {
  const usable = S.agents.find((a) => a.ok !== false) || S.agents[0];
  const agent = t.agent || usable.key;
  return `<label>执行者 ${agentSelect(agent)}</label>
    <label>模型 ${modelSelect(agent, t.model || agentOf(agent).default_model)}</label>
    <label>权限 ${levelSelect(agent, t.level || "edit")}</label>
    <label>说明<textarea name="prompt" rows="10">${esc(t.prompt || "")}</textarea></label>`;
}

function render() {
  $("#project").value = S.project || $("#project").value;
  $("#recent").innerHTML = S.recent.map((p) => `<option value="${esc(p)}">`).join("");
  $("#branch").textContent = S.project ? `分支：${S.branch}` : "请先打开项目";
  $("#running").textContent = S.project ? `正在跑 ${S.tasks.filter((t) => t.status === "running").length} / 最多同时 ${S.max_parallel}` : "";
  renderList();
  renderDetail();
}

function renderList() {
  if (!S.project) { $("#list").innerHTML = ""; return; }
  const item = (t) => `<div class="item ${sel?.id === t.id ? "sel" : ""}" data-task="${t.id}">${ICON[t.status]} ${esc(t.title)} <small>${esc(agentOf(t.agent)?.label || t.agent)}</small></div>`;
  let html = S.requirements.slice().reverse().map((r) => `<div class="req">
      <div class="item ${sel?.id === r.id ? "sel" : ""}" data-req="${r.id}"><b>${esc(r.text.slice(0, 40))}</b><br><small>${REQ[r.status]}</small></div>
      ${r.tasks.map(task).filter(Boolean).map(item).join("")}</div>`).join("");
  const manual = S.tasks.filter((t) => !t.requirement);
  if (manual.length) html += `<div class="req"><div class="head">手动任务</div>${manual.slice().reverse().map(item).join("")}</div>`;
  $("#list").innerHTML = html;
}

function renderDetail() {
  const box = $("#detail");
  if (!S.project) { box.innerHTML = "<p>在上方输入或选择一个 git 仓库目录，点“打开”。</p>"; return; }
  if (sel?.kind === "req" && req(sel.id)) { box.innerHTML = reqView(req(sel.id)); return; }
  if (sel?.kind === "task" && task(sel.id)) { box.innerHTML = taskView(task(sel.id)); loadTab(); return; }
  box.innerHTML = "<p>左侧选择需求或任务，或点上方按钮新建。</p>";
}

function mergeView(m, kind) {
  if (!m) return "";
  let h = `<h3>${MERGE[m.state] || esc(m.state)}</h3>`;
  if (m.message) h += `<pre>${esc(m.message)}</pre>`;
  if (m.conflicts?.length) h += `<p>冲突文件：${m.conflicts.map(esc).join("、")}（任务 ${esc(m.conflict_task)}，可以“从头重做”后再合并）</p>`;
  if (m.test_output) h += `<details ${m.state === "test_failed" ? "open" : ""}><summary>全量测试输出（退出码 ${m.test_code}）</summary><pre>${esc(m.test_output)}</pre></details>`;
  if (m.state === "test_failed") h += `<button data-undo="${kind}">撤销这次合并</button> <button data-keep="${kind}">保留合并</button>`;
  return h;
}

function reqView(r) {
  let h = `<h2>${esc(r.text)}</h2><p class="state">${REQ[r.status]}</p>`;
  if (r.error) h += `<p class="err">${esc(r.error)}</p>`;
  if (r.plan_raw) h += `<details><summary>Claude 原文</summary><pre>${esc(r.plan_raw)}</pre></details>`;
  const tasks = r.tasks.map(task).filter(Boolean);
  if (r.status === "confirm_plan") {
    h += `<table class="plan"><tr><th>任务号</th><th>标题</th><th>执行者</th><th>模型</th><th>权限</th><th>依赖</th><th>验收命令</th><th></th></tr>`;
    for (const t of tasks) {
      h += `<tr data-row data-id="${t.id}"><td>${t.id}</td><td><input name="title" value="${esc(t.title)}"></td>
        <td>${agentSelect(t.agent)}</td><td>${modelSelect(t.agent, t.model)}</td><td>${levelSelect(t.agent, t.level)}</td>
        <td><input name="after" value="${esc(t.after.join(","))}" size="10"></td><td><input name="accept" value="${esc(t.accept)}"></td>
        <td><button data-del="${t.id}">删除</button></td></tr>
        <tr data-prompt="${t.id}"><td colspan="8"><textarea name="prompt" rows="4">${esc(t.prompt)}</textarea>
        ${t.problems.length ? `<div class="err">${t.problems.map(esc).join("；")}</div>` : ""}</td></tr>`;
    }
    h += `</table><p><button id="add-draft">＋ 加一个任务</button> <button id="start" class="primary">开始</button></p>`;
  } else {
    h += `<ul>${tasks.map((t) => `<li>${ICON[t.status]} ${t.id} ${esc(t.title)} — ${STATUS[t.status]}${t.status === "queued" && t.after.length ? `（等待 ${t.after.join("、")}）` : ""}</li>`).join("")}</ul>`;
  }
  if (r.status === "confirm_merge") h += `<h3>Claude 的总结</h3><pre>${esc(r.summary)}</pre><button id="merge-req" class="primary">合并</button>`;
  return h + mergeView(r.merge, "req");
}

function taskView(t) {
  const a = agentOf(t.agent);
  const st = t.status;
  const btn = (id, text, show) => (show ? `<button id="${id}">${text}</button> ` : "");
  let h = `<h2>${t.id} ${esc(t.title)}</h2><p class="state">${ICON[st]} ${STATUS[st]} · ${esc(a?.label || t.agent)} / ${esc(t.model)} / ${S.levels[t.level]} · 第 ${t.attempts} 次</p>`;
  if (t.error) h += `<p class="err">${esc(t.error)}</p>`;
  if (st === "rejected" && t.attempts >= 3) h += `<p class="err">已经重派 2 次还不合格，建议你自己处理。</p>`;
  h += `<div class="actions">
    ${btn("stop", "停止", ["running", "queued"].includes(st))}
    ${btn("retry", "重派…", ["failed", "rejected", "stopped", "interrupted", "done", "passed"].includes(st))}
    ${btn("review", "交给 Claude 审查", !t.requirement && st === "done")}
    ${btn("approve", "我确认合格", ["done", "rejected"].includes(st))}
    ${btn("merge-task", "合并", !t.requirement && ["done", "passed"].includes(st))}
    ${btn("discard", "丢弃", !["running", "reviewing", "merged", "discarded", "draft"].includes(st))}</div>`;
  h += `<div class="tabs">${[["prompt", "说明"], ["log", "实时输出"], ["diff", "改动"], ["review", "Claude 审查"]].map(([k, v]) => `<button data-tab="${k}" class="${tab === k ? "on" : ""}">${v}</button>`).join("")}</div><pre id="tabbody"></pre>`;
  return h + (t.requirement ? "" : mergeView(t.merge, "task"));
}

async function loadTab() {
  const t = task(sel.id);
  const body = $("#tabbody");
  if (tab === "prompt") {
    body.textContent = t.prompt + (t.accept ? `\n\n验收命令：${t.accept}` : "") + (t.accept_code !== null ? `\n验收退出码：${t.accept_code}\n${t.accept_output}` : "");
  } else if (tab === "review") {
    const r = t.review;
    body.textContent = r ? `${r.verdict === "pass" ? "合格" : "不合格"}\n${r.reasons.join("\n")}${r.retry_prompt ? "\n\n建议的新说明：\n" + r.retry_prompt : ""}` : "还没有审查";
  } else {
    const text = await api(`/api/tasks/${t.id}/${tab}`);
    body.textContent = text || (tab === "diff" ? "没有改动" : "还没有输出");
    if (tab === "log") body.scrollTop = body.scrollHeight;
  }
}

function dialog(title, inner, onOk) {
  const d = $("#dlg");
  d.innerHTML = `<form method="dialog" data-row><h3>${title}</h3>${inner}<menu><button value="cancel">取消</button><button value="ok" class="primary">确定</button></menu></form>`;
  d.returnValue = "";
  d.showModal();
  d.onclose = async () => {
    if (d.returnValue !== "ok") return;
    const f = Object.fromEntries(new FormData(d.querySelector("form")));
    try { await onOk(f); await refresh(); } catch (e) { alert(e.message); }
  };
}

const accept = (v = "") => `<label>验收命令（可空）<input name="accept" value="${esc(v)}" size="60"></label>`;

document.addEventListener("click", async (e) => {
  const el = e.target.closest("button, .item");
  if (!el || el.closest("dialog")) return;
  if (el.dataset.req) { sel = { kind: "req", id: el.dataset.req }; return render(); }
  if (el.dataset.task) { sel = { kind: "task", id: el.dataset.task }; tab = "log"; return render(); }
  if (el.dataset.tab) { tab = el.dataset.tab; return render(); }
  if (el.dataset.del) return act(`/api/tasks/${el.dataset.del}/delete`);
  const owner = sel?.kind === "req" ? `/api/requirements/${sel.id}` : `/api/tasks/${sel?.id}`;
  if (el.dataset.undo) return confirm("回到合并前的提交？只回退这次合并产生的提交。") && act(`${owner}/undo`);
  if (el.dataset.keep) return act(`${owner}/keep`);
  const t = sel?.kind === "task" ? task(sel.id) : null;
  switch (el.id) {
    case "open": return act("/api/open", { path: $("#project").value.trim() });
    case "new-req":
      if (!S.project) return alert("请先打开项目");
      return dialog("新需求给 Claude", `<textarea name="text" rows="8" placeholder="描述你要做的事"></textarea>`,
        async (f) => { const r = await api("/api/requirements", f); sel = { kind: "req", id: r.id }; });
    case "new-task":
      if (!S.project) return alert("请先打开项目");
      return dialog("手动派活", `<label>标题（可空）<input name="title" size="40"></label>${taskFields({})}${accept()}`,
        async (f) => { const r = await api("/api/tasks", f); sel = { kind: "task", id: r.id }; tab = "log"; });
    case "add-draft":
      return dialog("加一个任务", `<label>标题<input name="title" size="40"></label>${taskFields({})}<label>依赖（任务号，逗号分隔）<input name="after"></label>${accept()}`,
        (f) => api(`/api/requirements/${sel.id}/tasks`, { ...f, after: split(f.after) }));
    case "start": return act(`/api/requirements/${sel.id}/start`);
    case "merge-req": return confirm("把合格的任务合并进当前分支？") && act(`/api/requirements/${sel.id}/merge`);
    case "stop": return act(`/api/tasks/${t.id}/stop`);
    case "review": return act(`/api/tasks/${t.id}/review`);
    case "approve": return act(`/api/tasks/${t.id}/approve`);
    case "merge-task": return confirm("把这个任务的改动合并进当前分支？") && act(`/api/tasks/${t.id}/merge`);
    case "discard": return confirm("丢弃这个任务和它的改动？") && act(`/api/tasks/${t.id}/discard`);
    case "retry":
      return dialog("重派", `${taskFields({ ...t, prompt: t.review?.retry_prompt || t.prompt })}
        <label><input type="checkbox" name="keep" checked> 在已有改动上继续（不勾选则从头重做）</label>`,
        (f) => api(`/api/tasks/${t.id}/retry`, { ...f, keep: f.keep === "on" }));
  }
});

document.addEventListener("change", (e) => {
  const row = e.target.closest("[data-row]");
  if (e.target.name === "agent" && row) {
    const a = agentOf(e.target.value);
    row.querySelector("[name=model]").outerHTML = modelSelect(a.key, a.default_model);
    const lv = row.querySelector("[name=level]");
    lv.outerHTML = levelSelect(a.key, a.levels.includes(lv.value) ? lv.value : a.levels[0]);
  }
  if (e.target.closest("dialog")) return;
  const id = e.target.closest("tr[data-id]")?.dataset.id || e.target.closest("tr[data-prompt]")?.dataset.prompt;
  if (!id) return;
  const tr = document.querySelector(`tr[data-id="${id}"]`);
  const v = (name) => tr.querySelector(`[name=${name}]`).value;
  act(`/api/tasks/${id}/update`, {
    title: v("title"), agent: v("agent"), model: v("model"), level: v("level"), accept: v("accept"),
    after: split(v("after")), prompt: document.querySelector(`tr[data-prompt="${id}"] textarea`).value,
  });
});

const editing = () => !!document.activeElement?.closest("#detail input, #detail textarea, #detail select");
document.addEventListener("focusout", () => { if (dirty) { dirty = false; setTimeout(refresh, 0); } });

function connect() {
  const es = new EventSource(`/api/events?t=${encodeURIComponent(token)}`);
  let timer = null;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.project && S && ev.project !== S.project) return;
    if (ev.type === "log") {
      const body = $("#tabbody");
      if (body && sel?.id === ev.task && tab === "log") {
        const stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 20;
        body.textContent += ev.line + "\n";
        if (stick) body.scrollTop = body.scrollHeight;
      }
      return;
    }
    if (ev.type === "error") console.warn(ev.message);
    clearTimeout(timer);
    timer = setTimeout(() => (editing() ? (dirty = true) : refresh()), 200);
  };
  es.onerror = () => { es.close(); setTimeout(() => refresh().then(connect).catch(connect), 2000); };
}

refresh().then(connect).catch((e) => { document.body.textContent = e.message; });
```

- [ ] **Step 5: 运行，确认通过**

Run: `python -m pytest tests/test_server.py -q`
Expected: 9 passed

- [ ] **Step 6: 提交**
```bash
git add agentdesk/web tests/test_server.py
git commit -m "feat: 界面——需求和任务列表、确认拆分表格、实时输出、改动、审查、重派和合并"
```

### Task 12: 启动入口、说明文档和真机走查

**Files:**
- Create: `agentdesk/__main__.py`, `启动.bat`, `README.md`, `tests/test_main.py`
- Modify: `.gitattributes`（已有 `*.bat text eol=crlf`，确认即可）

**Interfaces:**
- Consumes: `App`、`make_server`
- Produces: `python -m agentdesk [项目目录]`；`APP_DIR`；`find_edge() -> str | None`；`open_window(url)`；`main(argv=None)`

- [ ] **Step 1: 写失败的测试** `tests/test_main.py`：
```python
import threading
import urllib.request

from agentdesk import __main__ as entry
from helpers import wait_for


def test_main_starts_server_and_opens_the_project(tmp_path, repo, monkeypatch):
    app_dir = tmp_path / "app"   # 不碰真实应用目录里的 recent.json
    app_dir.mkdir()
    (app_dir / "agents.toml").write_text((entry.APP_DIR / "agents.toml").read_text(encoding="utf-8"),
                                         encoding="utf-8")
    monkeypatch.setattr(entry, "APP_DIR", app_dir)
    opened = []
    monkeypatch.setattr(entry, "open_window", opened.append)
    servers = []
    started = threading.Event()
    real = entry.make_server

    def capture(app, port=0):
        srv = real(app, port)
        servers.append((srv, app))
        started.set()
        return srv

    monkeypatch.setattr(entry, "make_server", capture)
    threading.Thread(target=entry.main, args=([str(repo)],), daemon=True).start()
    assert started.wait(10)
    srv, app = servers[0]
    try:
        wait_for(lambda: opened)
        assert opened[0].startswith("http://127.0.0.1:") and f"t={app.token}" in opened[0]
        assert app.current is not None and app.current.repo.name == "仓库"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        assert opener.open(opened[0], timeout=10).status == 200
    finally:
        srv.shutdown()
        for engine in app.engines.values():
            engine.close()
```

Run: `python -m pytest tests/test_main.py -q`
Expected: FAIL，`cannot import name '__main__'` 或缺少 `open_window`

- [ ] **Step 2: 实现** `agentdesk/__main__.py`：
```python
"""启动：python -m agentdesk [项目目录]。启动本地服务并用 Edge 应用窗口打开界面。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from agentdesk.server import App, make_server

APP_DIR = Path(__file__).resolve().parent.parent
EDGE_PATHS = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
              Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]


def find_edge() -> str | None:
    return shutil.which("msedge") or next((str(p) for p in EDGE_PATHS if p.is_file()), None)


def open_window(url: str) -> None:
    edge = find_edge()
    if edge:
        subprocess.Popen([edge, f"--app={url}"])
    else:
        webbrowser.open(url)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    app = App(APP_DIR)
    project = argv[0] if argv else next(iter(app.recent()), None)
    if project:
        try:
            app.open_project(project)
        except Exception as e:  # 打不开就让用户在界面里重选
            print(f"打开项目失败：{e}")
    server = make_server(app)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={app.token}"
    print(f"Agent 指挥台已启动：{url}\n关闭这个窗口即退出。")
    open_window(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for engine in app.engines.values():
            engine.close()


if __name__ == "__main__":
    main()
```

`启动.bat`（CRLF 换行）：
```bat
@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m agentdesk %*
pause
```

`README.md`：
````markdown
# agentdesk：Agent 指挥台

Claude 拆任务、审查；Gemini、Copilot、Claude 等命令行 agent 在各自的 git worktree 里执行；你在一个窗口里确认拆分、重派和合并。只用 Python 标准库，不需要安装。

## 准备
1. Python 3.13、git、Edge。
2. 装好并登录要用的命令行：`claude`、`gemini`、`copilot`（`npm install -g @google/gemini-cli @github/copilot`，各运行一次按提示登录）。
3. 按需修改 `agents.toml`：每个执行者的可选模型、各权限档位的启动命令。Copilot 能用的模型以它的 `/model` 列表为准。

## 启动
双击 `启动.bat`（或 `python -m agentdesk <项目目录>`），会打开一个窗口。关闭黑色控制台窗口即退出。

## 项目级配置（可选）
在项目根目录放 `.agentdesk.toml`，覆盖全局配置中的任意项：
```toml
worktree_setup = ["mkdir runs"]          # 每个 worktree 建好后先执行
test_command = "python -m pytest -q"     # 合并后跑的全量测试
```

## 数据放在哪
- 需求、任务、日志：`<项目>/.git/agentdesk/`，不进版本库。
- 每个任务的 worktree：`<项目父目录>/<项目名>.agentdesk-wt/<任务号>`，分支 `agentdesk/<任务号>`；合并或丢弃后自动删除。

## 已知限制
- 启动时只检测命令能否运行，检测不了是否已登录；没登录的执行者会在第一次派活时失败。
- 应用在任务运行中被关掉，重开后任务标为"中断"，不会自动结束残留进程（pid 可能已被别的程序复用），请在任务管理器里确认。
- 正在运行的任务不能改权限，要改就停止后重派（可保留已有改动）。
````

- [ ] **Step 3: 运行全部测试**

Run: `python -m pytest -q`
Expected: 全部通过

- [ ] **Step 4: 提交**
```bash
git add agentdesk/__main__.py 启动.bat README.md tests/test_main.py
git commit -m "feat: 启动入口、Edge 应用窗口和说明文档"
```

- [ ] **Step 5: 真机走查（用真实的 claude、gemini、copilot）**

准备一个演示仓库（不要用正式项目）：
```bash
mkdir -p /d/mingjw/agentdesk-demo && cd /d/mingjw/agentdesk-demo && git init -q -b main
printf 'def add(a, b):\n    return a + b\n' > calc.py && git add -A && git commit -qm init
printf 'test_command = "python -c \\"import calc; assert calc.add(1, 2) == 3\\""\n' > .agentdesk.toml
```
双击 `启动.bat`，在窗口里打开 `D:\mingjw\agentdesk-demo`，逐项确认并记录结果：

1. 三个执行者的检测结果显示为可用（没装的显示原因）。
2. 手动派活：Copilot / `只读` / "说出 calc.py 里有哪些函数"——实时输出滚动，完成后状态"待审查"，改动页"没有改动"。
3. 手动派活：Gemini / `改代码` / "给 calc.py 加一个 sub(a, b) 函数"，验收命令 `python -c "import calc; assert calc.sub(3, 1) == 2"`——完成后说明页显示验收退出码 0，改动页能看到 diff；点"交给 Claude 审查"得到结论；点"合并"，主仓库出现 `sub`，全量测试通过。
4. 手动派活：Claude / haiku / `改代码` / 一个会跑很久的任务，跑起来后点"停止"，状态变"已停止"；再"重派…"，勾选"在已有改动上继续"，换成 sonnet，能继续跑完。
5. 新需求给 Claude："给 calc.py 加 mul 和 div，div 除数为 0 时抛 ValueError，并各写一个测试文件"——出现确认表格；把其中一个任务改派给 Copilot、换模型，点"开始"；全部合格后出现总结，点"合并"。
6. 在主仓库里随便改一个文件不提交，再合并另一个任务——提示"主工作区有未提交的改动"，仓库没被改动。
7. 把 `.agentdesk.toml` 的 `test_command` 改成必定失败的命令（需重启应用），合并一个任务——显示"全量测试没通过"，点"撤销这次合并"后主仓库回到合并前。
8. 任务运行中关掉控制台窗口，再双击 `启动.bat`——该任务显示"中断"和提示文字。
9. 走查结束后删除 `D:\mingjw\agentdesk-demo` 和 `D:\mingjw\agentdesk-demo.agentdesk-wt`。

任何一项不符合，按 superpowers:systematic-debugging 排查后修复，补上对应的自动化测试再提交。

