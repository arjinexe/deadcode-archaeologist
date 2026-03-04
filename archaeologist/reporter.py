"""
Reporter — renders archaeological findings with Rich.
Includes live progress bars for long-running scans.
"""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Generator
from contextlib import contextmanager

from .models import ARTIFACT_EMOJI, Artifact, ArtifactType, ExcavationReport

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.rule import Rule
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


HEADER_ART = r"""
    ____             __   ______          __
   / __ \___  ____ _/ /  / ____/___  ____/ /__
  / / / / _ \/ __ `/ /  / /   / __ \/ __  / _ \
 / /_/ /  __/ /_/ / /  / /___/ /_/ / /_/ /  __/
/_____/\___/\__,_/_/   \____/\____/\__,_/\___/
     /   |____________/ /_  ____ ___  ____  / /___ _____ (_)____/ /_
    / /| |/ ___/ ___/ __ \/ _ \/ __ \/ __ \/ / __ \/ __ `/ / ___/ __/
   / ___ / /  / /__/ / / /  __/ /_/ / /_/ / / /_/ / /_/ / (__  ) /_
  /_/  |_\___/\___/_/ /_/\___/\____/\____/_/\____/\__, /_/____/\__/
                                                   /____/
"""
SUBTITLE = "Unearthing the code that time forgot."


def _type_color(t: ArtifactType) -> str:
    return {
        ArtifactType.DELETED_BLOCK: "red",
        ArtifactType.DEAD_FUNCTION: "magenta",
        ArtifactType.ORPHANED_COMMENT: "yellow",
        ArtifactType.ANCIENT_TODO: "orange3",
        ArtifactType.GHOST_IMPORT: "blue",
        ArtifactType.REVERTED_DREAM: "bright_red",
        ArtifactType.LONE_VARIABLE: "cyan",
    }.get(t, "white")


def _lang(file_path: str) -> str:
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "jsx": "jsx",
        "tsx": "tsx",
        "java": "java",
        "c": "c",
        "cpp": "cpp",
        "cs": "csharp",
        "go": "go",
        "rb": "ruby",
        "php": "php",
        "swift": "swift",
        "kt": "kotlin",
        "rs": "rust",
        "sh": "bash",
        "bash": "bash",
    }.get(ext, "text")


# ─────────────────────────────────────────────────────────────────────────────
# Progress handles
# ─────────────────────────────────────────────────────────────────────────────


class _ProgressHandle:
    """Wraps a Rich Progress task so callers can advance it."""

    def __init__(self, progress: Progress, task_id: int) -> None:
        self._progress = progress
        self._task_id = task_id

    def advance(self, n: int = 1) -> None:
        self._progress.advance(self._task_id, n)

    def update(self, description: str | None = None, total: int | None = None) -> None:
        kw: dict = {}
        if description is not None:
            kw["description"] = description
        if total is not None:
            kw["total"] = total
        if kw:
            self._progress.update(self._task_id, **kw)


class _NoopProgressHandle:
    """Drop-in replacement when Rich is not available."""

    def advance(self, n: int = 1) -> None:
        pass

    def update(self, **_: object) -> None:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Rich reporter
# ─────────────────────────────────────────────────────────────────────────────


