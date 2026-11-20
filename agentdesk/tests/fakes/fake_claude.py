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
