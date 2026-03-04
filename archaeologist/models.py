"""
Core data models for DeadCode Archaeologist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ArtifactType(Enum):
    DELETED_BLOCK = "deleted_block"  # Code written then erased from history
    DEAD_FUNCTION = "dead_function"  # Defined but never called
    ORPHANED_COMMENT = "orphaned_comment"  # Comment older than surrounding code
    ANCIENT_TODO = "ancient_todo"  # TODO left to rot for years
    GHOST_IMPORT = "ghost_import"  # Import that serves no one
    REVERTED_DREAM = "reverted_dream"  # Branch merged then immediately reverted
    LONE_VARIABLE = "lone_variable"  # Assigned but never read


ARTIFACT_EMOJI = {
    ArtifactType.DELETED_BLOCK: "🪦",
    ArtifactType.DEAD_FUNCTION: "👻",
    ArtifactType.ORPHANED_COMMENT: "💬",
    ArtifactType.ANCIENT_TODO: "⏳",
    ArtifactType.GHOST_IMPORT: "🌫️",
    ArtifactType.REVERTED_DREAM: "💔",
    ArtifactType.LONE_VARIABLE: "🗿",
}

ARTIFACT_EPITAPHS = {
    ArtifactType.DELETED_BLOCK: "Written with hope. Deleted in silence.",
    ArtifactType.DEAD_FUNCTION: "Called by no one. Mourned by few.",
    ArtifactType.ORPHANED_COMMENT: "The code changed. The comment stayed.",
    ArtifactType.ANCIENT_TODO: "TODO: deal with this. (Never did.)",
    ArtifactType.GHOST_IMPORT: "Imported for a purpose long forgotten.",
    ArtifactType.REVERTED_DREAM: "It lived. It was merged. It was undone.",
    ArtifactType.LONE_VARIABLE: "Assigned with care. Read by no one.",
}


@dataclass
class Artifact:
    """A single archaeological finding — a piece of code lost to time."""

    type: ArtifactType
    title: str
    description: str
    code_snippet: str
    file_path: str
    line_number: int | None = None
    author: str | None = None
    date: datetime | None = None
    commit_hash: str | None = None
    tragedy_score: int = 0  # 0–100, how tragic is this discovery
    age_days: int | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def emoji(self) -> str:
        return ARTIFACT_EMOJI.get(self.type, "🔍")

    @property
    def epitaph(self) -> str:
        return ARTIFACT_EPITAPHS.get(self.type, "Lost to time.")

    @property
    def short_hash(self) -> str:
        if self.commit_hash:
            return self.commit_hash[:7]
        return "unknown"

    @property
    def tragedy_label(self) -> str:
        if self.tragedy_score >= 80:
            return "💀 Devastating"
        elif self.tragedy_score >= 60:
            return "😢 Very Tragic"
        elif self.tragedy_score >= 40:
            return "😔 Tragic"
        elif self.tragedy_score >= 20:
            return "😐 Melancholic"
        else:
            return "🙂 Bittersweet"


@dataclass
class ExcavationReport:
    """The full result of analyzing a repository."""

    repo_path: str
    repo_name: str
    total_commits_scanned: int
    total_files_analyzed: int
    artifacts: list[Artifact] = field(default_factory=list)
    scan_duration_seconds: float = 0.0
    scanned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_artifacts(self) -> int:
        return len(self.artifacts)

    @property
    def average_tragedy_score(self) -> float:
        if not self.artifacts:
            return 0.0
        return sum(a.tragedy_score for a in self.artifacts) / len(self.artifacts)

    @property
    def most_tragic(self) -> Artifact | None:
        if not self.artifacts:
            return None
        return max(self.artifacts, key=lambda a: a.tragedy_score)

    @property
    def artifacts_by_type(self) -> dict:
        result: dict = {}
        for artifact in self.artifacts:
            result.setdefault(artifact.type, []).append(artifact)
        return result

    def sorted_by_tragedy(self) -> list[Artifact]:
        return sorted(self.artifacts, key=lambda a: a.tragedy_score, reverse=True)