class Reporter:
    def __init__(self, console: Console | None = None, no_color: bool = False) -> None:
        if not RICH_AVAILABLE:
            raise ImportError("Rich is required. pip install rich")
        self.console = console or Console(highlight=False, no_color=no_color)

    def print_header(self) -> None:
        self.console.print()
        self.console.print(Align.center(Text(HEADER_ART, style="bold dark_orange")))
        self.console.print(Align.center(Text(SUBTITLE, style="italic dim")))
        self.console.print()

    def print_scan_start(self, repo_name: str, commits: int, files: int) -> None:
        lines = [f"[bold]Repository:[/bold] {repo_name}"]
        if commits:
            lines.append(f"[bold]Commits to scan:[/bold] {commits}")
        if files:
            lines.append(f"[bold]Files to analyze:[/bold] {files}")
        lines.append("\n[dim italic]Brushing away the dust of forgotten code...[/dim italic]")
        self.console.print(
            Panel(
                "\n".join(lines),
                title="[bold dark_orange]🏺 Excavation Started[/bold dark_orange]",
                border_style="dark_orange",
                padding=(1, 2),
            )
        )
        self.console.print()

    @contextmanager
    def progress_context(
        self,
        description: str,
        total: int | None = None,
    ) -> Generator[_ProgressHandle, None, None]:
        """Context manager that shows a Rich progress bar while work is in flight."""
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        )
        task_id = progress.add_task(description, total=total or 100)
        handle = _ProgressHandle(progress, task_id)
        with progress:
            yield handle

    def print_artifact(self, artifact: Artifact, index: int) -> None:
        color = _type_color(artifact.type)
        lang = _lang(artifact.file_path)

        meta = [
            f"[bold]Type:[/bold] {artifact.type.value.replace('_', ' ').title()}",
            f"[bold]Tragedy:[/bold] {artifact.tragedy_label} ({artifact.tragedy_score}/100)",
            f"[bold]File:[/bold] {artifact.file_path}"
            + (f":{artifact.line_number}" if artifact.line_number else ""),
        ]
        if artifact.author:
            meta.append(f"[bold]Author:[/bold] {artifact.author}")
        if artifact.date:
            meta.append(f"[bold]Date:[/bold] {artifact.date.strftime('%Y-%m-%d')}")
        if artifact.age_days:
            y, m = artifact.age_days // 365, (artifact.age_days % 365) // 30
            age = f"{y}y {m}m ago" if y else f"{m} month{'s' if m != 1 else ''} ago"
            meta.append(f"[bold]Age:[/bold] {age}")
        if artifact.commit_hash:
            meta.append(f"[bold]Commit:[/bold] {artifact.short_hash}")

        desc = textwrap.fill(artifact.description, width=70)
        epitaph = f'[dim italic]"{artifact.epitaph}"[/dim italic]'
        body = "\n".join(meta) + f"\n\n[dim]{desc}[/dim]\n\n{epitaph}"
        if artifact.code_snippet:
            body += "\n\n[bold]Evidence:[/bold]"

        self.console.print(
            Panel(
                body,
                title=f"[dim]#{index}[/dim] {artifact.emoji}  [{color}]{artifact.title}[/{color}]",
                border_style=color,
                padding=(1, 2),
            )
        )

        if artifact.code_snippet:
            snippet = artifact.code_snippet[:600]
            try:
                self.console.print(
                    Syntax(
                        snippet,
                        lang,
                        theme="monokai",
                        line_numbers=bool(artifact.line_number),
                        start_line=artifact.line_number or 1,
                        word_wrap=True,
                    )
                )
            except Exception:
                self.console.print(f"[dim]{snippet}[/dim]")
        self.console.print()

    def print_summary(self, report: ExcavationReport) -> None:
        self.console.print(Rule("[bold dark_orange]📜 Excavation Summary[/bold dark_orange]"))
        self.console.print()

        stats = Table(
            title="Overview",
            box=box.ROUNDED,
            border_style="dark_orange",
            header_style="bold dark_orange",
        )
        stats.add_column("Metric", style="bold", min_width=28)
        stats.add_column("Value", justify="right", min_width=10)
        stats.add_row("Repository", report.repo_name)
        if report.total_commits_scanned:
            stats.add_row("Commits Scanned", str(report.total_commits_scanned))
        if report.total_files_analyzed:
            stats.add_row("Files Analyzed", str(report.total_files_analyzed))
        stats.add_row("Total Artifacts Found", f"[bold red]{report.total_artifacts}[/bold red]")
        stats.add_row(
            "Average Tragedy Score", f"[bold]{report.average_tragedy_score:.1f}/100[/bold]"
        )
        stats.add_row("Scan Duration", f"{report.scan_duration_seconds:.1f}s")
        self.console.print(Align.center(stats))
        self.console.print()

        by_type = report.artifacts_by_type
        if by_type:
            tbl = Table(
                title="Findings by Type",
                box=box.SIMPLE_HEAVY,
                border_style="dim",
                header_style="bold",
            )
            tbl.add_column("Type", min_width=22)
            tbl.add_column("Count", justify="right", min_width=8)
            tbl.add_column("Avg Tragedy", justify="right", min_width=12)
            tbl.add_column("Top Finding", min_width=40)
            for atype, arts in sorted(by_type.items(), key=lambda x: len(x[1]), reverse=True):
                avg = sum(a.tragedy_score for a in arts) / len(arts)
                best = max(arts, key=lambda a: a.tragedy_score)
                col = _type_color(atype)
                tbl.add_row(
                    f"[{col}]{ARTIFACT_EMOJI.get(atype, '🔍')} {atype.value.replace('_', ' ').title()}[/{col}]",
                    str(len(arts)),
                    f"{avg:.0f}/100",
                    textwrap.shorten(best.title, width=38, placeholder="..."),
                )
            self.console.print(Align.center(tbl))
            self.console.print()

        worst = report.most_tragic
        if worst:
            self.console.print(
                Panel(
                    f"[bold]{worst.emoji}  {worst.title}[/bold]\n\n"
                    f"[dim]{worst.description}[/dim]\n\n"
                    f'[italic]"{worst.epitaph}"[/italic]',
                    title="[bold red]💀 Most Tragic Finding[/bold red]",
                    border_style="red",
                    padding=(1, 2),
                )
            )
            self.console.print()

    def print_no_findings(self) -> None:
        self.console.print(
            Panel(
                "[green bold]This codebase is clean.[/green bold]\n\n"
                "[dim]No dead code, no ancient TODOs, no abandoned dreams found.\n"
                "Either the developers were remarkably tidy...\n"
                "or they deleted the evidence before we got here.[/dim]",
                title="[green]✨ Nothing Found[/green]",
                border_style="green",
                padding=(1, 2),
            )
        )

    def print_progress(self, msg: str) -> None:
        self.console.print(f"[dim]  ⛏  {msg}[/dim]")

    def print_error(self, msg: str) -> None:
        self.console.print(f"[bold red]  ✗  {msg}[/bold red]")

    def print_section(self, title: str) -> None:
        self.console.print()
        self.console.print(Rule(f"[bold]{title}[/bold]", style="dim"))
        self.console.print()


