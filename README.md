# dispatch：Claude 当指挥，Gemini / Copilot 干活

Claude Code 插件，包含：
- `dispatch` skill：Claude 拆任务、派活、审查、合并、验证的流程
- `dispatch:copilot-worker`：把任务交给 Copilot CLI，在独立 worktree 里改代码
- `dispatch:gemini-worker`：把任务交给 Gemini CLI，只读分析或改代码

## 新机器上的准备
1. 安装并登录两个命令行工具：
   ```
   npm install -g @google/gemini-cli @github/copilot
   gemini        # 首次运行按提示登录
   copilot       # 首次运行按提示登录
   ```
2. 确认可以不进交互界面直接调用：`gemini -p "回复OK"`、`copilot -p "回复OK" -s`

## 安装插件
在 Claude Code 里执行（路径换成本目录的位置或 git 仓库地址）：
```
/plugin marketplace add D:/mingjw/claude-dispatch
/plugin install dispatch@dispatch-marketplace
```
装好后重启 Claude Code。

## 使用
直接说"用 /dispatch 做：……"，或者"这个交给 Copilot / Gemini 做"。

建议在各个项目的 `.git/info/exclude` 里加一行 `.claude/worktrees/`，避免 worktree 出现在 `git status` 里。
