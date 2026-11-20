"""对 git 的最小封装。中文路径和文件名原样处理（core.quotepath=false）。"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentdesk.proc import NO_WINDOW


class GitError(Exception):
    pass


def _run(cwd: Path, args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = _run(cwd, args)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} 失败：{(r.stderr or r.stdout).strip()}")
    return r.stdout.rstrip("\n")


def repo_root(path: Path) -> Path:
    if not path.is_dir():
        raise GitError(f"目录不存在：{path}")
    return Path(git(path, "rev-parse", "--show-toplevel")).resolve()


def store_dir(repo: Path) -> Path:
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    return (common if common.is_absolute() else repo / common).resolve() / "agentdesk"


def head(cwd: Path) -> str:
    return git(cwd, "rev-parse", "HEAD")


def branch(cwd: Path) -> str:
    return git(cwd, "rev-parse", "--abbrev-ref", "HEAD")


def is_clean(cwd: Path) -> bool:
    return git(cwd, "status", "--porcelain") == ""


def worktree_dir(repo: Path, task_id: str) -> Path:
    return repo.parent / f"{repo.name}.agentdesk-wt" / task_id


def remove_worktree(repo: Path, path: Path | None, branch_name: str | None) -> None:
    if path is not None and path.exists():
        git(repo, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)
    if branch_name:
        git(repo, "branch", "-D", branch_name, check=False)


def create_worktree(repo: Path, task_id: str, start: str) -> tuple[Path, str]:
    path, name = worktree_dir(repo, task_id), f"agentdesk/{task_id}"
    remove_worktree(repo, path, None)  # 清掉上次留下的目录
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-f", "-B", name, str(path), start)
    return path, name


def commit_all(wt: Path, message: str) -> str | None:
    git(wt, "add", "-A")
    if git(wt, "status", "--porcelain") == "":
        return None
    git(wt, "commit", "-q", "--no-verify", "-m", message)
    return head(wt)


def diff_stat(cwd: Path, base: str, target: str = "HEAD") -> str:
    return git(cwd, "diff", "--stat", base, target)


def diff(cwd: Path, base: str, target: str = "HEAD", limit: int = 200_000) -> str:
    text = git(cwd, "diff", base, target)
    if len(text) > limit:
        return f"改动超过 {limit // 1000}KB，只给统计：\n" + diff_stat(cwd, base, target)
    return text


def changed_files(cwd: Path, base: str, target: str = "HEAD") -> list[str]:
    out = git(cwd, "diff", "--name-only", base, target)
    return out.splitlines() if out else []


@dataclass
class MergeResult:
    ok: bool
    conflicts: list[str]
    message: str


def merge(cwd: Path, ref: str, message: str) -> MergeResult:
    r = _run(cwd, ["merge", "--no-ff", "--no-edit", "-m", message, ref])
    if r.returncode == 0:
        return MergeResult(True, [], "")
    conflicts = git(cwd, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()
    git(cwd, "merge", "--abort", check=False)
    return MergeResult(False, conflicts, (r.stdout + r.stderr).strip())


def reset_hard(cwd: Path, sha: str) -> None:
    git(cwd, "reset", "--hard", "-q", sha)
