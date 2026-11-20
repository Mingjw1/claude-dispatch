#!/usr/bin/env bash
# 转发员唯一要执行的命令：bash run.sh <copilot|gemini> <read|edit> <任务说明文件>
# 所有管道、重定向、git 相关参数都放在这里，转发员的命令行保持最简单（worktree 隔离检查会拦复杂命令）。
set -u
tool=$1 mode=$2 file=$3
[ -f "$file" ] || { echo "找不到任务说明文件: $file"; exit 2; }
prompt=$(cat "$file")
rm -f "$file"

git_dir=$(git rev-parse --git-dir 2>/dev/null || echo .)
err="$git_dir/dispatch-$tool-err.log"
start=$(date +%s)

if [ "$mode" = edit ]; then
  prompt="$prompt

完成后不要 git commit、push 或 reset，只改工作区文件。"
fi

case "$tool:$mode" in
  copilot:read)
    copilot -p "只读任务：不要修改、新建或删除任何文件。
$prompt" -s --no-ask-user --allow-all-tools \
      --deny-tool write >"$git_dir/dispatch-out.log" 2>"$err" ;;
  copilot:edit)
    copilot -p "$prompt" -s --no-ask-user --allow-all-tools \
      --deny-tool 'shell(git push)' --deny-tool 'shell(git commit)' --deny-tool 'shell(git reset)' \
      >"$git_dir/dispatch-out.log" 2>"$err" ;;
  gemini:read)
    gemini -p "$prompt" --approval-mode plan >"$git_dir/dispatch-out.log" 2>"$err" ;;
  gemini:edit)
    gemini -p "$prompt" --approval-mode yolo >"$git_dir/dispatch-out.log" 2>"$err" ;;
  *) echo "用法: run.sh <copilot|gemini> <read|edit> <任务说明文件>"; exit 2 ;;
esac
code=$?

echo "=== $tool 输出（最后 200 行） ==="
tail -n 200 "$git_dir/dispatch-out.log"
echo "=== 退出码 $code，用时 $(( $(date +%s) - start )) 秒 ==="
if [ $code -ne 0 ]; then
  echo "=== 错误输出（已滤掉常见警告） ==="
  grep -viE "TLS|metadata|deprecat|trace-warnings|ripgrep" "$err" | tail -n 30
fi
if [ "$mode" = edit ]; then
  echo "=== 工作区改动 ==="
  git status --short
fi
rm -f "$err" "$git_dir/dispatch-out.log"
exit $code
