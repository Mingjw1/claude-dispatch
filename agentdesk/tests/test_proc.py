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
