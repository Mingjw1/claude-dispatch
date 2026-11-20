# agentdesk 设计：轻量的多 agent 指挥台

日期：2026-11-20

## 1. 目标

一个本机使用的轻量桌面工具：Claude 当指挥，把编码和分析任务派给 Gemini、Copilot、Claude 等命令行 agent，每个任务可选执行者和模型；也可以手动派活。

### 约束
- 不能装安装包，不引入第三方依赖：只用本机已有的 Python 3.13 标准库和 Edge。
- 代码不能发给未经批准的第三方：应用自己不连任何模型服务，只调用本机已安装并登录的 `claude`、`gemini`、`copilot` 等命令行。
- 不做来路不明的大软件：代码量小、可读、可审查。

### 不做
登录、多用户、远程访问、定时任务、自动 push、内置模型调用。

### 成功标准
- 双击 `启动.bat` 就能打开，不用安装。
- 每个派出的任务都能看到状态、实时输出、改动、审查意见。
- 合并进当前分支前一定经过用户确认。
- 换执行者或模型只需在下拉框选；增加新执行者只改配置文件。

## 2. 用户怎么用

### 界面
```
┌ Agent 指挥台 ─ 项目: D:\…\repo  分支: feature-x ─────────────────────────┐
│ [＋ 新需求给 Claude]  [＋ 手动派活]                  正在跑 2 / 最多同时 3   │
├──────────────────────────┬───────────────────────────────────────────────┤
│ 需求：补测试架构          │ 任务 2：给 photo_checks 补流程测试            │
│  ⏸ 等你确认：任务拆分      │ 执行者 [Copilot ▾]  模型 [auto ▾]  改代码     │
│  1 修复 runs 目录 Gemini ✅│ ┌ 说明 ┬ 实时输出 ┬ 改动 ┬ Claude 审查 ┐       │
│  2 photo_checks  Copilot ⏳│ │ ...执行者输出逐行滚动...                │       │
│  3 统一 Fake     Claude  ⏳│ └────────────────────────────────────────┘       │
│ 手动任务                  │ [停止]                                        │
│  · 审查 diff     Gemini ✅│                                               │
└──────────────────────────┴───────────────────────────────────────────────┘
```
启动时选择项目目录（任意本地 git 仓库），记住最近用过的项目。

### Claude 指挥模式
1. 用户输入需求；Claude 读代码后拆成任务列表（说明、执行者、模型、只读/改代码、涉及文件、验收命令、先后依赖）。
2. **⏸ 确认拆分**：任务以可编辑表格显示，可改执行者、模型、说明，可删可加，点"开始"。
3. 任务各在自己的 worktree 里执行，并行数受 `max_parallel` 限制，有 `after` 依赖的等前置任务完成。
4. 每个任务跑完：应用执行验收命令，再交给 Claude 审查，得到合格或不合格加理由。
5. **⏸ 确认重派**：不合格时显示理由和 Claude 建议的新说明，用户可改执行者、模型、说明后点"重派"，或点"我自己处理"。同一任务重派 2 次后，界面建议用户自己处理。
6. **⏸ 确认合并**：全部合格后显示每个任务的改动、Claude 的总结，用户点"合并"才合并进当前分支。不自动 push。

### 手动模式
填写说明，选执行者、模型、只读/改代码、验收命令（可空），点"派出"。完成后用户看改动，选"合并"、"丢弃"或"交给 Claude 审查"。

### 通用规则
- 同一个任务不会同时派给两个执行者（worktree 之间不隔离，执行者能读到彼此的成果）。
- 任何任务都可以随时停止。
- 关掉窗口再打开，需求和任务记录都在。

## 3. 架构

Python 包 `agentdesk`，只用标准库。界面是一个原生 HTML/JS/CSS 页面，不需要构建；用 `msedge --app=http://127.0.0.1:<端口>/?t=<口令>` 打开，呈现为独立窗口。

| 模块 | 职责 |
|---|---|
| `server` | `ThreadingHTTPServer`，只监听 127.0.0.1；每次启动生成随机口令，所有请求必须带口令；JSON 接口；SSE 推送实时输出和状态变化 |
| `store` | 需求、任务、状态、日志存为 JSON 和文本文件，放在 `<项目>/.git/agentdesk/`（不进版本库） |
| `config` | 读全局 `agents.toml`（与应用放在一起）和项目里可选的 `.agentdesk.toml`，后者覆盖前者 |
| `worktree` | 从当前分支 HEAD 为每个任务建 worktree 和分支 `agentdesk/<任务号>`，目录放在仓库外 `<仓库>.agentdesk-wt\<任务号>`；建好后执行项目配置的 `worktree_setup` 命令；合并或丢弃后清理 |
| `runner` | 用子进程启动执行者；执行者结束后由应用把 worktree 里的改动提交到任务分支（审查和合并都基于这个提交）；输出写日志并推送；超时（默认 30 分钟，可配）；停止时结束整个进程树（`taskkill /T /F`） |
| `commander` | 调用 `claude -p` 完成拆任务、审查、合并前总结；解析并校验返回的 JSON |
| `merger` | 用户确认后：依次把各任务分支合并进当前分支（`--no-ff`），执行全量测试，清理 worktree 和分支 |

