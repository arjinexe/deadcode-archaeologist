"""
Excavator — mines git history for lost, deleted, and abandoned code.

Thread-safety fix: GitPython Repo objects are NOT thread-safe.
Each worker thread now opens its own Repo instance from the path string,
so there are no shared mutable git objects across threads.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .models import Artifact, ArtifactType

try:
    import git

    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False


MIN_DELETED_LINES = 5
MAX_SNIPPET_LINES = 30
MAX_WORKERS = 8

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".java",
    ".c",
    ".cpp",
    ".cs",
    ".go",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".rs",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".lua",
    ".ex",
    ".exs",
    ".elm",
    ".ml",
}

INTERESTING_PATTERNS = [
    re.compile(r"def\s+\w+\s*\("),
    re.compile(r"class\s+\w+"),
    re.compile(r"function\s+\w+\s*\("),
    re.compile(r"async\s+function"),
    re.compile(r"const\s+\w+\s*=\s*\("),
    re.compile(r"#\s*TODO|//\s*TODO"),
    re.compile(r"FIXME|HACK|XXX|BUG"),
    re.compile(r"raise\s+NotImplemented"),
    re.compile(r"^\s*pass\s*$", re.MULTILINE),
]

ProgressFn = Callable[[int, int], None]


def _is_code_file(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXTENSIONS


def _score_deleted_block(lines: list[str], age_days: int) -> int:
    score = min(len(lines) * 2, 30)
    joined = "\n".join(lines)
    for pat in INTERESTING_PATTERNS:
        if pat.search(joined):
            score += 5
    if age_days > 365 * 3:
        score += 25
    elif age_days > 365:
        score += 15
    elif age_days > 90:
        score += 8
    return min(score, 100)


def _group_into_blocks(items: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    if not items:
        return []
    blocks, current = [], [items[0]]
    for i in range(1, len(items)):
        if items[i][0] - items[i - 1][0] <= 2:
            current.append(items[i])
        else:
            if len(current) >= MIN_DELETED_LINES:
                blocks.append(current)
            current = [items[i]]
    if len(current) >= MIN_DELETED_LINES:
        blocks.append(current)
    return blocks


def _parse_deleted_blocks(diff_text: str) -> list[tuple[int, list[str]]]:
    results: list[tuple[int, list[str]]] = []
    lineno = 0
    current: list[tuple[int, str]] = []

    for line in diff_text.splitlines():
        if line.startswith("@@"):
            if current:
                for blk in _group_into_blocks(current):
                    results.append((blk[0][0], [line for _, line in blk]))
                current = []
            m = re.search(r"-(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("-") and not line.startswith("---"):
            current.append((lineno, line[1:]))
            lineno += 1
        elif not line.startswith("+"):
            lineno += 1

    if current:
        for blk in _group_into_blocks(current):
            results.append((blk[0][0], [line for _, line in blk]))
    return results


def _age_days(commit: git.Commit) -> int:
    now = datetime.now(timezone.utc)
    dt = commit.committed_datetime
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    res = (now - dt).days
    return int(res)


# ─────────────────────────────────────────────────────────────────────────────
# Per-commit processing  (thread-safe: opens own Repo from path string)
# ─────────────────────────────────────────────────────────────────────────────


def _process_commit_safe(repo_path: str, hexsha: str, age_days_val: int) -> list[Artifact]:
    """
    Open a fresh Repo instance per call so there are no shared git objects
    between threads.  GitPython's internal state (index, config, etc.) is
    NOT thread-safe when shared.
    """
    results: list[Artifact] = []
    try:
        repo = git.Repo(repo_path)
        commit = repo.commit(hexsha)
    except Exception:
        return results

    if not commit.parents:
        return results

    parent = commit.parents[0]
    try:
        diffs = parent.diff(commit, create_patch=True)
    except Exception:
        return results

    for diff in diffs:
        path = diff.b_path or diff.a_path or ""

        if diff.deleted_file:
            try:
                a_blob = diff.a_blob
                a_path = diff.a_path or ""
                if a_blob is None or not a_path:
                    continue
                raw = a_blob.data_stream.read().decode("utf-8", errors="replace")
                lines = raw.splitlines()
                if len(lines) >= MIN_DELETED_LINES and _is_code_file(a_path):
                    snippet = "\n".join(lines[:MAX_SNIPPET_LINES])
                    if len(lines) > MAX_SNIPPET_LINES:
                        snippet += f"\n... ({len(lines) - MAX_SNIPPET_LINES} more lines)"
                    results.append(
                        Artifact(
                            type=ArtifactType.DELETED_BLOCK,
                            title=f"Entire file erased: {Path(a_path).name}",
                            description=(
                                f"{len(lines)}-line file committed by {commit.author.name} "
                                f"on {commit.committed_datetime.strftime('%Y-%m-%d')} "
                                f"— then deleted entirely."
                            ),
                            code_snippet=snippet,
                            file_path=a_path,
                            author=commit.author.name,
                            date=commit.committed_datetime,
                            commit_hash=commit.hexsha,
                            tragedy_score=_score_deleted_block(lines, age_days_val),
                            age_days=age_days_val,
                            tags=["entire-file", "deleted"],
                        )
                    )
            except Exception:
                pass
            continue

        if not _is_code_file(path):
            continue

        try:
            raw_diff = diff.diff
            if raw_diff is None:
                continue
            diff_text = raw_diff.decode("utf-8", errors="replace") if isinstance(raw_diff, bytes) else raw_diff
        except Exception:
            continue

        for start_line, lines in _parse_deleted_blocks(diff_text):
            score = _score_deleted_block(lines, age_days_val)
            if score < 10:
                continue
            results.append(
                Artifact(
                    type=ArtifactType.DELETED_BLOCK,
                    title=f"Code block erased from {Path(path).name}",
                    description=(
                        f"{len(lines)}-line block deleted by {commit.author.name} "
                        f"on {commit.committed_datetime.strftime('%Y-%m-%d')}."
                    ),
                    code_snippet="\n".join(lines[:MAX_SNIPPET_LINES]),
                    file_path=path,
                    line_number=start_line,
                    author=commit.author.name,
                    date=commit.committed_datetime,
                    commit_hash=commit.hexsha,
                    tragedy_score=score,
                    age_days=age_days_val,
                    tags=["deleted-block"],
                )
            )

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Excavator
# ─────────────────────────────────────────────────────────────────────────────


class Excavator:
    """
    Mines a git repository's history for lost, deleted, and abandoned code.

    Parallelism: excavate_deleted_blocks() processes commits concurrently.
    Each worker opens its own git.Repo to avoid shared-state crashes.
    """

    def __init__(
        self,
        repo_path: str,
        max_commits: int = 500,
        verbose: bool = False,
        workers: int = MAX_WORKERS,
    ) -> None:
        if not GIT_AVAILABLE:
            raise ImportError(
                "GitPython is required for git history analysis.\n"
                "Install it with: pip install gitpython"
            )
        self.repo_path = str(Path(repo_path).resolve())
        self.max_commits = max_commits
        self.verbose = verbose
        self.workers = workers

    def _get_repo(self) -> git.Repo:
        try:
            return git.Repo(self.repo_path)
        except Exception as e:
            raise RuntimeError(f"Cannot open git repository at {self.repo_path}: {e}") from e

    # ── Deleted blocks ─────────────────────────────────────────────────────

    def excavate_deleted_blocks(
        self,
        progress: ProgressFn | None = None,
    ) -> Iterator[Artifact]:
        """
        Walk git history and yield deleted-code artifacts.
        Uses a thread pool; each thread opens its own Repo for safety.
        """
        repo = self._get_repo()
        try:
            commits = list(repo.iter_commits(max_count=self.max_commits))
        except Exception as e:
            if self.verbose:
                print(f"[excavator] Cannot iterate commits: {e}")
            return

        # work_items excludes the root commit (no parents -> no diff to inspect).
        # Use len(work_items) as the progress total so the bar reaches 100%.
        work_items = [(self.repo_path, c.hexsha, _age_days(c)) for c in commits if c.parents]
        total = len(work_items)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(_process_commit_safe, rp, sha, age): idx
                for idx, (rp, sha, age) in enumerate(work_items)
            }
            done = 0
            seen_blocks: set[tuple[str, int, str]] = set()
            for future in as_completed(futures):
                done += 1
                if progress:
                    progress(done, total)
                try:
                    for artifact in future.result():
                        if artifact.type == ArtifactType.DELETED_BLOCK:
                            block_key = (
                                artifact.file_path,
                                artifact.line_number or 0,
                                artifact.code_snippet[:80],
                            )
                            if block_key in seen_blocks:
                                continue
                            seen_blocks.add(block_key)
                        yield artifact
                except Exception as e:
                    if self.verbose:
                        print(f"[excavator] Worker error: {e}")

    # ── Ancient TODOs ──────────────────────────────────────────────────────

    def find_ancient_todos(
        self,
        progress: ProgressFn | None = None,
    ) -> Iterator[Artifact]:
        """Find TODO/FIXME/HACK comments older than 180 days via git blame."""
        repo = self._get_repo()
        pat = re.compile(
            r"^\s*(?:#|//|/\*|--|\*)\s*.*\b(TODO|FIXME|HACK|XXX|BUG|OPTIMIZE)\b\s*[:\-]?\s*(.*)",
            re.IGNORECASE | re.MULTILINE,
        )
        seen: set[tuple[str, str]] = set()

        files = [
            fp
            for fp in Path(self.repo_path).rglob("*")
            if fp.is_file() and fp.suffix.lower() in CODE_EXTENSIONS and ".git" not in fp.parts
        ]
        total = len(files)

        for idx, filepath in enumerate(files):
            if progress:
                progress(idx + 1, total)
            try:
                rel = str(filepath.relative_to(self.repo_path))
                blame = repo.blame("HEAD", rel)
            except Exception:
                continue

            if blame is None:
                continue
            for blame_entry in blame:
                commit_obj = blame_entry[0]
                blame_lines = blame_entry[1]
                if not isinstance(commit_obj, git.Commit):
                    continue
                if not isinstance(blame_lines, (list, range)):
                    continue
                age = _age_days(commit_obj)
                if age < 180:
                    continue
                for line_bytes in blame_lines:
                    try:
                        line: str = (
                            line_bytes.decode("utf-8", errors="replace")
                            if isinstance(line_bytes, bytes)
                            else str(line_bytes)
                        )
                    except Exception:
                        continue
                    m = pat.search(line)
                    if not m:
                        continue

                    marker = m.group(1).upper()
                    message = m.group(2).strip()[:120]
                    key = (rel, message[:60])
                    if key in seen:
                        continue
                    seen.add(key)

                    years = age // 365
                    months = (age % 365) // 30
                    age_str = (
                        f"{years} year{'s' if years != 1 else ''}"
                        if years
                        else f"{months} month{'s' if months != 1 else ''}"
                    )

                    yield Artifact(
                        type=ArtifactType.ANCIENT_TODO,
                        title=f"{marker} rotting for {age_str}",
                        description=(
                            f'"{message}" — left by {commit_obj.author.name} '
                            f"on {commit_obj.committed_datetime.strftime('%Y-%m-%d')} "
                            f"and never addressed."
                        ),
                        code_snippet=line.strip(),
                        file_path=rel,
                        author=commit_obj.author.name,
                        date=commit_obj.committed_datetime,
                        commit_hash=commit_obj.hexsha,
                        tragedy_score=min(20 + (age // 30) * 3, 100),
                        age_days=age,
                        tags=["todo", marker.lower()],
                    )

    # ── Reverted dreams ────────────────────────────────────────────────────

    def find_reverted_dreams(self) -> Iterator[Artifact]:
        """Find commits that were reverted shortly after being merged."""
        repo = self._get_repo()
        pat = re.compile(r'[Rr]evert\s+"?(.+?)"?\s*(?:\n|$)', re.MULTILINE)
        seen: set[str] = set()

        try:
            commits = list(repo.iter_commits(max_count=self.max_commits))
        except Exception:
            return

        for commit in commits:
            msg = commit.message
            if isinstance(msg, bytes):
                msg = msg.decode("utf-8", errors="replace")
            m = pat.search(msg)
            if not m:
                continue
            original = m.group(1).strip()
            key = original[:80]
            if key in seen:
                continue
            seen.add(key)

            age = _age_days(commit)
            try:
                files_affected = commit.stats.files
                total_changes = commit.stats.total.get("lines", 0)
            except Exception:
                files_affected = {}
                total_changes = 0

            yield Artifact(
                type=ArtifactType.REVERTED_DREAM,
                title=f'Reverted: "{original[:60]}"',
                description=(
                    f"{commit.author.name} merged something, then took it all back. "
                    f"{len(files_affected)} file(s) affected, "
                    f"{total_changes} total line changes undone."
                ),
                code_snippet=msg.strip()[:300],
                file_path="(multiple files)",
                author=commit.author.name,
                date=commit.committed_datetime,
                commit_hash=commit.hexsha,
                tragedy_score=min(30 + len(files_affected) * 5 + (age // 60) * 2, 100),
                age_days=age,
                tags=["reverted", "dream"],
            )

    # ── Helpers ────────────────────────────────────────────────────────────

    def count_commits(self) -> int:
        try:
            return sum(1 for _ in self._get_repo().iter_commits(max_count=self.max_commits))
        except Exception:
            return 0
