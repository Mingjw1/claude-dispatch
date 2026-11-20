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
        if not isinstance(t.get("after") or [], list) or not isinstance(t.get("files") or [], list):
            raise ValueError(f"任务 {t['id']} 的 after 和 files 必须是数组")
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
