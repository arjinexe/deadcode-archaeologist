"""Tests for the static code analyzer — all fixes covered."""

import ast
from pathlib import Path

import pytest

from archaeologist.analyzer import (
    Analyzer,
    _decorator_names,
    _dunder_all_names,
    _getattr_strings,
    _has_star_import,
)
from archaeologist.models import ArtifactType

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse(src: str) -> ast.AST:
    return ast.parse(src)


def _func_node(src: str) -> ast.AST:
    tree = _parse(src)
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))


# ─────────────────────────────────────────────────────────────────────────────
# False positive guards — AST helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestGetAttrStrings:
    def test_finds_getattr_second_arg(self):
        tree = _parse("getattr(obj, 'my_func')")
        assert "my_func" in _getattr_strings(tree)

    def test_ignores_non_string(self):
        tree = _parse("getattr(obj, name)")
        assert len(_getattr_strings(tree)) == 0

    def test_multiple(self):
        tree = _parse("getattr(a,'foo'); getattr(b,'bar')")
        names = _getattr_strings(tree)
        assert "foo" in names and "bar" in names


class TestDunderAllNames:
    def test_finds_exported_names(self):
        tree = _parse("__all__ = ['do_thing', 'helper']")
        names = _dunder_all_names(tree)
        assert "do_thing" in names and "helper" in names

    def test_empty_all(self):
        tree = _parse("__all__ = []")
        assert _dunder_all_names(tree) == set()

    def test_no_all(self):
        tree = _parse("x = 1")
        assert _dunder_all_names(tree) == set()


class TestDecoratorNames:
    def test_simple_decorator(self):
        tree = _parse("@my_decorator\ndef f(): pass")
        assert "my_decorator" in _decorator_names(tree)

    def test_call_decorator(self):
        tree = _parse("@app.route('/home')\ndef home(): pass")
        assert "route" in _decorator_names(tree)

    def test_no_decorators(self):
        tree = _parse("def f(): pass")
        assert _decorator_names(tree) == set()


class TestStarImport:
    def test_detects_star(self):
        tree = _parse("from utils import *")
        assert _has_star_import(tree)

    def test_no_star(self):
        tree = _parse("from utils import foo")
        assert not _has_star_import(tree)


# ─────────────────────────────────────────────────────────────────────────────
# Dead Functions — Python
# ─────────────────────────────────────────────────────────────────────────────


class TestDeadFunctionsPython:
    def test_finds_unreferenced(self, tmp_python_files: Path):
        an = Analyzer(str(tmp_python_files))
        names = [a.title for a in an.find_dead_functions()]
        assert any("truly_dead" in n for n in names)

    def test_ignores_called_function(self, tmp_python_files: Path):
        an = Analyzer(str(tmp_python_files))
        names = [a.title for a in an.find_dead_functions()]
        assert not any("dead_function" in n for n in names)

    def test_does_not_flag_getattr_ref(self, tmp_path: Path):
        (tmp_path / "mod.py").write_text(
            "def secret(): return 42\nresult = getattr(obj, 'secret')()\n"
        )
        an = Analyzer(str(tmp_path))
        names = [a.title for a in an.find_dead_functions()]
        assert not any("secret" in n for n in names)

    def test_does_not_flag_exported_name(self, tmp_path: Path):
        (tmp_path / "mod.py").write_text("__all__ = ['public_api']\ndef public_api(): pass\n")
        an = Analyzer(str(tmp_path))
        names = [a.title for a in an.find_dead_functions()]
        assert not any("public_api" in n for n in names)

    def test_does_not_flag_decorator(self, tmp_path: Path):
        (tmp_path / "mod.py").write_text(
            "def my_decorator(f): return f\n@my_decorator\ndef target(): pass\n"
        )
        an = Analyzer(str(tmp_path))
        names = [a.title for a in an.find_dead_functions()]
        assert not any("my_decorator" in n for n in names)

    def test_skips_file_with_star_import(self, tmp_path: Path):
        (tmp_path / "mod.py").write_text("from utils import *\ndef might_be_dead(): pass\n")
        an = Analyzer(str(tmp_path))
        # File with star import should be skipped entirely
        names = [a.title for a in an.find_dead_functions()]
        assert not any("might_be_dead" in n for n in names)

    def test_no_duplicates(self, tmp_python_files: Path):
        an = Analyzer(str(tmp_python_files))
        artifacts = list(an.find_dead_functions())
        keys = [(a.file_path, a.line_number) for a in artifacts]
        assert len(keys) == len(set(keys)), "Duplicate artifacts emitted"

    def test_artifact_type(self, tmp_python_files: Path):
        for a in Analyzer(str(tmp_python_files)).find_dead_functions():
            assert a.type == ArtifactType.DEAD_FUNCTION

    def test_tragedy_score_range(self, tmp_python_files: Path):
        for a in Analyzer(str(tmp_python_files)).find_dead_functions():
            assert 0 <= a.tragedy_score <= 100


