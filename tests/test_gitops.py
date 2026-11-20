import pytest

from agentdesk import gitops


def test_store_dir_is_inside_git_dir_even_from_a_worktree(repo):
    assert gitops.store_dir(repo) == (repo / ".git" / "agentdesk").resolve()
    wt, _ = gitops.create_worktree(repo, "t0001", gitops.head(repo))
    assert gitops.store_dir(wt) == gitops.store_dir(repo)


def test_worktree_lifecycle_with_chinese_names(repo):
    base = gitops.head(repo)
    wt, branch = gitops.create_worktree(repo, "t0001", base)
    assert wt == repo.parent / "仓库.agentdesk-wt" / "t0001" and branch == "agentdesk/t0001"
    assert gitops.commit_all(wt, "nothing") is None
    (wt / "中文.txt").write_text("x\n", encoding="utf-8")
    sha = gitops.commit_all(wt, "add")
    assert sha == gitops.head(wt) != base
    assert gitops.changed_files(repo, base, sha) == ["中文.txt"]
    assert "+x" in gitops.diff(repo, base, sha) and "中文.txt" in gitops.diff_stat(repo, base, sha)
    gitops.remove_worktree(repo, wt, branch)
    assert not wt.exists() and gitops.git(repo, "branch", "--list", branch) == ""


def test_create_worktree_replaces_a_stale_directory(repo):
    wt, branch = gitops.create_worktree(repo, "t0001", gitops.head(repo))
    gitops.git(repo, "worktree", "remove", "--force", str(wt))
    wt.mkdir(parents=True)
    (wt / "leftover.txt").write_text("x", encoding="utf-8")
    again, _ = gitops.create_worktree(repo, "t0001", gitops.head(repo))
    assert again == wt and not (wt / "leftover.txt").exists()


def test_huge_diff_falls_back_to_stat(repo):
    base = gitops.head(repo)
    wt, _ = gitops.create_worktree(repo, "t0001", base)
    (wt / "big.txt").write_text("y\n" * 5000, encoding="utf-8")
    sha = gitops.commit_all(wt, "big")
    text = gitops.diff(repo, base, sha, limit=1000)
    assert text.startswith("改动超过") and "big.txt" in text


def test_merge_conflict_is_aborted(repo):
    base = gitops.head(repo)
    wt1, b1 = gitops.create_worktree(repo, "t1", base)
    (wt1 / "README.md").write_text("one\n", encoding="utf-8")
    gitops.commit_all(wt1, "one")
    wt2, b2 = gitops.create_worktree(repo, "t2", base)
    (wt2 / "README.md").write_text("two\n", encoding="utf-8")
    gitops.commit_all(wt2, "two")
    assert gitops.merge(repo, b1, "m1").ok
    r = gitops.merge(repo, b2, "m2")
    assert not r.ok and r.conflicts == ["README.md"]
    assert gitops.is_clean(repo) and (repo / "README.md").read_text(encoding="utf-8") == "one\n"


def test_is_clean_and_reset_hard(repo):
    base = gitops.head(repo)
    (repo / "README.md").write_text("x", encoding="utf-8")
    assert not gitops.is_clean(repo)
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-q", "-m", "x")
    gitops.reset_hard(repo, base)
    assert gitops.head(repo) == base and gitops.is_clean(repo) and gitops.branch(repo) == "main"


def test_repo_root_rejects_non_repo(tmp_path):
    with pytest.raises(gitops.GitError):
        gitops.repo_root(tmp_path / "nope")


def test_untracked_files_do_not_count_as_uncommitted_changes(repo):
    (repo / "new.txt").write_text("x", encoding="utf-8")
    assert gitops.is_clean(repo)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    assert not gitops.is_clean(repo)
