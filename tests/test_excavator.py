"""
Integration tests for the git history excavator.
Covers deleted blocks, ancient TODOs (via fake old timestamps),
reverted dreams, deduplication, and parallel processing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

try:
    import git

    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False

from archaeologist.models import ArtifactType

pytestmark = pytest.mark.skipif(not GIT_AVAILABLE, reason="GitPython not installed")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_repo(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(str(tmp_path))
    repo.config_writer().set_value("user", "name", "Dev").release()
    repo.config_writer().set_value("user", "email", "dev@test.com").release()
    return repo


def _commit(
    repo: git.Repo, message: str, files: dict, *, author_date: datetime | None = None
) -> git.Commit:
    """Write files and commit."""
    root = Path(repo.working_dir)
    for name, content in files.items():
        fp = root / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            fp.unlink(missing_ok=True)
        else:
            fp.write_text(content)
    repo.git.add("-A")
    return repo.index.commit(message, author_date=author_date, commit_date=author_date)


# ─────────────────────────────────────────────────────────────────────────────
# Excavator init
# ─────────────────────────────────────────────────────────────────────────────


class TestExcavatorInit:
    def test_count_commits(self, tmp_python_repo: Path):
        from archaeologist.excavator import Excavator

        ex = Excavator(str(tmp_python_repo), max_commits=50)
        assert ex.count_commits() >= 3

    def test_invalid_path_raises(self, tmp_path: Path):
        from archaeologist.excavator import Excavator

        ex = Excavator(str(tmp_path / "nonexistent"))
        with pytest.raises(RuntimeError):
            ex._get_repo()


# ─────────────────────────────────────────────────────────────────────────────
# Deleted blocks
# ─────────────────────────────────────────────────────────────────────────────


class TestDeletedBlocks:
    def test_finds_deleted_file(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Add module", {"module.py": "def big():\n    pass\n" * 10})
        _commit(repo, "Remove module", {"module.py": None})

        from archaeologist.excavator import Excavator

        ex = Excavator(str(tmp_path), max_commits=20)
        artifacts = list(ex.excavate_deleted_blocks())
        assert any("module.py" in a.title for a in artifacts)

    def test_finds_deleted_block_within_file(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Add file", {"app.py": "x = 1\n"})

        big_block = "def temporary():\n" + "    pass\n" * 8
        _commit(repo, "Add block", {"app.py": "x = 1\n" + big_block})
        _commit(repo, "Remove block", {"app.py": "x = 1\n"})

        from archaeologist.excavator import Excavator

        artifacts = list(Excavator(str(tmp_path), max_commits=20).excavate_deleted_blocks())
        assert len(artifacts) >= 1

    def test_artifact_type(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Add", {"f.py": "def x():\n    pass\n" * 6})
        _commit(repo, "Del", {"f.py": None})

        from archaeologist.excavator import Excavator

        for a in Excavator(str(tmp_path), max_commits=10).excavate_deleted_blocks():
            assert a.type == ArtifactType.DELETED_BLOCK

    def test_has_commit_metadata(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Add", {"f.py": "def x():\n    pass\n" * 6})
        _commit(repo, "Del", {"f.py": None})

        from archaeologist.excavator import Excavator

        for a in Excavator(str(tmp_path), max_commits=10).excavate_deleted_blocks():
            assert a.commit_hash is not None
            assert a.author is not None
            assert a.date is not None

    def test_progress_callback_called(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "A", {"f.py": "x=1\n"})
        _commit(repo, "B", {"f.py": "x=2\n"})

        from archaeologist.excavator import Excavator

        calls: list[tuple] = []
        list(
            Excavator(str(tmp_path), max_commits=10).excavate_deleted_blocks(
                progress=lambda d, t: calls.append((d, t))
            )
        )
        assert len(calls) > 0

    def test_no_duplicates(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        content = "def fn():\n    pass\n" * 8
        _commit(repo, "Add", {"mod.py": content})
        _commit(repo, "Del", {"mod.py": None})

        from archaeologist.excavator import Excavator

        artifacts = list(Excavator(str(tmp_path), max_commits=10).excavate_deleted_blocks())
        [a.title for a in artifacts]
        # Same file should not appear twice with identical title+hash
        combos = [(a.title, a.commit_hash) for a in artifacts]
        assert len(combos) == len(set(combos))

    def test_parallel_processing(self, tmp_path: Path):
        """Results should be the same with 1 worker vs many workers."""
        repo = _make_repo(tmp_path)
        for i in range(5):
            _commit(repo, f"step {i}", {f"f{i}.py": "def x():\n    pass\n" * 6})
        for i in range(5):
            _commit(repo, f"del {i}", {f"f{i}.py": None})

        from archaeologist.excavator import Excavator

        a1 = sorted(
            a.title
            for a in Excavator(str(tmp_path), max_commits=20, workers=1).excavate_deleted_blocks()
        )
        a4 = sorted(
            a.title
            for a in Excavator(str(tmp_path), max_commits=20, workers=4).excavate_deleted_blocks()
        )
        assert a1 == a4


# ─────────────────────────────────────────────────────────────────────────────
# Ancient TODOs
# ─────────────────────────────────────────────────────────────────────────────


class TestAncientTodos:
    def test_finds_old_todo(self, tmp_path: Path):
        """Fake an old commit date so the TODO is older than 180 days."""
        repo = _make_repo(tmp_path)
        old_date = datetime.now(timezone.utc) - timedelta(days=400)
        _commit(
            repo,
            "Initial",
            {"app.py": "# TODO: rewrite this entire module\nx = 1\n"},
            author_date=old_date,
        )

        from archaeologist.excavator import Excavator

        artifacts = list(Excavator(str(tmp_path)).find_ancient_todos())
        assert any("TODO" in a.title for a in artifacts)

    def test_ignores_recent_todo(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Add todo", {"f.py": "# TODO: fix this\nx=1\n"})

        from archaeologist.excavator import Excavator

        artifacts = list(Excavator(str(tmp_path)).find_ancient_todos())
        # Recent repo — no ancient TODOs
        assert len(artifacts) == 0

    def test_deduplicates_same_todo(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        old = datetime.now(timezone.utc) - timedelta(days=500)
        _commit(
            repo,
            "Add",
            {"f.py": "# TODO: fix the thing\n# TODO: fix the thing\nx=1\n"},
            author_date=old,
        )

        from archaeologist.excavator import Excavator

        artifacts = list(Excavator(str(tmp_path)).find_ancient_todos())
        messages = [a.description for a in artifacts]
        # Same message should not appear twice for the same file
        seen = set()
        for m in messages:
            key = m[:50]
            assert key not in seen, f"Duplicate TODO: {key}"
            seen.add(key)

    def test_artifact_type(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        old = datetime.now(timezone.utc) - timedelta(days=400)
        _commit(repo, "Old", {"app.py": "# FIXME: broken\nx=1\n"}, author_date=old)

        from archaeologist.excavator import Excavator

        for a in Excavator(str(tmp_path)).find_ancient_todos():
            assert a.type == ArtifactType.ANCIENT_TODO

    def test_progress_callback(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Init", {"f.py": "x=1\n"})

        from archaeologist.excavator import Excavator

        calls: list[tuple] = []
        list(
            Excavator(str(tmp_path)).find_ancient_todos(progress=lambda d, t: calls.append((d, t)))
        )
        assert len(calls) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Reverted dreams
# ─────────────────────────────────────────────────────────────────────────────


class TestRevertedDreams:
    def test_finds_revert_commit(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Init", {"f.py": "x=1\n"})
        _commit(repo, "Add feature", {"f.py": "x=2\n"})
        _commit(repo, 'Revert "Add feature"', {"f.py": "x=1\n"})

        from archaeologist.excavator import Excavator

        artifacts = list(Excavator(str(tmp_path), max_commits=20).find_reverted_dreams())
        assert len(artifacts) == 1
        assert "Add feature" in artifacts[0].title

    def test_no_reverts_in_clean_repo(self, tmp_python_repo: Path):
        from archaeologist.excavator import Excavator

        artifacts = list(Excavator(str(tmp_python_repo), max_commits=50).find_reverted_dreams())
        assert len(artifacts) == 0

    def test_deduplicates_same_revert(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Init", {"f.py": "x=1\n"})
        _commit(repo, "Feat", {"f.py": "x=2\n"})
        _commit(repo, 'Revert "Feat"', {"f.py": "x=1\n"})
        # Try to trigger it again with same title
        _commit(repo, "Feat again", {"f.py": "x=3\n"})
        _commit(repo, 'Revert "Feat"', {"f.py": "x=1\n"})

        from archaeologist.excavator import Excavator

        artifacts = list(Excavator(str(tmp_path), max_commits=20).find_reverted_dreams())
        titles = [a.title for a in artifacts]
        assert len(titles) == len(set(titles)), "Duplicate revert artifacts"

    def test_artifact_type(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit(repo, "Init", {"f.py": "x=1\n"})
        _commit(repo, "Add", {"f.py": "x=2\n"})
        _commit(repo, 'Revert "Add"', {"f.py": "x=1\n"})

        from archaeologist.excavator import Excavator

        for a in Excavator(str(tmp_path), max_commits=10).find_reverted_dreams():
            assert a.type == ArtifactType.REVERTED_DREAM
