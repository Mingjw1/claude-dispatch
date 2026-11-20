# claude-dispatch：Claude 当指挥，Gemini / Copilot 干活

两种用法，选一种或都用：

| | 目录 | 怎么用 |
|---|---|---|
| Claude Code 插件 | `plugin/` | 在 Claude Code 对话里说"这个交给 Copilot / Gemini 做"，当前会话的 Claude 拆任务、派活、审查 |
| agentdesk 桌面工具 | `agentdesk/` | 双击 `agentdesk\启动.bat` 打开窗口，填需求或手动派活；拆任务、审查、合并都在界面上确认，执行者和模型下拉选择 |

## 共同的准备
1. 安装并登录命令行工具：
   ```
   npm install -g @google/gemini-cli @github/copilot
   gemini        # 首次运行按提示登录
   copilot       # 首次运行按提示登录
   ```
2. 确认可以不进交互界面直接调用：`gemini -p "回复OK"`、`copilot -p "回复OK" -s`

## Claude Code 插件
包含：
- `dispatch` skill：Claude 拆任务、派活、审查、合并、验证的流程
- `dispatch:copilot-worker`：把任务交给 Copilot CLI，在独立 worktree 里改代码
- `dispatch:gemini-worker`：把任务交给 Gemini CLI，只读分析或改代码

安装：
1. 在 `~/.claude/settings.json` 里加上 `"worktree": {"baseRef": "head"}`，让 worktree 从当前分支建。
2. 在 Claude Code 里执行，然后重启：
   ```
   /plugin marketplace add Mingjw1/claude-dispatch
   /plugin install dispatch@dispatch-marketplace
   ```

使用：直接说"用 /dispatch 做：……"，或者"这个交给 Copilot / Gemini 做"。建议在各个项目的 `.git/info/exclude` 里加一行 `.claude/worktrees/`。

## agentdesk 桌面工具
只用 Python 3.13 标准库和 Edge，不需要安装。克隆本仓库后双击 `agentdesk\启动.bat`。详见 [agentdesk/README.md](agentdesk/README.md)。
