"""Tests for the git collector, run against real throwaway repositories."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.collectors.git.collector import (
    GitCollector,
    GitError,
    correlate_commits,
    find_repository_root,
)
from app.schemas.observed import ChangeType, FileTouch, GitCommit


def _git(repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> str:
    env = {
        "GIT_AUTHOR_NAME": "Fixture Author",
        "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_NAME": "Fixture Author",
        "GIT_COMMITTER_EMAIL": "fixture@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": "/usr/bin:/bin",
        "HOME": str(repo),
    }
    env.update(env_extra or {})
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, env=env, check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _commit(repo: Path, files: dict[str, str], message: str, when: datetime) -> str:
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    stamp = when.isoformat()
    _git(
        repo, "commit", "-q", "-m", message,
        env_extra={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )
    return _git(repo, "rev-parse", "HEAD").strip()


BASE = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def test_find_repository_root(repo: Path):
    _commit(repo, {"a.txt": "x\n"}, "first", BASE)
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert find_repository_root(nested) == repo.resolve()


def test_an_empty_repository_is_not_a_project_boundary(repo: Path):
    """``git init`` with no commits does not define a project.

    On this machine ``~/work`` is exactly that, sitting above two
    unrelated client projects. Accepting it merged them into one and hid the
    real repository nested below.
    """
    assert find_repository_root(repo) is None
    assert find_repository_root(repo, require_commits=False) == repo.resolve()


def test_a_nested_repository_wins_over_the_one_containing_it(repo: Path):
    _commit(repo, {"a.txt": "x\n"}, "outer", BASE)
    inner = repo / "vendor" / "lib"
    inner.mkdir(parents=True)
    _git(inner, "init", "-q", "-b", "main")
    _commit(inner, {"b.txt": "y\n"}, "inner", BASE)
    assert find_repository_root(inner) == inner.resolve()


def test_find_repository_root_returns_none_outside_a_repo(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert find_repository_root(plain) is None


def test_numstat_is_attached_to_the_right_commit(repo: Path):
    _commit(repo, {"a.txt": "1\n2\n3\n"}, "first", BASE)
    _commit(repo, {"b.txt": "1\n", "c.txt": "1\n2\n"}, "second", BASE + timedelta(minutes=5))

    commits = GitCollector(repo).commits_between(
        BASE - timedelta(hours=1), BASE + timedelta(hours=1)
    )
    by_message = {c.message: c for c in commits}
    assert by_message["first"].files_changed == 1
    assert by_message["first"].insertions == 3
    assert by_message["second"].files_changed == 2
    assert by_message["second"].insertions == 3
    assert sorted(by_message["second"].changed_files) == ["b.txt", "c.txt"]


def test_commit_messages_containing_separators_do_not_corrupt_parsing(repo: Path):
    _commit(repo, {"a.txt": "x\n"}, "fix: handle tab\tand pipe | chars", BASE)
    commits = GitCollector(repo).commits_between(
        BASE - timedelta(hours=1), BASE + timedelta(hours=1)
    )
    assert commits[0].message == "fix: handle tab\tand pipe | chars"
    assert commits[0].files_changed == 1


def test_commits_outside_the_window_are_excluded(repo: Path):
    _commit(repo, {"old.txt": "x\n"}, "old", BASE - timedelta(days=30))
    _commit(repo, {"new.txt": "x\n"}, "new", BASE)
    commits = GitCollector(repo).commits_between(
        BASE - timedelta(hours=1), BASE + timedelta(hours=1)
    )
    assert [c.message for c in commits] == ["new"]


def test_repository_metadata(repo: Path):
    _commit(repo, {"a.txt": "x\n"}, "first", BASE)
    (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    collector = GitCollector(repo)
    info = collector.collect(session_start=BASE, session_end=BASE + timedelta(minutes=1))
    assert info.is_repository is True
    assert info.current_branch == "main"
    assert info.repository_url is None  # no remote configured
    assert info.working_tree_dirty is True
    assert info.working_tree_changes == 1


def test_collect_without_a_session_window_skips_commit_lookup(repo: Path):
    _commit(repo, {"a.txt": "x\n"}, "first", BASE)
    info = GitCollector(repo).collect(session_start=None, session_end=None)
    assert info.commits_in_window == []
    assert info.correlations == []


def test_git_error_surfaces_instead_of_being_swallowed(tmp_path: Path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with pytest.raises(GitError):
        GitCollector(plain).current_branch()


# -- correlation ----------------------------------------------------------
def _touch(path: str, change: ChangeType = ChangeType.MODIFIED) -> FileTouch:
    return FileTouch(file_path=path, change_type=change)


def _commit_obj(files: list[str], when: datetime) -> GitCommit:
    return GitCommit(
        commit_hash="deadbeef", message="m", committed_at=when, changed_files=files
    )


def test_file_overlap_after_the_session_marks_likely_related(tmp_path: Path):
    root = tmp_path
    (root / "src").mkdir()
    (root / "src" / "form.tsx").write_text("x", encoding="utf-8")
    correlations = correlate_commits(
        commits=[_commit_obj(["src/form.tsx"], BASE + timedelta(minutes=8))],
        session_start=BASE - timedelta(hours=1),
        session_end=BASE,
        session_files=[_touch(str(root / "src" / "form.tsx"))],
        repo_root=root,
    )
    assert correlations[0].likely_related is True
    assert correlations[0].overlapping_files == ["src/form.tsx"]
    assert correlations[0].minutes_after_session_end == pytest.approx(8)


def test_time_proximity_alone_is_not_enough(tmp_path: Path):
    correlations = correlate_commits(
        commits=[_commit_obj(["docs/readme.md"], BASE + timedelta(minutes=2))],
        session_start=BASE - timedelta(hours=1),
        session_end=BASE,
        session_files=[_touch(str(tmp_path / "src" / "form.tsx"))],
        repo_root=tmp_path,
    )
    assert correlations[0].likely_related is False
    assert correlations[0].overlap_count == 0
    assert correlations[0].reasons  # the evidence is still reported


def test_files_only_read_do_not_create_an_association(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "form.tsx").write_text("x", encoding="utf-8")
    correlations = correlate_commits(
        commits=[_commit_obj(["src/form.tsx"], BASE + timedelta(minutes=2))],
        session_start=BASE - timedelta(hours=1),
        session_end=BASE,
        session_files=[_touch(str(tmp_path / "src" / "form.tsx"), ChangeType.READ)],
        repo_root=tmp_path,
    )
    assert correlations[0].likely_related is False


def test_commit_before_the_session_ended_is_not_flagged_by_time_alone(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    correlations = correlate_commits(
        commits=[_commit_obj(["a.txt"], BASE - timedelta(days=5))],
        session_start=BASE - timedelta(hours=1),
        session_end=BASE,
        session_files=[_touch(str(tmp_path / "a.txt"))],
        repo_root=tmp_path,
    )
    assert correlations[0].within_session_window is False
    assert correlations[0].likely_related is False


def test_session_with_no_file_changes_yields_no_associations(tmp_path: Path):
    correlations = correlate_commits(
        commits=[_commit_obj(["a.txt"], BASE + timedelta(minutes=1))],
        session_start=BASE - timedelta(hours=1),
        session_end=BASE,
        session_files=[],
        repo_root=tmp_path,
    )
    assert correlations[0].likely_related is False
    assert correlations[0].overlap_count == 0


def test_commit_naming_the_session_id_is_direct_evidence(tmp_path: Path):
    """Claude Code stamps the short session id into its checkpoint commits.

    That is the strongest association available and must not need file overlap.
    """
    session_id = "f50b5adf-9b3c-4722-8f0c-fc58d90c3a15"
    commit = _commit_obj(["unrelated.txt"], BASE + timedelta(minutes=5))
    commit.message = "WIP: Claude Code rate-limit checkpoint (f50b5adf)"
    correlations = correlate_commits(
        commits=[commit],
        session_start=BASE - timedelta(hours=1),
        session_end=BASE,
        session_files=[],
        repo_root=tmp_path,
        session_id=session_id,
    )
    assert correlations[0].likely_related is True
    assert any("names this session id" in r for r in correlations[0].reasons)


def test_a_different_session_id_in_the_message_is_not_evidence(tmp_path: Path):
    commit = _commit_obj(["unrelated.txt"], BASE + timedelta(minutes=5))
    commit.message = "WIP: Claude Code rate-limit checkpoint (deadbeef)"
    correlations = correlate_commits(
        commits=[commit],
        session_start=BASE - timedelta(hours=1),
        session_end=BASE,
        session_files=[],
        repo_root=tmp_path,
        session_id="f50b5adf-9b3c-4722-8f0c-fc58d90c3a15",
    )
    assert correlations[0].likely_related is False


def test_commit_predating_the_session_says_so(tmp_path: Path):
    correlations = correlate_commits(
        commits=[_commit_obj(["a.txt"], BASE - timedelta(hours=3))],
        session_start=BASE - timedelta(hours=1),
        session_end=BASE,
        session_files=[],
        repo_root=tmp_path,
    )
    reasons = correlations[0].reasons
    assert any("before the session started" in r for r in reasons)
    assert not any("before the session ended" in r for r in reasons)
