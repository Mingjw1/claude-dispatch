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
