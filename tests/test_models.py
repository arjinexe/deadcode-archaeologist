"""Tests for data models."""

import pytest

from archaeologist.models import (
    ARTIFACT_EMOJI,
    ARTIFACT_EPITAPHS,
    Artifact,
    ArtifactType,
    ExcavationReport,
)


def make_artifact(**kwargs):
    defaults = dict(
        type=ArtifactType.DEAD_FUNCTION,
        title="def orphan() — called by no one",
        description="A sad function.",
        code_snippet="def orphan():\n    pass",
        file_path="src/module.py",
        tragedy_score=50,
    )
    defaults.update(kwargs)
    return Artifact(**defaults)


class TestArtifact:
    def test_emoji_property(self):
        for atype in ArtifactType:
            artifact = make_artifact(type=atype)
            assert artifact.emoji in ARTIFACT_EMOJI[atype]

    def test_epitaph_property(self):
        for atype in ArtifactType:
            artifact = make_artifact(type=atype)
            assert len(artifact.epitaph) > 0
            assert artifact.epitaph == ARTIFACT_EPITAPHS[atype]

    def test_short_hash_with_commit(self):
        artifact = make_artifact(commit_hash="abc123def456")
        assert artifact.short_hash == "abc123d"

    def test_short_hash_without_commit(self):
        artifact = make_artifact(commit_hash=None)
        assert artifact.short_hash == "unknown"

    def test_tragedy_label_devastating(self):
        artifact = make_artifact(tragedy_score=90)
        assert "Devastating" in artifact.tragedy_label

    def test_tragedy_label_very_tragic(self):
        artifact = make_artifact(tragedy_score=70)
        assert "Very Tragic" in artifact.tragedy_label

    def test_tragedy_label_tragic(self):
        artifact = make_artifact(tragedy_score=50)
        assert "Tragic" in artifact.tragedy_label

    def test_tragedy_label_melancholic(self):
        artifact = make_artifact(tragedy_score=30)
        assert "Melancholic" in artifact.tragedy_label

    def test_tragedy_label_bittersweet(self):
        artifact = make_artifact(tragedy_score=10)
        assert "Bittersweet" in artifact.tragedy_label

    def test_tags_default_empty(self):
        artifact = make_artifact()
        assert artifact.tags == []

    def test_tags_custom(self):
        artifact = make_artifact(tags=["deleted", "python"])
        assert "deleted" in artifact.tags
        assert "python" in artifact.tags


class TestExcavationReport:
    def _make_report(self, artifacts=None):
        return ExcavationReport(
            repo_path="/tmp/myrepo",
            repo_name="myrepo",
            total_commits_scanned=100,
            total_files_analyzed=25,
            artifacts=artifacts or [],
        )

    def test_total_artifacts(self):
        artifacts = [make_artifact(tragedy_score=i * 10) for i in range(5)]
        report = self._make_report(artifacts)
        assert report.total_artifacts == 5

    def test_average_tragedy_score(self):
        artifacts = [
            make_artifact(tragedy_score=20),
            make_artifact(tragedy_score=40),
            make_artifact(tragedy_score=60),
        ]
        report = self._make_report(artifacts)
        assert report.average_tragedy_score == pytest.approx(40.0)

    def test_average_tragedy_score_empty(self):
        report = self._make_report([])
        assert report.average_tragedy_score == 0.0

    def test_most_tragic(self):
        artifacts = [
            make_artifact(title="low", tragedy_score=10),
            make_artifact(title="high", tragedy_score=90),
            make_artifact(title="mid", tragedy_score=50),
        ]
        report = self._make_report(artifacts)
        assert report.most_tragic.title == "high"

    def test_most_tragic_empty(self):
        report = self._make_report([])
        assert report.most_tragic is None

    def test_artifacts_by_type(self):
        artifacts = [
            make_artifact(type=ArtifactType.DEAD_FUNCTION),
            make_artifact(type=ArtifactType.DEAD_FUNCTION),
            make_artifact(type=ArtifactType.GHOST_IMPORT),
        ]
        report = self._make_report(artifacts)
        by_type = report.artifacts_by_type
        assert len(by_type[ArtifactType.DEAD_FUNCTION]) == 2
        assert len(by_type[ArtifactType.GHOST_IMPORT]) == 1

    def test_sorted_by_tragedy(self):
        artifacts = [
            make_artifact(tragedy_score=10),
            make_artifact(tragedy_score=90),
            make_artifact(tragedy_score=50),
        ]
        report = self._make_report(artifacts)
        sorted_artifacts = report.sorted_by_tragedy()
        assert sorted_artifacts[0].tragedy_score == 90
        assert sorted_artifacts[-1].tragedy_score == 10
