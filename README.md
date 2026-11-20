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

## 开发
```
python -m pytest -q
```