# ─────────────────────────────────────────────────────────────────────────────
# Fallback reporter (no Rich installed)
# Fix: @contextmanager was missing — FallbackReporter.progress_context
#      would crash in any non-Rich environment.
# ─────────────────────────────────────────────────────────────────────────────


class FallbackReporter:
    def print_header(self) -> None:
        print("=" * 60)
        print("  DeadCode Archaeologist")
        print("  Unearthing the code that time forgot.")
        print("=" * 60)

    def print_scan_start(self, repo_name: str, commits: int, files: int) -> None:
        print(f"\nScanning: {repo_name}")
        if commits:
            print(f"Commits: {commits}")
        if files:
            print(f"Files:   {files}")

    @contextmanager  # ← was missing in previous version
    def progress_context(
        self,
        description: str,
        total: int | None = None,
    ) -> Generator[_NoopProgressHandle, None, None]:
        print(f"  > {description}...")
        yield _NoopProgressHandle()

    def print_artifact(self, artifact: Artifact, index: int) -> None:
        print(f"\n#{index} {artifact.emoji} {artifact.title}")
        print(f"   Tragedy: {artifact.tragedy_score}/100  |  {artifact.file_path}")
        if artifact.author:
            print(f"   Author:  {artifact.author}")
        print(f"   {artifact.description}")
        if artifact.code_snippet:
            for line in artifact.code_snippet.splitlines()[:5]:
                print(f"   {line}")

    def print_summary(self, report: ExcavationReport) -> None:
        print(f"\n{'=' * 60}")
        print(
            f"  Summary: {report.total_artifacts} artifacts  "
            f"avg tragedy {report.average_tragedy_score:.1f}/100"
        )
        print(f"{'=' * 60}\n")

    def print_no_findings(self) -> None:
        print("\nNo findings. Clean codebase.")

    def print_progress(self, msg: str) -> None:
        print(f"  > {msg}")

    def print_error(self, msg: str) -> None:
        print(f"  ERROR: {msg}")

    def print_section(self, title: str) -> None:
        print(f"\n--- {title} ---")


class _SilentReporter:
    """Fully silent reporter — used when JSON is emitted to stdout."""

    @contextmanager
    def progress_context(
        self, description: str = "", total: int | None = None
    ) -> Generator[_NoopProgressHandle, None, None]:  # type: ignore[override]
        yield _NoopProgressHandle()

    def print_header(self) -> None:
        pass

    def print_scan_start(self, *a: object, **k: object) -> None:
        pass

    def print_artifact(self, *a: object, **k: object) -> None:
        pass

    def print_summary(self, *a: object, **k: object) -> None:
        pass

    def print_no_findings(self) -> None:
        pass

    def print_progress(self, *a: object, **k: object) -> None:
        pass

    def print_error(self, msg: str) -> None:
        print(f"  ERROR: {msg}", file=sys.stderr)

    def print_section(self, *a: object, **k: object) -> None:
        pass


def make_reporter(
    no_color: bool = False, json_mode: bool = False
) -> Reporter | FallbackReporter | _SilentReporter:
    if json_mode:
        return _SilentReporter()
    if RICH_AVAILABLE:
        return Reporter(no_color=no_color)
    return FallbackReporter()
