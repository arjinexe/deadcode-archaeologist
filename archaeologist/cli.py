"""
CLI entry point for DeadCode Archaeologist.

Fixes in this version:
  1. --no-git validation: argument callback deferred with is_eager=False so
     --no-git is already parsed before the path is validated.
  5. commits_count always defined before ExcavationReport (was NameError on
     count_commits() failure inside history command).
  6. repo_path.is_dir() guarded — safe when path is a single file.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click

from .models import Artifact, ArtifactType, ExcavationReport
from .reporter import make_reporter

# ─────────────────────────────────────────────────────────────────────────────
# Serialisation helpers
# ─────────────────────────────────────────────────────────────────────────────


def _artifact_to_dict(a: Artifact) -> dict:
    return {
        "type": a.type.value,
        "title": a.title,
        "description": a.description,
        "file_path": a.file_path,
        "line_number": a.line_number,
        "author": a.author,
        "date": a.date.isoformat() if a.date else None,
        "commit_hash": a.commit_hash,
        "tragedy_score": a.tragedy_score,
        "tragedy_label": a.tragedy_label,
        "age_days": a.age_days,
        "tags": a.tags,
        "code_snippet": a.code_snippet,
        "epitaph": a.epitaph,
    }


def _report_to_dict(report: ExcavationReport, top: list[Artifact]) -> dict:
    return {
        "repo_name": report.repo_name,
        "repo_path": report.repo_path,
        "scanned_at": report.scanned_at.isoformat(),
        "total_commits_scanned": report.total_commits_scanned,
        "total_files_analyzed": report.total_files_analyzed,
        "total_artifacts": report.total_artifacts,
        "average_tragedy_score": round(report.average_tragedy_score, 2),
        "scan_duration_seconds": round(report.scan_duration_seconds, 2),
        "artifacts": [_artifact_to_dict(a) for a in top],
    }


def _emit_json(report: ExcavationReport, top: list[Artifact], output: str | None) -> None:
    serialized = json.dumps(_report_to_dict(report, top), indent=2, ensure_ascii=False)
    if output:
        try:
            Path(output).write_text(serialized, encoding="utf-8")
            click.echo(f"\n📄 JSON report saved to {output}")
        except OSError as exc:
            raise click.ClickException(f"Cannot write JSON report to {output!r}: {exc}") from exc
    else:
        click.echo(serialized)


def _emit_text(report: ExcavationReport, top: list[Artifact], output: str | None) -> None:
    if output is None:
        return
    lines = [
        "DeadCode Archaeologist — Excavation Report",
        "=" * 60,
        f"Repository:   {report.repo_name}",
        f"Scanned at:   {report.scanned_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Commits:      {report.total_commits_scanned}",
        f"Files:        {report.total_files_analyzed}",
        f"Artifacts:    {report.total_artifacts}",
        f"Avg tragedy:  {report.average_tragedy_score:.1f}/100",
        "",
    ]
    for i, a in enumerate(top, 1):
        lines.append(f"#{i} {a.emoji} {a.title}")
        lines.append(f"   Type:    {a.type.value}")
        lines.append(f"   Tragedy: {a.tragedy_score}/100")
        lines.append(f"   File:    {a.file_path}")
        if a.author:
            lines.append(f"   Author:  {a.author}")
        if a.date:
            lines.append(f"   Date:    {a.date.strftime('%Y-%m-%d')}")
        lines.append(f"   {a.description}")
        if a.code_snippet:
            for ln in a.code_snippet.splitlines()[:8]:
                lines.append(f"      {ln}")
        lines.append("")
    try:
        Path(output).write_text("\n".join(lines), encoding="utf-8")
        click.echo(f"\n📄 Report saved to {output}")
    except OSError as exc:
        raise click.ClickException(f"Cannot write report to {output!r}: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# Path validators
#
# Fix 1: The old approach used sys.argv to peek at --no-git before Click had
# finished parsing, which broke in any non-argv invocation.
#
# New approach: the ARGUMENT callback is set is_eager=False (default) so all
# OPTIONS are parsed first. By the time the argument callback fires,
# ctx.params already contains no_git=True/False.
# ─────────────────────────────────────────────────────────────────────────────


def _require_path_exists(ctx: click.Context, param: click.Parameter, value: str) -> Path:
    """Accept any existing path (file or directory). Used by `analyze`."""
    p = Path(value).resolve()
    if not p.exists():
        raise click.BadParameter(f"Path does not exist: {p}")
    return p


def _require_dir(ctx: click.Context, param: click.Parameter, value: str) -> Path:
    """
    Require an existing directory.

    The .git check is intentionally NOT performed here — argument callbacks
    fire as the token is encountered on the command line, which may be before
    Click has parsed options that appear *after* the argument
    (e.g. ``excavate /some/path --no-git``).  Performing the git check inside
    the callback via ``ctx.params.get("no_git")`` therefore gives a false
    negative whenever the argument precedes the flag.

    The .git check is deferred to the command body via ``_check_git_dir()``,
    where every parameter is fully resolved.
    """
    p = Path(value).resolve()
    if not p.exists():
        raise click.BadParameter(f"Path does not exist: {p}")
    if not p.is_dir():
        raise click.BadParameter(f"Not a directory: {p}")
    return p


def _check_git_dir(repo_path: Path, no_git: bool) -> None:
    """Raise UsageError if a .git dir is required but absent.

    Called from the command body after Click has fully resolved all options.
    """
    if not no_git and not (repo_path / ".git").exists():
        raise click.UsageError(
            f"No .git directory found in {repo_path}\n"
            "Tip: use --no-git to run static analysis without git."
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI group
# ─────────────────────────────────────────────────────────────────────────────


@click.group()
@click.version_option(version="1.0.0", prog_name="archaeologist")
def cli() -> None:
    """
    \b
    ⛏  DeadCode Archaeologist
    Unearthing the code that time forgot.

    Analyze a git repository for dead functions, deleted code blocks,
    ancient TODOs, ghost imports, and other archaeological curiosities.
    """


# ── excavate ──────────────────────────────────────────────────────────────────
# Options MUST be declared before the argument so they appear first in the
# help output. The git check itself is inside the command body.


@cli.command("excavate")
@click.option(
    "--max-commits", default=300, show_default=True, help="Maximum number of commits to scan."
)
@click.option("--top", default=20, show_default=True, help="Show top N most tragic findings.")
@click.option(
    "--min-score", default=15, show_default=True, help="Minimum tragedy score to report (0-100)."
)
@click.option("--no-color", is_flag=True, default=False, help="Disable coloured output.")
@click.option(
    "--no-git",
    is_flag=True,
    default=False,
    help="Skip git history scanning (static analysis only). Also allows non-git directories.",
)
@click.option(
    "--no-static", is_flag=True, default=False, help="Skip static analysis (git history only)."
)
@click.option(
    "--format",
    "-f",
    "output_format",
    default="terminal",
    show_default=True,
    type=click.Choice(["terminal", "json"]),
    help="Output format.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(),
    help="Save report to file. Plain-text with --format terminal; "
    "JSON (no terminal output) with --format json.",
)
@click.argument("repo_path", default=".", callback=_require_dir, expose_value=True)
def excavate(
    max_commits: int,
    top: int,
    min_score: int,
    no_color: bool,
    no_git: bool,
    no_static: bool,
    output_format: str,
    output: str | None,
    repo_path: Path,
) -> None:
    """
    Run a full excavation of a git repository.

    REPO_PATH is the root of the git repository (default: current directory).

    \b
    Examples:
      archaeologist excavate .
      archaeologist excavate ~/projects/myapp --top 10
      archaeologist excavate . --no-git
      archaeologist excavate . --no-git /path/without/git
      archaeologist excavate . --format json | jq '.artifacts[0]'
      archaeologist excavate . --output report.txt
    """
    # Bug #1 fix: validate .git here, after Click has resolved every option.
    # Doing this in the argument callback was unreliable because callbacks fire
    # as tokens are consumed — options that appear *after* the argument on the
    # command line (e.g. ``excavate /path --no-git``) are not yet in ctx.params
    # when the argument callback runs.
    _check_git_dir(repo_path, no_git)

    from .analyzer import Analyzer
    from .excavator import Excavator

    json_mode = output_format == "json"
    reporter = make_reporter(no_color=no_color, json_mode=json_mode)
    if not json_mode:
        reporter.print_header()

    start = time.time()
    all_artifacts: list[Artifact] = []
    repo_name = repo_path.name
    commits_count = 0  # always defined — Fix 5 applied here too
    files_count = 0

    # ── Git history ──────────────────────────────────────────────────────
    if not no_git:
        try:
            ex = Excavator(str(repo_path), max_commits=max_commits)
            commits_count = ex.count_commits()
            if not json_mode:
                reporter.print_scan_start(repo_name, commits_count, 0)
                reporter.print_section("🪦  Git History — The Mass Graves")

            with reporter.progress_context(
                "Scanning deleted code blocks", total=commits_count
            ) as pb:

                def _blk_prog(done: int, total: int) -> None:
                    pb.advance(1)

                for a in ex.excavate_deleted_blocks(progress=_blk_prog):
                    if a.tragedy_score >= min_score:
                        all_artifacts.append(a)
            if not json_mode:
                deleted_n = sum(1 for a in all_artifacts if a.type == ArtifactType.DELETED_BLOCK)
                reporter.print_progress(f"Found {deleted_n} deleted code artifacts.")

            todo_before = len(all_artifacts)
            # Use the progress callback's `total` arg from the excavator itself,
            # which counts only code files — not all repo files.
            with reporter.progress_context("Hunting ancient TODOs", total=None) as pb:

                def _todo_prog(done: int, total: int) -> None:
                    pb.update(total=total)
                    pb.advance(1)

                for a in ex.find_ancient_todos(progress=_todo_prog):
                    if a.tragedy_score >= min_score:
                        all_artifacts.append(a)
            if not json_mode:
                reporter.print_progress(f"Found {len(all_artifacts) - todo_before} rotting TODOs.")

            rev_before = len(all_artifacts)
            for a in ex.find_reverted_dreams():
                if a.tragedy_score >= min_score:
                    all_artifacts.append(a)
            if not json_mode:
                reporter.print_progress(
                    f"Found {len(all_artifacts) - rev_before} reverted commits."
                )

        except ImportError as e:
            reporter.print_error(str(e))
            reporter.print_error("Skipping git history analysis.")
        except Exception as e:
            reporter.print_error(f"Git history scan failed: {e}")

    # ── Static analysis ──────────────────────────────────────────────────
    if not no_static:
        try:
            an = Analyzer(str(repo_path))
            files_count = an.count_analyzable_files()
            if not json_mode:
                if no_git:
                    reporter.print_scan_start(repo_name, 0, files_count)
                reporter.print_section("👻  Static Analysis — The Walking Dead")

            for label, method in [
                ("Finding dead functions", an.find_dead_functions),
                ("Tracking ghost imports", an.find_ghost_imports),
                ("Searching lone variables", an.find_lone_variables),
                ("Looking for orphaned comments", an.find_orphaned_comments),
            ]:
                before = len(all_artifacts)
                with reporter.progress_context(label, total=files_count) as pb:
                    for a in method():
                        if a.tragedy_score >= min_score:
                            all_artifacts.append(a)
                if not json_mode:
                    reporter.print_progress(f"{label}: {len(all_artifacts) - before} found.")

        except Exception as e:
            reporter.print_error(f"Static analysis failed: {e}")

    # ── Output ───────────────────────────────────────────────────────────
    duration = time.time() - start
    top_artifacts = sorted(all_artifacts, key=lambda a: a.tragedy_score, reverse=True)[:top]

    report = ExcavationReport(
        repo_path=str(repo_path),
        repo_name=repo_name,
        total_commits_scanned=commits_count,
        total_files_analyzed=files_count,
        artifacts=all_artifacts,
        scan_duration_seconds=duration,
    )

    if output_format == "json":
        _emit_json(report, top_artifacts, output)
        return

    if not all_artifacts:
        reporter.print_no_findings()
    else:
        reporter.print_section(f"🏺  Top {len(top_artifacts)} Most Tragic Findings")
        for i, a in enumerate(top_artifacts, 1):
            reporter.print_artifact(a, i)
        reporter.print_summary(report)

    if output:
        _emit_text(report, top_artifacts, output)


# ── analyze ───────────────────────────────────────────────────────────────────


@cli.command("analyze")
@click.option("--top", default=30, show_default=True, help="Show top N findings.")
@click.option("--min-score", default=10, show_default=True, help="Minimum tragedy score.")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="terminal",
    show_default=True,
    type=click.Choice(["terminal", "json"]),
    help="Output format.",
)
@click.option("--output", "-o", default=None, type=click.Path(), help="Save report to file.")
@click.option("--no-color", is_flag=True, default=False, help="Disable coloured output.")
@click.argument("repo_path", default=".", callback=_require_path_exists, expose_value=True)
def analyze(
    top: int,
    min_score: int,
    output_format: str,
    output: str | None,
    no_color: bool,
    repo_path: Path,
) -> None:
    """
    Static analysis only — no git history required.

    REPO_PATH can be a directory OR a single source file.

    \b
    Examples:
      archaeologist analyze .
      archaeologist analyze src/models.py
      archaeologist analyze ~/projects/myapp --top 15
      archaeologist analyze . --format json --output report.json
    """
    from .analyzer import Analyzer

    json_mode = output_format == "json"
    reporter = make_reporter(no_color=no_color, json_mode=json_mode)
    if not json_mode:
        reporter.print_header()

    an = Analyzer(str(repo_path))
    files_count = an.count_analyzable_files()
    # Fix 6: repo_path may be a file — guard is_dir() call
    repo_name = repo_path.stem if repo_path.is_file() else repo_path.name

    if not json_mode:
        reporter.print_scan_start(repo_name, 0, files_count)
        reporter.print_section("👻  Static Analysis Only")

    all_artifacts: list[Artifact] = []
    try:
        for label, method in [
            ("Finding dead functions", an.find_dead_functions),
            ("Tracking ghost imports", an.find_ghost_imports),
            ("Searching lone variables", an.find_lone_variables),
            ("Looking for orphaned comments", an.find_orphaned_comments),
        ]:
            before = len(all_artifacts)
            with reporter.progress_context(label, total=files_count):
                for a in method():
                    if a.tragedy_score >= min_score:
                        all_artifacts.append(a)
            if not json_mode:
                reporter.print_progress(f"{label}: {len(all_artifacts) - before} found.")
    except Exception as e:
        reporter.print_error(f"Analysis failed: {e}")
        sys.exit(1)

    top_artifacts = sorted(all_artifacts, key=lambda a: a.tragedy_score, reverse=True)[:top]
    report = ExcavationReport(
        repo_path=str(repo_path),
        repo_name=repo_name,
        total_commits_scanned=0,
        total_files_analyzed=files_count,
        artifacts=all_artifacts,
    )

    if output_format == "json":
        _emit_json(report, top_artifacts, output)
        return

    if not all_artifacts:
        reporter.print_no_findings()
    else:
        reporter.print_section(f"🏺  Top {len(top_artifacts)} Findings")
        for i, a in enumerate(top_artifacts, 1):
            reporter.print_artifact(a, i)
        reporter.print_summary(report)

    if output:
        _emit_text(report, top_artifacts, output)


# ── history ───────────────────────────────────────────────────────────────────


@cli.command("history")
@click.option("--max-commits", default=500, show_default=True, help="Maximum commits to scan.")
@click.option("--top", default=20, show_default=True, help="Show top N findings.")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="terminal",
    show_default=True,
    type=click.Choice(["terminal", "json"]),
    help="Output format.",
)
@click.option("--output", "-o", default=None, type=click.Path(), help="Save report to file.")
@click.option("--no-color", is_flag=True, default=False, help="Disable coloured output.")
@click.argument("repo_path", default=".", callback=_require_dir, expose_value=True)
def history(
    max_commits: int,
    top: int,
    output_format: str,
    output: str | None,
    no_color: bool,
    repo_path: Path,
) -> None:
    """
    Scan git history only (no static analysis).

    \b
    Examples:
      archaeologist history .
      archaeologist history ~/projects/myapp --max-commits 1000
      archaeologist history . --format json --output history.json
    """
    # Bug #1 fix: validate .git here, after Click has fully resolved all options.
    _check_git_dir(repo_path, no_git=False)

    from .excavator import Excavator

    json_mode = output_format == "json"
    reporter = make_reporter(no_color=no_color, json_mode=json_mode)
    commits_count = 0  # Fix 5: always defined before ExcavationReport
    all_artifacts: list[Artifact] = []

    if not json_mode:
        reporter.print_header()

    try:
        ex = Excavator(str(repo_path), max_commits=max_commits)
        commits_count = ex.count_commits()  # may still be 0 on error, that's fine

        if not json_mode:
            reporter.print_scan_start(repo_path.name, commits_count, 0)
            reporter.print_section("⛏  Mining Git History")

        with reporter.progress_context("Scanning deleted code blocks", total=commits_count) as pb:

            def _blk(done: int, total: int) -> None:
                pb.advance(1)

            for a in ex.excavate_deleted_blocks(progress=_blk):
                all_artifacts.append(a)

        with reporter.progress_context("Hunting ancient TODOs", total=None) as pb:

            def _todo(done: int, total: int) -> None:
                pb.update(total=total)
                pb.advance(1)

            for a in ex.find_ancient_todos(progress=_todo):
                all_artifacts.append(a)

        for a in ex.find_reverted_dreams():
            all_artifacts.append(a)

    except ImportError as e:
        reporter.print_error(str(e))
        sys.exit(1)
    except Exception as e:
        reporter.print_error(f"History scan failed: {e}")
        sys.exit(1)

    top_artifacts = sorted(all_artifacts, key=lambda a: a.tragedy_score, reverse=True)[:top]
    report = ExcavationReport(
        repo_path=str(repo_path),
        repo_name=repo_path.name,
        total_commits_scanned=commits_count,
        total_files_analyzed=0,
        artifacts=all_artifacts,
    )

    if output_format == "json":
        _emit_json(report, top_artifacts, output)
        return

    if not all_artifacts:
        reporter.print_no_findings()
    else:
        reporter.print_section(f"🏺  Top {len(top_artifacts)} Findings")
        for i, a in enumerate(top_artifacts, 1):
            reporter.print_artifact(a, i)
        reporter.print_summary(report)

    if output:
        _emit_text(report, top_artifacts, output)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
