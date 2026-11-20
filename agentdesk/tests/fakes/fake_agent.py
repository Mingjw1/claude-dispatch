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