# ─────────────────────────────────────────────────────────────────────────────
# Dead Functions — JavaScript / TypeScript
# ─────────────────────────────────────────────────────────────────────────────


class TestDeadFunctionsJS:
    def test_finds_dead_js_function(self, tmp_path: Path):
        (tmp_path / "utils.js").write_text(
            "function used() { return 1; }\nfunction dead() { return 2; }\nused();\n"
        )
        names = [a.title for a in Analyzer(str(tmp_path)).find_dead_functions()]
        assert any("dead" in n for n in names)
        assert not any("used" in n for n in names)

    def test_finds_dead_arrow_function(self, tmp_path: Path):
        (tmp_path / "app.ts").write_text(
            "const helper = () => 42;\nconst live = () => 1;\nlive();\n"
        )
        names = [a.title for a in Analyzer(str(tmp_path)).find_dead_functions()]
        assert any("helper" in n for n in names)

    def test_js_tags(self, tmp_path: Path):
        (tmp_path / "app.js").write_text("function orphan() { return 0; }\n")
        for a in Analyzer(str(tmp_path)).find_dead_functions():
            if "orphan" in a.title:
                assert "javascript" in a.tags

    def test_no_duplicates_js(self, tmp_path: Path):
        (tmp_path / "a.js").write_text("function foo() {}\n")
        artifacts = list(Analyzer(str(tmp_path)).find_dead_functions())
        keys = [(a.file_path, a.line_number) for a in artifacts]
        assert len(keys) == len(set(keys))


# ─────────────────────────────────────────────────────────────────────────────
# Ghost Imports — Python
# ─────────────────────────────────────────────────────────────────────────────


class TestGhostImportsPython:
    def test_finds_unused_import(self, tmp_python_files: Path):
        names = [a.title for a in Analyzer(str(tmp_python_files)).find_ghost_imports()]
        assert any("json" in n for n in names)

    def test_finds_unused_from_import(self, tmp_python_files: Path):
        names = [a.title for a in Analyzer(str(tmp_python_files)).find_ghost_imports()]
        assert any("Path" in n for n in names)

    def test_does_not_flag_used_import(self, tmp_python_files: Path):
        # 'os' is used in the fixture file
        names = [a.title for a in Analyzer(str(tmp_python_files)).find_ghost_imports()]
        assert not any(n.startswith("import os") for n in names)

    def test_no_duplicates(self, tmp_python_files: Path):
        artifacts = list(Analyzer(str(tmp_python_files)).find_ghost_imports())
        keys = [(a.file_path, a.line_number, a.title) for a in artifacts]
        assert len(keys) == len(set(keys))

    def test_artifact_type(self, tmp_python_files: Path):
        for a in Analyzer(str(tmp_python_files)).find_ghost_imports():
            assert a.type == ArtifactType.GHOST_IMPORT


# ─────────────────────────────────────────────────────────────────────────────
# Ghost Imports — JavaScript / TypeScript
# ─────────────────────────────────────────────────────────────────────────────


class TestGhostImportsJS:
    def test_named_import_unused(self, tmp_path: Path):
        (tmp_path / "page.tsx").write_text(
            "import { useState, useEffect } from 'react';\n"
            "function Page() { const [x] = useState(0); return x; }\n"
        )
        names = [a.title for a in Analyzer(str(tmp_path)).find_ghost_imports()]
        assert any("useEffect" in n for n in names)
        assert not any("useState" in n for n in names)

    def test_default_import_unused(self, tmp_path: Path):
        (tmp_path / "comp.js").write_text(
            "import axios from 'axios';\nexport function hello() { return 'world'; }\n"
        )
        names = [a.title for a in Analyzer(str(tmp_path)).find_ghost_imports()]
        assert any("axios" in n for n in names)

    def test_no_duplicates_js(self, tmp_path: Path):
        (tmp_path / "a.ts").write_text("import { foo } from 'lib';\n")
        artifacts = list(Analyzer(str(tmp_path)).find_ghost_imports())
        keys = [(a.file_path, a.line_number, a.title) for a in artifacts]
        assert len(keys) == len(set(keys))


