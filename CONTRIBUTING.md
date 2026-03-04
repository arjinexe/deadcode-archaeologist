# Contributing to DeadCode Archaeologist

First of all: thank you. Most tools celebrate *new* code. We celebrate the forgotten.

## Getting Started

```bash
git clone https://github.com/deadcode-archaeologist/deadcode-archaeologist
cd deadcode-archaeologist
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -v
```

For coverage:

```bash
pytest tests/ --cov=archaeologist --cov-report=html
```

## Project Structure

```
archaeologist/
├── models.py      — Data structures (Artifact, ExcavationReport, etc.)
├── excavator.py   — Git history mining
├── analyzer.py    — Static code analysis
├── reporter.py    — Rich-based terminal output
└── cli.py         — Click CLI commands
```

## Adding New Artifact Types

1. Add a new value to `ArtifactType` enum in `models.py`
2. Add emoji and epitaph to `ARTIFACT_EMOJI` and `ARTIFACT_EPITAPHS`
3. Implement detection logic in `excavator.py` or `analyzer.py`
4. Write tests in `tests/`

## Extending Language Support

Currently, deep static analysis (dead functions, ghost imports, lone variables) only works for **Python**.
We'd love contributions for:

- JavaScript/TypeScript (using a JS AST parser)
- Go
- Ruby
- Rust

## Code Style

We use `ruff` for linting:

```bash
ruff check archaeologist/ tests/
ruff format archaeologist/ tests/
```

## Pull Request Guidelines

- Keep PRs focused on one thing
- Add tests for new detection logic
- Update the README if you add new features
- Run the full test suite before opening a PR

## Ideas for Future Features

- `--format json` output for piping into other tools
- GitHub Action for running in CI
- VS Code extension showing "grave markers" inline
- HTML report with searchable artifact gallery
- "Hall of Fame" — ranking the most haunted repositories
- Multi-language support (JS, Go, Rust)
- Blame-based grief attribution ("who wrote the most dead code?")

## Code of Conduct

Be kind. This project is about dead code, not dead people.
