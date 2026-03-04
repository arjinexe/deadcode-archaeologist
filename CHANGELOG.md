# Changelog

All notable changes to DeadCode Archaeologist are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.1] — 2026-03-05

### Fixed

- **`NameError: ARTIFACT_EMOJI` crash** — `ARTIFACT_EMOJI` was defined in
  `models.py` but never imported in `reporter.py`, causing a hard crash at the
  end of every run when the summary table was rendered.
- **Massive false-positive flood in ancient TODO detection** — the regex
  `(TODO|FIXME|HACK|XXX|BUG|OPTIMIZE)` matched keywords embedded inside
  identifiers (e.g. `app.debug`, `use_debugger`, `@pytest.mark.parametrize`)
  because no word-boundary or comment-marker guard was applied. Three
  progressive fixes were applied:
  1. Added `\b` word-boundary anchors so `debugger` no longer matches `BUG`.
  2. Required a leading comment marker (`#`, `//`, `/*`, `--`, `*`) so only
     actual comments are matched — not string literals or code expressions.
  3. Anchored the pattern to `^` (start of line) so `http://xxx.example.com`
     can no longer trigger an `XXX` match via the `//` in the URL.
  Result: 172 spurious "rotting TODO" findings → 0 on the Flask repository.
- **Duplicate deleted-block artifacts** — the same code block deleted in
  closely-related commits (e.g. a commit and its squash) was reported multiple
  times. A `seen_blocks` deduplication set keyed on
  `(file_path, line_number, snippet[:80])` was added to
  `excavate_deleted_blocks()`. Result: 105 deleted blocks → 64 on the Flask
  repository.


## [1.0.0] — 2024-01-01

### Added
- `excavate` command: full scan combining git history + static analysis
- `analyze` command: static analysis only, no git repository required; accepts a single file or directory
- `history` command: git history scan only (deleted blocks, ancient TODOs, reverted commits)
- Seven artifact types: `deleted_block`, `dead_function`, `orphaned_comment`, `ancient_todo`, `ghost_import`, `reverted_dream`, `lone_variable`
- Tragedy scoring (0–100) with labels: Bittersweet → Melancholic → Tragic → Very Tragic → Devastating
- Rich terminal output with syntax-highlighted code snippets and summary tables
- JSON output mode (`--format json`) — pipe-friendly, suppresses all terminal noise
- `--output FILE` flag: saves plain-text or JSON report to disk
- `--no-git` flag: allows `excavate` on non-git directories (static analysis only)
- `--no-static` flag: skip static analysis, git history only
- `--min-score`, `--top`, `--max-commits`, `--no-color` tuning flags
- Thread-safe parallel commit processing (each worker opens its own `git.Repo`)
- Python AST analysis: dead functions, ghost imports, lone variables, orphaned comments
- JavaScript / TypeScript regex analysis: dead functions, ghost imports
- False-positive guards: `getattr()` strings, `__all__`, decorators, star-imports, framework hooks
- `git blame`-based ancient TODO detection (markers older than 180 days)
- Reverted-dream detection (commits whose message starts with "Revert")
- Graceful fallback reporter when `rich` is not installed

### Fixed
- `--no-git` validation deferred to command body so argument order on the CLI is irrelevant
- `FallbackReporter.progress_context` missing `@contextmanager` decorator (crash in no-rich environments)
- `commits_count` always initialised before `ExcavationReport` construction
- `repo_path.is_dir()` guarded when a single file path is passed to `analyze`
- `_py_files` cache split into two independent lists (`skip_tests=True/False`) to prevent cross-contamination
- Thread-safety: `_process_commit` replaced by `_process_commit_safe` (per-thread `Repo` instance)
- Progress bar totals corrected: deleted-blocks bar tracks `work_items` (diffs), TODO bar tracks code files
- `--format json --output FILE` now suppresses terminal output (silent reporter selected)
- `ExcavationReport.scanned_at` is timezone-aware (`datetime.now(timezone.utc)`)
- `_emit_json` / `_emit_text` raise `ClickException` with a human-readable message on write failure
- `_SilentReporter.print_error` uses top-level `import sys` instead of `__import__("sys")`
- Build backend changed from `setuptools.backends.legacy:build` (requires ≥ 69) to `setuptools.build_meta` (available since setuptools 40)