# ─────────────────────────────────────────────────────────────────────────────
# Lone Variables — edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestLoneVariables:
    def test_real_lone_variable(self, tmp_python_files: Path):
        names = [a.title for a in Analyzer(str(tmp_python_files)).find_lone_variables()]
        assert any("result" in n for n in names)

    def test_used_variable_not_flagged(self, tmp_python_files: Path):
        names = [a.title for a in Analyzer(str(tmp_python_files)).find_lone_variables()]
        assert not any('"final"' in n for n in names)

    @pytest.mark.parametrize(
        "src,excluded_var",
        [
            ("def f(items):\n    for item in items:\n        print(item)\n", "item"),
            ("def f(p):\n    with open(p) as fh:\n        return fh.read()\n", "fh"),
            (
                "def f():\n    try:\n        pass\n    except Exception as e:\n        print(e)\n",
                "e",
            ),
            ("def f(xs):\n    return [x*2 for x in xs]\n", "x"),
            (
                "def f(items):\n    total=0\n    for _ in items:\n        total+=1\n    return total\n",
                "total",
            ),
        ],
        ids=["for-loop", "with", "except", "comprehension", "augmented-assign"],
    )
    def test_edge_cases_not_flagged(self, tmp_path: Path, src: str, excluded_var: str):
        (tmp_path / "code.py").write_text(src)
        names = [a.title for a in Analyzer(str(tmp_path)).find_lone_variables()]
        assert not any(f'"{excluded_var}"' in n for n in names), (
            f"'{excluded_var}' wrongly flagged as lone variable"
        )

    def test_no_duplicates(self, tmp_python_files: Path):
        artifacts = list(Analyzer(str(tmp_python_files)).find_lone_variables())
        keys = [(a.file_path, a.line_number) for a in artifacts]
        assert len(keys) == len(set(keys))

    def test_artifact_type(self, tmp_python_files: Path):
        for a in Analyzer(str(tmp_python_files)).find_lone_variables():
            assert a.type == ArtifactType.LONE_VARIABLE


# ─────────────────────────────────────────────────────────────────────────────
# Single-file mode
# ─────────────────────────────────────────────────────────────────────────────


class TestSingleFileMode:
    def test_analyze_single_py_file(self, tmp_path: Path):
        f = tmp_path / "module.py"
        f.write_text("import json\ndef dead(): pass\ndef live(): return 1\nlive()\n")
        an = Analyzer(str(f))
        assert an.count_analyzable_files() == 1
        funcs = list(an.find_dead_functions())
        imports = list(an.find_ghost_imports())
        assert any("dead" in a.title for a in funcs)
        assert any("json" in a.title for a in imports)

    def test_analyze_single_js_file(self, tmp_path: Path):
        f = tmp_path / "app.js"
        f.write_text("import lodash from 'lodash';\nfunction orphan() { return 1; }\n")
        an = Analyzer(str(f))
        assert an.count_analyzable_files() == 1
        imports = list(an.find_ghost_imports())
        assert any("lodash" in a.title for a in imports)


# ─────────────────────────────────────────────────────────────────────────────
# AST cache
# ─────────────────────────────────────────────────────────────────────────────


class TestASTCache:
    def test_each_file_parsed_once(self, tmp_python_files: Path):
        an = Analyzer(str(tmp_python_files))
        _ = list(an.find_dead_functions())
        _ = list(an.find_ghost_imports())
        _ = list(an.find_lone_variables())
        # All files should be in the cache
        for fp in tmp_python_files.rglob("*.py"):
            assert fp in an._cache._src

    def test_consistent_results_on_second_run(self, tmp_python_files: Path):
        an = Analyzer(str(tmp_python_files))
        r1 = list(an.find_dead_functions())
        r2 = list(an.find_dead_functions())
        assert len(r1) == len(r2)


# ─────────────────────────────────────────────────────────────────────────────
# File counting
# ─────────────────────────────────────────────────────────────────────────────


