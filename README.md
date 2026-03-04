# ⛏ DeadCode Archaeologist

> *Unearthing the code that time forgot.*

[![PyPI version](https://img.shields.io/pypi/v/deadcode-archaeologist.svg)](https://pypi.org/project/deadcode-archaeologist/)
[![Python versions](https://img.shields.io/pypi/pyversions/deadcode-archaeologist.svg)](https://pypi.org/project/deadcode-archaeologist/)
[![CI](https://github.com/deadcode-archaeologist/deadcode-archaeologist/actions/workflows/ci.yml/badge.svg)](https://github.com/deadcode-archaeologist/deadcode-archaeologist/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A CLI tool that mines your **git history** and performs **static analysis** to surface dead functions, deleted code blocks, ancient TODOs, ghost imports, and other archaeological artifacts of forgotten code — ranked by how tragic their abandonment was.

---

## Features

| Artifact | Detection Method |
|----------|-----------------|
| 🪦 **Deleted code blocks** | Git history — large hunks removed and never restored |
| 👻 **Dead functions** | AST (Python) + regex (JS/TS) — defined but never called |
| ⏳ **Ancient TODOs** | `git blame` — `TODO`/`FIXME`/`HACK`/`BUG` comments older than 180 days |
| 🌫️ **Ghost imports** | AST / regex — imported but never referenced |
| 💔 **Reverted dreams** | Git history — commits merged then immediately reverted |
| 🗿 **Lone variables** | AST — assigned inside a function but never read |
| 💬 **Orphaned comments** | AST — comments referencing names that no longer exist |

Every artifact receives a **tragedy score** (0–100):

| Score | Label |
|-------|-------|
| 80–100 | 💀 Devastating |
| 60–79  | 😢 Very Tragic |
| 40–59  | 😔 Tragic |
| 20–39  | 😐 Melancholic |
| 0–19   | 🙂 Bittersweet |

---

## Installation

```bash
pip install deadcode-archaeologist
```

Or from source:

```bash
git clone https://github.com/deadcode-archaeologist/deadcode-archaeologist
cd deadcode-archaeologist
pip install -e ".[dev]"
```

**Requirements:** Python 3.9+ · `click` · `gitpython` · `rich`

---

## Quick Start

```bash
# Full scan — git history + static analysis
archaeologist excavate .

# Static analysis only (no git required)
archaeologist analyze .

# Analyze a single file
archaeologist analyze src/models.py

# Git history only
archaeologist history .
```

---

## Commands

### `excavate` — full scan

```
archaeologist excavate [OPTIONS] [REPO_PATH]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--max-commits N` | 300 | Commits to scan in git history |
| `--top N` | 20 | Show top N findings |
| `--min-score N` | 15 | Minimum tragedy score (0–100) |
| `--no-git` | off | Skip git history; works on non-git directories |
| `--no-static` | off | Skip static analysis |
| `--format terminal\|json` | terminal | Output format |
| `--output FILE` | — | Save report to file |
| `--no-color` | off | Disable Rich colours |

```bash
# Deeper scan with stricter filter
archaeologist excavate . --max-commits 1000 --min-score 40

# Static analysis only on a non-git directory
archaeologist excavate /path/to/code --no-git

# Save a JSON report
archaeologist excavate . --format json --output report.json

# Pipe JSON to jq
archaeologist excavate . --format json | jq '.artifacts[] | select(.tragedy_score > 60)'
```

### `analyze` — static analysis only

No git repository required. Accepts a directory **or a single file**.

```
archaeologist analyze [OPTIONS] [REPO_PATH]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--top N` | 30 | Show top N findings |
| `--min-score N` | 10 | Minimum tragedy score |
| `--format terminal\|json` | terminal | Output format |
| `--output FILE` | — | Save report to file |
| `--no-color` | off | Disable Rich colours |

```bash
archaeologist analyze .
archaeologist analyze src/utils.py
archaeologist analyze . --format json --output analysis.json
```

### `history` — git history only

```
archaeologist history [OPTIONS] [REPO_PATH]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--max-commits N` | 500 | Commits to scan |
| `--top N` | 20 | Show top N findings |
| `--format terminal\|json` | terminal | Output format |
| `--output FILE` | — | Save report to file |
| `--no-color` | off | Disable Rich colours |

```bash
archaeologist history . --max-commits 2000
archaeologist history . --format json --output history.json
```

---

## JSON Output

> **Note:** `artifacts` contains the top `--top N` findings. `total_artifacts` reflects the full count — use `--top 9999` to retrieve everything.

```jsonc
{
  "repo_name": "myapp",
  "total_artifacts": 47,
  "average_tragedy_score": 47.3,
  "scan_duration_seconds": 3.2,
  "artifacts": [
    {
      "type": "dead_function",
      "title": "def process_legacy_data() — called by no one",
      "file_path": "src/pipeline.py",
      "line_number": 84,
      "tragedy_score": 78,
      "tragedy_label": "Very Tragic",
      "code_snippet": "def process_legacy_data(records):\n    ...",
      "author": "alice",
      "date": "2021-03-14",
      "tags": ["dead-function", "python"]
    }
  ]
}
```

---

## Language Support

| Language | Dead functions | Ghost imports | Lone variables | Orphaned comments |
|----------|---------------|---------------|----------------|-------------------|
| Python | ✅ AST | ✅ AST | ✅ AST | ✅ AST |
| JS / TS / JSX / TSX | ✅ regex | ✅ regex | — | — |
| Go, Rust, Java, … | via git history only | — | — | — |

---

## False-Positive Guards

**Dead function detection** skips functions that are:
- Referenced via `getattr(obj, "func_name")` (dynamic dispatch)
- Listed in `__all__` (public API)
- Used as a decorator (`@my_decorator`)
- Inside a file that uses `from module import *`
- Framework hooks (`setUp`, `save`, `get`, `post`, `dispatch`, …)
- Dunder methods (`__init__`, `__str__`, …)

**Ancient TODO detection** only matches keywords (`TODO`, `FIXME`, `BUG`, etc.) that:
- Appear as **whole words** (not inside identifiers like `debugger` or `use_debug`)
- Are on an actual **comment line** starting with `#`, `//`, `/*`, `--`, or `*`
- Are not inside string literals, variable names, or URLs like `http://xxx.example.com`

---

## Programmatic Use

```python
from archaeologist.analyzer import Analyzer
from archaeologist.excavator import Excavator

# Static analysis
an = Analyzer("/path/to/project")
for artifact in an.find_dead_functions():
    print(f"{artifact.tragedy_score:3d}  {artifact.title}")

# Git history with progress callback
ex = Excavator("/path/to/project", max_commits=500)
for artifact in ex.excavate_deleted_blocks(
    progress=lambda done, total: print(f"{done}/{total}", end="\r")
):
    print(artifact.title)
```

---

## Development

```bash
git clone https://github.com/deadcode-archaeologist/deadcode-archaeologist
cd deadcode-archaeologist
pip install -e ".[dev]"
pytest
```

Linting and type checking:

```bash
ruff check archaeologist/ tests/
mypy archaeologist/ --ignore-missing-imports
```

---

## Publishing a New Release

```bash
# 1. Bump version in pyproject.toml and archaeologist/__init__.py
# 2. Add entry to CHANGELOG.md
# 3. Commit, tag, push — the publish workflow fires automatically
git tag v1.0.1
git push origin v1.0.1
```

First-time setup: configure a [PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/) for the `publish.yml` workflow, or add a `PYPI_API_TOKEN` secret to the repository.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full history.

---

## License

[MIT](LICENSE)
