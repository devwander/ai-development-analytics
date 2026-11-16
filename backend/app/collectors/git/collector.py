"""Collect repository facts with local git commands.

The collector answers two separate questions and never conflates them:

* what does the repository contain (branch, commits, diffs)?
* which commits *plausibly* relate to a Claude Code session?

The second is reported as ``likely_related`` with the evidence that produced it.
Nothing here claims a commit was authored by the AI.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.collectors.claude.parser import parse_timestamp
from app.schemas.observed import FileTouch, GitCommit, GitCorrelation, GitInfo

#: ASCII unit/record separators: safe against any character in a commit message.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
# The record separator must LEAD the header: with --numstat, git prints the
# formatted header first and the file stats after it. Putting the separator
# at the end would attach every commit's numstat block to the next commit.
_LOG_FORMAT = _RECORD_SEP + _FIELD_SEP.join(["%H", "%an", "%ae", "%aI", "%cI", "%s"])

DEFAULT_TIMEOUT_SECONDS = 60


class GitError(RuntimeError):
    pass


@dataclass
class GitRunner:
    repo_path: Path
    timeout: int = DEFAULT_TIMEOUT_SECONDS

    def run(self, *args: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.repo_path), *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except OSError as exc:
            raise GitError(f"git is not runnable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git timed out after {self.timeout}s") from exc
        if completed.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} exited {completed.returncode}: "
                f"{completed.stderr.strip()[:300]}"
            )
        return completed.stdout


def find_repository_root(
    path: str | Path, *, require_commits: bool = True
) -> Path | None:
    """The git repository containing ``path``, if it is a usable one.

    ``require_commits`` rejects an initialised-but-empty repository. That is not
    pedantry: on this machine ``~/work`` is an empty repo that sits above
    two unrelated client projects, and accepting it merged them into a single
    "project" while hiding the real repository nested below.
    """
    path = Path(path)
    if not path.is_dir():
        return None
    runner = GitRunner(path)
    try:
        output = runner.run("rev-parse", "--show-toplevel")
    except GitError:
        return None
    root = output.strip()
    if not root:
        return None
    if require_commits:
        try:
            runner.run("rev-parse", "--verify", "HEAD")
        except GitError:
            return None
    return Path(root)


class GitCollector:
    def __init__(self, repo_root: Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.repo_root = Path(repo_root)
        self.runner = GitRunner(self.repo_root, timeout=timeout)

    # -- repository level -------------------------------------------------
    def current_branch(self) -> str | None:
        branch = self.runner.run("rev-parse", "--abbrev-ref", "HEAD").strip()
        return branch or None

    def remote_url(self) -> str | None:
        try:
            return self.runner.run("remote", "get-url", "origin").strip() or None
        except GitError:
            return None

    def working_tree_changes(self) -> list[str]:
        output = self.runner.run("status", "--porcelain=v1")
        return [line for line in output.splitlines() if line.strip()]

    def commits_between(
        self, since: datetime, until: datetime, max_commits: int = 200
    ) -> list[GitCommit]:
        """Commits authored in ``[since, until]``, across all branches.

        ``--all`` matters: a session's work often lands on a branch that is no
        longer checked out by the time collection runs.
        """
        try:
            output = self.runner.run(
                "log",
                "--all",
                f"--since={since.isoformat()}",
                f"--until={until.isoformat()}",
                f"--max-count={max_commits}",
                "--no-merges",
                f"--pretty=format:{_LOG_FORMAT}",
                "--numstat",
            )
        except GitError:
            return []
        return _parse_log_with_numstat(output)

    def collect(
        self,
        *,
        session_start: datetime | None,
        session_end: datetime | None,
        session_files: list[FileTouch] | None = None,
        session_branches: list[str] | None = None,
        session_id: str | None = None,
        window_before_minutes: int = 30,
        window_after_minutes: int = 120,
    ) -> GitInfo:
        try:
            branch = self.current_branch()
            changes = self.working_tree_changes()
        except GitError as exc:
            return GitInfo(
                is_repository=True,
                repository_root=str(self.repo_root),
                error=str(exc),
            )

        info = GitInfo(
            is_repository=True,
            repository_root=str(self.repo_root),
            repository_url=self.remote_url(),
            current_branch=branch,
            session_branches=list(session_branches or []),
            working_tree_dirty=bool(changes),
            working_tree_changes=len(changes),
        )

        if session_start is None or session_end is None:
            info.commits_in_window = []
            return info

        window_start = session_start - timedelta(minutes=window_before_minutes)
        window_end = session_end + timedelta(minutes=window_after_minutes)
        info.commits_in_window = self.commits_between(window_start, window_end)
        info.correlations = correlate_commits(
            commits=info.commits_in_window,
            session_start=session_start,
            session_end=session_end,
            session_files=session_files or [],
            repo_root=self.repo_root,
            session_id=session_id,
        )
        return info


def correlate_commits(
    *,
    commits: list[GitCommit],
    session_start: datetime,
    session_end: datetime,
    session_files: list[FileTouch],
    repo_root: Path,
    session_id: str | None = None,
) -> list[GitCorrelation]:
    """Associate commits with a session using time and file overlap.

    A commit is flagged ``likely_related`` when it lands in the session window
    *and* shares at least one file with what Claude actually touched, or when
    the commit message names the session id outright. Time alone is treated as
    weak evidence and reported without the flag.
    """
    modified = {
        _relative_to(path.file_path, repo_root)
        for path in session_files
        if path.change_type.value in {"modified", "created", "deleted", "renamed"}
    }
    modified.discard(None)
    # Claude Code stamps the short session id into its own checkpoint commits.
    short_id = session_id.split("-")[0] if session_id else None

    correlations: list[GitCorrelation] = []
    for commit in commits:
        when = commit.committed_at or commit.authored_at
        reasons: list[str] = []
        within = False
        minutes_after = None
        if when is not None:
            within = session_start <= when <= session_end
            minutes_after = (when - session_end).total_seconds() / 60
            if within:
                reasons.append("commit timestamp falls inside the session window")
            elif minutes_after >= 0:
                reasons.append(
                    f"commit made {minutes_after:.0f} min after the session ended"
                )
            else:
                before_start = (session_start - when).total_seconds() / 60
                reasons.append(
                    f"commit made {before_start:.0f} min before the session started"
                )

        overlap = sorted(set(commit.changed_files) & modified)
        if overlap:
            reasons.append(
                f"{len(overlap)} file(s) also modified by Claude during the session"
            )

        names_session = bool(
            short_id and session_id and (
                session_id in commit.message or short_id in commit.message
            )
        )
        if names_session:
            reasons.append("commit message names this session id")

        after_or_during = within or (minutes_after is not None and minutes_after >= 0)
        correlations.append(
            GitCorrelation(
                commit_hash=commit.commit_hash,
                likely_related=names_session or (bool(overlap) and after_or_during),
                overlapping_files=overlap,
                overlap_count=len(overlap),
                minutes_after_session_end=minutes_after,
                within_session_window=within,
                reasons=reasons,
            )
        )
    return correlations


def _relative_to(file_path: str, repo_root: Path) -> str | None:
    try:
        return str(Path(file_path).resolve().relative_to(repo_root.resolve()))
    except (ValueError, OSError):
        return None


def _parse_log_with_numstat(output: str) -> list[GitCommit]:
    commits: list[GitCommit] = []
    for chunk in output.split(_RECORD_SEP):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        header, _, body = chunk.partition("\n")
        fields = header.split(_FIELD_SEP)
        if len(fields) < 6:
            continue
        commit_hash, author_name, author_email, authored, committed, subject = fields[:6]
        insertions = deletions = 0
        changed_files: list[str] = []
        for line in body.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, removed, name = parts
            # "-" appears for binary files; count the file, not the lines.
            if added.isdigit():
                insertions += int(added)
            if removed.isdigit():
                deletions += int(removed)
            changed_files.append(name)
        commits.append(
            GitCommit(
                commit_hash=commit_hash.strip(),
                author_name=author_name or None,
                author_email=author_email or None,
                message=subject,
                authored_at=parse_timestamp(authored),
                committed_at=parse_timestamp(committed),
                files_changed=len(changed_files),
                insertions=insertions,
                deletions=deletions,
                changed_files=changed_files,
            )
        )
    return commits