class TestCountFiles:
    def test_counts_py_and_js(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x=1")
        (tmp_path / "b.ts").write_text("const x=1")
        assert Analyzer(str(tmp_path)).count_analyzable_files() >= 2

    def test_skips_node_modules(self, tmp_path: Path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "lib.js").write_text("// vendor")
        (tmp_path / "app.js").write_text("const x=1")
        assert Analyzer(str(tmp_path)).count_analyzable_files() == 1

    def test_skips_pycache(self, tmp_path: Path):
        pc = tmp_path / "__pycache__"
        pc.mkdir()
        (pc / "mod.pyc").write_bytes(b"")
        (tmp_path / "mod.py").write_text("x=1")
        # .pyc not in CODE_EXTENSIONS, but .py is
        assert Analyzer(str(tmp_path)).count_analyzable_files() == 1

    def test_empty_dir(self, tmp_path: Path):
        assert Analyzer(str(tmp_path)).count_analyzable_files() == 0


# ─────────────────────────────────────────────────────────────────────────────
# skip_tests cache regression (Bug #7 fix verification)
# First call with skip_tests=False must not poison the skip_tests=True cache.
# ─────────────────────────────────────────────────────────────────────────────


class TestSkipTestsCache:
    def test_skip_tests_false_then_true_returns_different_lists(self, tmp_path: Path):
        """
        Regression: previously a single _py_files_cache was used for both
        skip_tests=True and skip_tests=False. A skip_tests=False call populated
        the cache; a subsequent skip_tests=True call returned the same full list
        (including test files). Verify the two caches are now independent.
        """
        (tmp_path / "module.py").write_text("def foo(): pass\n")
        (tmp_path / "test_module.py").write_text(
            "from module import foo\ndef test_foo(): assert foo() is None\n"
        )

        an = Analyzer(str(tmp_path))

        all_files = an._py_files(skip_tests=False)
        skip_files = an._py_files(skip_tests=True)

        all_names = {fp.name for fp in all_files}
        skip_names = {fp.name for fp in skip_files}

        assert "module.py" in all_names, "module.py should appear in full list"
        assert "test_module.py" in all_names, "test_module.py should appear in full list"
        assert "module.py" in skip_names, "module.py should appear when skipping tests"
        assert "test_module.py" not in skip_names, (
            "test_module.py must be excluded when skip_tests=True — cache bug regressed!"
        )

    def test_skip_tests_true_first_then_false_independent(self, tmp_path: Path):
        """Calling skip_tests=True first must not affect the skip_tests=False cache."""
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "test_app.py").write_text("x = 2\n")

        an = Analyzer(str(tmp_path))

        # Populate skip_tests=True cache first
        skip_files = an._py_files(skip_tests=True)
        # Now populate skip_tests=False cache
        all_files = an._py_files(skip_tests=False)

        assert "test_app.py" not in {fp.name for fp in skip_files}
        assert "test_app.py" in {fp.name for fp in all_files}


# ─────────────────────────────────────────────────────────────────────────────
# CLI integration — JSON output / file writing behaviour
# These tests use only `analyze` (static analysis, no git required) so they
# run in every environment regardless of whether GitPython is installed.
# ─────────────────────────────────────────────────────────────────────────────


class TestCLIJsonOutput:
    def test_json_to_stdout_is_valid_json(self, tmp_python_files: Path):
        """--format json (no --output) emits valid JSON to stdout, nothing else."""
        import json

        from click.testing import CliRunner

        from archaeologist.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "analyze",
                str(tmp_python_files),
                "--format",
                "json",
                "--no-color",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert "artifacts" in parsed
        assert "repo_name" in parsed
        assert "total_artifacts" in parsed

    def test_json_to_file_suppresses_terminal_output(self, tmp_python_files: Path, tmp_path: Path):
        """--format json --output FILE writes JSON to file and produces no terminal noise."""
        import json

        from click.testing import CliRunner

        from archaeologist.cli import cli

        out_file = tmp_path / "report.json"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "analyze",
                str(tmp_python_files),
                "--format",
                "json",
                "--output",
                str(out_file),
                "--no-color",
            ],
        )
        assert result.exit_code == 0, result.output
        # Only the "saved" confirmation line is allowed — no analysis noise
        non_save_lines = [
            line
            for line in result.output.splitlines()
            if line.strip() and "JSON report saved" not in line
        ]
        assert non_save_lines == [], (
            f"Expected no terminal analysis output with --format json --output, got: {result.output!r}"
        )
        assert out_file.exists(), "Output file was not created"
        parsed = json.loads(out_file.read_text())
        assert "artifacts" in parsed

    def test_json_artifacts_count_matches_top(self, tmp_python_files: Path):
        """JSON `artifacts` array is capped at --top N; total_artifacts reflects the full count."""
        import json

        from click.testing import CliRunner

        from archaeologist.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "analyze",
                str(tmp_python_files),
                "--format",
                "json",
                "--top",
                "1",
                "--min-score",
                "0",
                "--no-color",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert len(parsed["artifacts"]) <= 1, "artifacts array should respect --top N"

    def test_scanned_at_is_timezone_aware(self, tmp_python_files: Path):
        """scanned_at in JSON output must include UTC timezone info."""
        import json

        from click.testing import CliRunner

        from archaeologist.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "analyze",
                str(tmp_python_files),
                "--format",
                "json",
                "--no-color",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        scanned = parsed["scanned_at"]
        assert "+" in scanned or scanned.endswith("Z"), (
            f"scanned_at should be timezone-aware, got: {scanned!r}"
        )

    def test_output_file_bad_path_gives_clean_error(self, tmp_python_files: Path):
        """Writing to a non-existent directory shows a clean error, not a traceback."""
        from click.testing import CliRunner

        from archaeologist.cli import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "analyze",
                str(tmp_python_files),
                "--format",
                "json",
                "--output",
                "/nonexistent/deep/path/report.json",
                "--no-color",
            ],
        )
        assert result.exit_code != 0
        assert "Cannot write" in result.output or "Error" in result.output, (
            f"Expected clean error message, got: {result.output!r}"
        )