### 配置
```toml
# agents.toml（全局）
max_parallel = 3
task_timeout_minutes = 30
commander_model = "opus"

[agents.copilot]
label = "Copilot"
models = ["auto", "gpt-6-luna", "gpt-6.1-sol"]
default_model = "auto"
read = ["copilot", "-p", "{prompt}", "-s", "--no-ask-user", "--allow-all-tools", "--deny-tool", "write", "--model", "{model}"]
edit = ["copilot", "-p", "{prompt}", "-s", "--no-ask-user", "--allow-all-tools", "--deny-tool", "shell(git push)", "--model", "{model}"]

[agents.gemini]
label = "Gemini"
models = ["auto-gemini-3"]
default_model = "auto-gemini-3"
prompt_via = "stdin"
read = ["gemini", "--approval-mode", "plan", "-m", "{model}"]
edit = ["gemini", "--approval-mode", "yolo", "-m", "{model}"]

[agents.claude]
label = "Claude"
models = ["haiku", "sonnet", "opus"]
default_model = "sonnet"
read = ["claude", "-p", "{prompt}", "--model", "{model}", "--permission-mode", "plan"]
edit = ["claude", "-p", "{prompt}", "--model", "{model}", "--permission-mode", "acceptEdits"]
```
```toml
# <项目>/.agentdesk.toml（可选）
worktree_setup = ["mkdir runs"]
test_command = "python -m pytest -q"
```
- 命令写成参数数组，不经过 shell，`{prompt}`、`{model}` 是占位符。
- `prompt_via = "stdin"` 时任务说明从标准输入传入（避开 Windows 32K 命令行长度上限）；默认 `arg`。任务说明超过 24K 字符而执行者只支持 `arg` 时，拒绝派出并提示。
- 改代码任务的说明末尾，应用自动追加"完成后不要 git commit、push 或 reset，只改工作区文件；只读写当前目录之内的文件"。只读任务前面加"只读任务：不要修改、新建或删除任何文件"。
- Copilot 的实际可用模型由用户在 Copilot 里用 `/model` 查看后填写。

### 任务状态
`草稿 → 排队 → 运行中 → 待审查 → 审查中 → 合格 / 不合格 → 待合并 → 已合并`
另有：`失败`（非 0 退出或超时）、`已停止`、`中断`（应用重启时发现仍在运行中）、`已丢弃`。

## 4. Claude 指挥协议

三次调用都在项目主目录下执行 `claude -p <提示> --output-format json --permission-mode plan --model <commander_model>`，Claude 只读。

### 拆任务
输入：用户需求、可用执行者和模型列表、规则（每个任务只改一组文件；会改同一文件的任务用 `after` 排序；说明要能独立看懂；写明验收命令）。
输出：
```json
{"tasks": [{"id": 1, "title": "...", "mode": "edit", "agent": "copilot", "model": "auto",
            "files": ["tests/test_x.py"], "after": [], "prompt": "...", "accept": "python -m pytest tests/test_x.py -q"}]}
```
校验：执行者和模型必须在配置里，否则在确认表格里标红；`after` 引用必须存在且无环。

### 审查
应用先在 worktree 里执行验收命令，然后把以下内容交给 Claude：任务说明、相对起点的改动（含新文件；超过 200KB 时只给 `--stat` 并注明）、验收命令的退出码和输出末尾、改到 `files` 以外文件的清单。
输出：
```json
{"verdict": "pass", "reasons": ["..."], "retry_prompt": "..."}
```
`verdict` 为 `fail` 时必须有 `retry_prompt`。

### 合并前总结
输入：全部任务的标题、执行者、审查结论、改动统计。输出：一段给用户看的总结（文本）。

### 返回格式错误
JSON 解析失败或缺字段：带着错误信息再要一次；仍失败则显示原文，拆任务时让用户手动建任务，审查时转为用户自己审查。

## 5. 出错处理

| 情况 | 处理 |
|---|---|
| 命令行工具没装或没登录 | 启动时对每个执行者执行一次版本检测，失败的在下拉框里变灰并显示原因 |
| 执行者非 0 退出或超时 | 任务标为失败，显示错误输出末尾，可重派 |
| Claude 返回格式错误 | 见第 4 节 |
| 应用中途关闭 | 重开时把"运行中"的任务标为"中断"，按记录的 pid 尽量结束残留进程；worktree 保留，可重跑或直接审查 |
| 合并时主工作区有未提交改动 | 拒绝合并，提示先提交 |
| 合并冲突 | `git merge --abort` 恢复，列出冲突文件；任务保持"待合并"，可在最新代码上重派 |
| 合并后全量测试失败 | 不自动回退；显示结果，提供"撤销这次合并"（回到合并前记录的 HEAD，只回退本次合并产生的提交），点击前再确认一次 |

## 6. 测试

- pytest 单元测试，临时 git 仓库放在项目内 `runs/`（不写 C 盘临时目录）。
- 执行者和 Claude 用假脚本代替：假执行者是写文件、按参数输出、可模拟失败和超时的小 Python 脚本；假 `claude` 返回预置 JSON。
- 覆盖：配置解析和覆盖、命令拼装（arg/stdin）、worktree 建立与清理、任务状态流转、依赖调度和并行上限、审查结果解析、合并、冲突回退、撤销合并、重启后的中断恢复、口令校验。
- 最后用真实的 claude、gemini、copilot 在一个小仓库上手动跑通一遍完整流程。

## 7. 目录结构

```
agentdesk/
├─ 启动.bat              启动服务并用 Edge 应用窗口打开
├─ agents.toml           全局执行者配置
├─ agentdesk/            Python 包：server, store, config, worktree, runner, commander, merger
│  └─ web/               index.html, app.js, style.css
├─ tests/
└─ docs/
```
