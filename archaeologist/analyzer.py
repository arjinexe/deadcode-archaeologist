"""
Analyzer — static analysis of the current codebase.

Fix 7: _py_files_cache inconsistency.
  Previous version: first call with skip_tests=False populated the cache,
  subsequent calls with skip_tests=True returned the same full list.
  Fix: two separate cached lists — _all_py_files and _non_test_py_files —
  built lazily and independently.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

from .models import Artifact, ArtifactType

PYTHON_EXTENSIONS = {".py"}
JS_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
CODE_EXTENSIONS = PYTHON_EXTENSIONS | JS_EXTENSIONS

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "vendor",
    ".tox",
}

_COMMENT_NOISE_WORDS: set[str] = {
    "this",
    "that",
    "with",
    "from",
    "into",
    "over",
    "when",
    "then",
    "also",
    "just",
    "only",
    "some",
    "each",
    "both",
    "more",
    "less",
    "data",
    "item",
    "list",
    "dict",
    "type",
    "name",
    "file",
    "path",
    "line",
    "text",
    "code",
    "note",
    "todo",
    "fixme",
    "hack",
    "print",
    "return",
    "raise",
    "yield",
    "break",
    "pass",
    "true",
    "false",
    "none",
    "null",
    "undefined",
    "error",
    "value",
    "result",
    "output",
    "input",
    "index",
    "count",
    "size",
    "length",
    "width",
    "height",
    "test",
    "check",
    "make",
    "call",
    "send",
    "read",
    "write",
    "load",
    "save",
    "open",
    "close",
    "stop",
    "start",
    "args",
    "kwargs",
    "self",
    "cls",
}


def _should_skip(fp: Path) -> bool:
    return any(part in SKIP_DIRS for part in fp.parts)


def _read(fp: Path) -> str | None:
    try:
        return fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# AST cache — parse each .py file exactly once
# ─────────────────────────────────────────────────────────────────────────────


class _ASTCache:
    def __init__(self) -> None:
        self._src: dict[Path, str | None] = {}
        self._tree: dict[Path, ast.AST | None] = {}

    def get(self, fp: Path) -> tuple[str | None, ast.AST | None]:
        if fp not in self._src:
            src = _read(fp)
            self._src[fp] = src
            if src is not None:
                try:
                    self._tree[fp] = ast.parse(src, filename=str(fp))
                except SyntaxError:
                    self._tree[fp] = None
            else:
                self._tree[fp] = None
        return self._src[fp], self._tree[fp]


# ─────────────────────────────────────────────────────────────────────────────
# Python AST helpers
# ─────────────────────────────────────────────────────────────────────────────


def _all_py_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


def _getattr_strings(tree: ast.AST) -> set[str]:
    """String literals used as second arg of getattr() — dynamic references."""
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            is_getattr = (isinstance(f, ast.Name) and f.id == "getattr") or (
                isinstance(f, ast.Attribute) and f.attr == "getattr"
            )
            if is_getattr and len(n.args) >= 2:
                arg = n.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.add(arg.value)
    return names


def _dunder_all_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "__all__" and isinstance(n.value, (ast.List, ast.Tuple)):
                    for elt in n.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                names.add(elt.value)
    return names


def _decorator_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in n.decorator_list:
                if isinstance(dec, ast.Name):
                    names.add(dec.id)
                elif isinstance(dec, ast.Attribute):
                    names.add(dec.attr)
                elif isinstance(dec, ast.Call):
                    if isinstance(dec.func, ast.Name):
                        names.add(dec.func.id)
                    elif isinstance(dec.func, ast.Attribute):
                        names.add(dec.func.attr)
    return names


def _has_star_import(tree: ast.AST) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for alias in n.names:
                if alias.name == "*":
                    return True
    return False


def _load_names_in_scope(func: ast.AST) -> set[str]:
    used: set[str] = set()
    for n in ast.walk(func):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            used.add(n.id)
    return used


def _assigned_names_in_scope(func: ast.AST) -> dict[str, int]:
    """
    Simple assignments inside a function, excluding edge-case vars:
    for-loop, with, except, comprehension, augmented assign, global/nonlocal,
    inner function/class definitions.
    """
    excluded: set[str] = set()
    for n in ast.walk(func):
        if isinstance(n, ast.For):
            for m in ast.walk(n.target):
                if isinstance(m, ast.Name):
                    excluded.add(m.id)
        elif isinstance(n, ast.With):
            for item in n.items:
                if item.optional_vars:
                    for m in ast.walk(item.optional_vars):
                        if isinstance(m, ast.Name):
                            excluded.add(m.id)
        elif isinstance(n, ast.ExceptHandler):
            if n.name:
                excluded.add(n.name)
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in getattr(n, "generators", []):
                for m in ast.walk(gen.target):
                    if isinstance(m, ast.Name):
                        excluded.add(m.id)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            for name in n.names:
                excluded.add(name)
        elif isinstance(n, ast.AugAssign):
            if isinstance(n.target, ast.Name):
                excluded.add(n.target.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not func:
            excluded.add(n.name)

    assigned: dict[str, int] = {}
    for n in ast.walk(func):
        if n is func:
            continue
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_") and t.id not in excluded:
                    assigned.setdefault(t.id, n.lineno)
        elif (
            isinstance(n, ast.AnnAssign)
            and (isinstance(n.target, ast.Name) and n.value and not n.target.id.startswith("_"))
            and n.target.id not in excluded
        ):
            assigned.setdefault(n.target.id, n.lineno)
    return assigned


# ─────────────────────────────────────────────────────────────────────────────
# JS/TS helpers (regex-based)
# ─────────────────────────────────────────────────────────────────────────────

_JS_FUNC_DEF_RE = [
    re.compile(
        r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[(<]",
        re.MULTILINE,
    ),
    re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(",
        re.MULTILINE,
    ),
    re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s+)?function",
        re.MULTILINE,
    ),
]
_JS_IMPORT_RE = [
    re.compile(r"^import\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s+['\"]", re.MULTILINE),
    re.compile(r"^import\s+\{([^}]+)\}\s+from\s+['\"]", re.MULTILINE),
    re.compile(r"^import\s+\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+from\s+['\"]", re.MULTILINE),
]
_JS_FRAMEWORK_HOOKS: set[str] = {
    "default",
    "getStaticProps",
    "getServerSideProps",
    "getStaticPaths",
    "getInitialProps",
    "render",
    "componentDidMount",
    "componentWillUnmount",
    "componentDidUpdate",
    "shouldComponentUpdate",
    "getDerivedStateFromProps",
    "getSnapshotBeforeUpdate",
    "componentDidCatch",
    "constructor",
    "handler",
    "middleware",
    "setup",
    "teardown",
    "main",
    "init",
    "loader",
    "action",
    "meta",
    "links",
}
_JS_GLOBALS: set[str] = {
    "React",
    "Component",
    "PureComponent",
    "Fragment",
    "console",
    "process",
    "require",
    "module",
    "exports",
    "window",
    "document",
    "navigator",
    "location",
    "Promise",
    "Array",
    "Object",
    "String",
    "Number",
    "Boolean",
    "Math",
    "Date",
    "JSON",
    "Error",
    "Map",
    "Set",
    "WeakMap",
    "WeakSet",
    "setTimeout",
    "setInterval",
    "clearTimeout",
    "clearInterval",
    "fetch",
    "URL",
    "URLSearchParams",
    "globalThis",
    "Symbol",
}


def _js_strip(src: str) -> str:
    s = re.sub(r"//.*$", " ", src, flags=re.MULTILINE)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r'"(?:[^"\\]|\\.)*"', '""', s)
    s = re.sub(r"'(?:[^'\\]|\\.)*'", "''", s)
    s = re.sub(r"`(?:[^`\\]|\\.)*`", "``", s)
    return s


def _js_name_count(name: str, stripped: str) -> int:
    return len(re.findall(r"\b" + re.escape(name) + r"\b", stripped))


def _js_dead_functions(filepath: Path, rel: str, src: str) -> Iterator[Artifact]:
    lines = src.splitlines()
    stripped = _js_strip(src)
    seen: set[str] = set()

    for pat in _JS_FUNC_DEF_RE:
        for m in pat.finditer(src):
            name = m.group(1)
            if not name or name in _JS_FRAMEWORK_HOOKS or name in _JS_GLOBALS:
                continue
            if name.startswith("_") or name in seen:
                continue
            seen.add(name)
            lineno = src[: m.start()].count("\n") + 1
            if _js_name_count(name, stripped) <= 1:
                end = min(lineno + 6, len(lines))
                yield Artifact(
                    type=ArtifactType.DEAD_FUNCTION,
                    title=f"{name}() — called by no one",
                    description=f"Defined in {rel}:{lineno} but never referenced in this project.",
                    code_snippet="\n".join(lines[lineno - 1 : end]),
                    file_path=rel,
                    line_number=lineno,
                    tragedy_score=35,
                    tags=["dead-function", "javascript"],
                )


def _js_ghost_imports(filepath: Path, rel: str, src: str) -> Iterator[Artifact]:
    lines = src.splitlines()
    stripped = _js_strip(src)

    for pat in _JS_IMPORT_RE:
        for m in pat.finditer(src):
            lineno = src[: m.start()].count("\n") + 1
            snippet = lines[lineno - 1] if lineno <= len(lines) else ""

            if "{" in m.group(0):
                for part in m.group(1).split(","):
                    part = part.strip()
                    alias = re.match(r"\S+\s+as\s+(\S+)", part)
                    local = alias.group(1) if alias else (part.split()[0] if part else None)
                    if not local or local in _JS_GLOBALS:
                        continue
                    if _js_name_count(local, stripped) <= 1:
                        yield Artifact(
                            type=ArtifactType.GHOST_IMPORT,
                            title=f"import {{ {local} }} — never used",
                            description=f"Named import '{local}' in {rel}:{lineno} is never referenced.",
                            code_snippet=snippet,
                            file_path=rel,
                            line_number=lineno,
                            tragedy_score=22,
                            tags=["ghost-import", "javascript"],
                        )
            else:
                local = m.group(1)
                if not local or local in _JS_GLOBALS:
                    continue
                if _js_name_count(local, stripped) <= 1:
                    yield Artifact(
                        type=ArtifactType.GHOST_IMPORT,
                        title=f"import {local} — never used",
                        description=f"Import '{local}' in {rel}:{lineno} is never referenced.",
                        code_snippet=snippet,
                        file_path=rel,
                        line_number=lineno,
                        tragedy_score=22,
                        tags=["ghost-import", "javascript"],
                    )


# ─────────────────────────────────────────────────────────────────────────────
# Analyzer
# ─────────────────────────────────────────────────────────────────────────────


class Analyzer:
    """
    Static analysis on the current codebase.
    Accepts either a directory or a single code file as repo_path.

    Fix 7: Two separate cached file lists — _all_py and _non_test_py —
    so skip_tests=True never accidentally returns test files from a
    skip_tests=False cache that was populated first.
    """

    def __init__(self, repo_path: str, verbose: bool = False) -> None:
        target = Path(repo_path).resolve()
        if target.is_file():
            self.repo_path = target.parent
            self._single_file: Path | None = target
        else:
            self.repo_path = target
            self._single_file = None

        self.verbose = verbose
        self._cache = _ASTCache()

        # Fix 7: separate caches per skip_tests value
        self._all_py: list[Path] | None = None  # skip_tests=False
        self._non_test_py: list[Path] | None = None  # skip_tests=True

        self._py_names: set[str] | None = None
        self._js_names: set[str] | None = None

    # ── File iterators ─────────────────────────────────────────────────────

    def _py_files(self, skip_tests: bool = False) -> list[Path]:
        """
        Return cached list of .py files.
        Two separate lists ensure skip_tests=True never returns test files
        even when skip_tests=False was called first.
        """
        if skip_tests:
            if self._non_test_py is None:
                self._non_test_py = self._scan_py_files(skip_tests=True)
            return self._non_test_py
        else:
            if self._all_py is None:
                self._all_py = self._scan_py_files(skip_tests=False)
            return self._all_py

    def _scan_py_files(self, skip_tests: bool) -> list[Path]:
        if self._single_file and self._single_file.suffix == ".py":
            return [self._single_file]
        result: list[Path] = []
        for fp in self.repo_path.rglob("*.py"):
            if _should_skip(fp):
                continue
            if fp.name in ("setup.py", "conf.py", "conftest.py"):
                continue
            if skip_tests and ("test_" in fp.name or "_test" in fp.name):
                continue
            result.append(fp)
        return result

    def _js_files(self) -> Iterator[Path]:
        if self._single_file and self._single_file.suffix in JS_EXTENSIONS:
            yield self._single_file
            return
        for ext in JS_EXTENSIONS:
            for fp in self.repo_path.rglob(f"*{ext}"):
                if not _should_skip(fp):
                    yield fp

    # ── Name indices (lazy) ────────────────────────────────────────────────

    def _get_py_names(self) -> set[str]:
        if self._py_names is None:
            names: set[str] = set()
            for fp in self._py_files(skip_tests=False):
                _, tree = self._cache.get(fp)
                if tree:
                    names |= _all_py_names(tree)
                    names |= _getattr_strings(tree)
                    names |= _dunder_all_names(tree)
                    names |= _decorator_names(tree)
            self._py_names = names
        return self._py_names

    def _get_js_names(self) -> set[str]:
        if self._js_names is None:
            names: set[str] = set()
            for fp in self._js_files():
                src = _read(fp)
                if src:
                    names |= set(re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b", _js_strip(src)))
            self._js_names = names
        return self._js_names

    # ── Dead Functions ─────────────────────────────────────────────────────

    _FRAMEWORK_HOOKS: set[str] = {
        "__init__",
        "__str__",
        "__repr__",
        "__len__",
        "__eq__",
        "__hash__",
        "__call__",
        "__enter__",
        "__exit__",
        "__iter__",
        "__next__",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__contains__",
        "__add__",
        "__sub__",
        "__mul__",
        "__truediv__",
        "__floordiv__",
        "__mod__",
        "__pow__",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
        "__bool__",
        "__int__",
        "__float__",
        "__bytes__",
        "__del__",
        "__class_getitem__",
        "__init_subclass__",
        "setUp",
        "tearDown",
        "setUpClass",
        "tearDownClass",
        "save",
        "delete",
        "clean",
        "validate",
        "full_clean",
        "get",
        "post",
        "put",
        "patch",
        "handle",
        "run",
        "execute",
        "process",
        "main",
        "setup",
        "teardown",
        "configure",
        "initialize",
        "on_get",
        "on_post",
        "on_put",
        "on_delete",
        "on_patch",
        "dispatch",
        "perform_create",
        "perform_update",
        "perform_destroy",
    }

    def find_dead_functions(self) -> Iterator[Artifact]:
        seen: set[tuple[str, int]] = set()
        all_py_names = self._get_py_names()

        for fp in self._py_files(skip_tests=True):
            src, tree = self._cache.get(fp)
            if tree is None or src is None:
                continue
            if _has_star_import(tree):
                continue

            rel = str(fp.relative_to(self.repo_path))
            extra = _getattr_strings(tree) | _dunder_all_names(tree) | _decorator_names(tree)
            lines = src.splitlines()

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                name = node.name
                if name.startswith("__") and name.endswith("__"):
                    continue
                if name in self._FRAMEWORK_HOOKS or name in extra:
                    continue

                key = (rel, node.lineno)
                if key in seen:
                    continue

                if name not in all_py_names:
                    seen.add(key)
                    end = min(getattr(node, "end_lineno", node.lineno + 8), node.lineno + 12)
                    snippet = "\n".join(lines[node.lineno - 1 : end])
                    yield Artifact(
                        type=ArtifactType.DEAD_FUNCTION,
                        title=f"def {name}() — called by no one",
                        description=(
                            f"Defined in {rel}:{node.lineno} but never referenced "
                            f"anywhere in the project."
                            + (" (async)" if isinstance(node, ast.AsyncFunctionDef) else "")
                        ),
                        code_snippet=snippet,
                        file_path=rel,
                        line_number=node.lineno,
                        tragedy_score=self._score_dead_func(node),
                        tags=["dead-function", "python"],
                    )

        seen_js: set[tuple[str, int]] = set()
        for fp in self._js_files():
            src = _read(fp)
            if src is None:
                continue
            rel = str(fp.relative_to(self.repo_path))
            for a in _js_dead_functions(fp, rel, src):
                key = (a.file_path, a.line_number or 0)
                if key not in seen_js:
                    seen_js.add(key)
                    yield a

    def _score_dead_func(self, node: ast.AST) -> int:
        score = 30
        lineno = getattr(node, "lineno", 0)
        end_line = getattr(node, "end_lineno", lineno)
        score += min((end_line - lineno) * 2, 30)
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                score += 15
        args = getattr(node, "args", None)
        if args:
            score += min(sum(1 for a in args.args if a.annotation) * 5, 15)
        return min(score, 100)

    # ── Ghost Imports ──────────────────────────────────────────────────────

    def find_ghost_imports(self) -> Iterator[Artifact]:
        seen: set[tuple[str, int, str]] = set()

        for fp in self._py_files(skip_tests=False):
            src, tree = self._cache.get(fp)
            if tree is None or src is None:
                continue
            rel = str(fp.relative_to(self.repo_path))
            lines = src.splitlines()

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        local = alias.asname or alias.name.split(".")[0]
                        if local == "*":
                            continue
                        key = (rel, node.lineno, local)
                        if key in seen:
                            continue
                        if self._py_name_unused(local, lines, node.lineno):
                            seen.add(key)
                            snippet = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
                            yield Artifact(
                                type=ArtifactType.GHOST_IMPORT,
                                title=f"import {alias.name} — serves no one",
                                description=f"Imported in {rel}:{node.lineno} but '{local}' is never used.",
                                code_snippet=snippet,
                                file_path=rel,
                                line_number=node.lineno,
                                tragedy_score=25,
                                tags=["ghost-import", "python"],
                            )

                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        local = alias.asname or alias.name
                        key = (rel, node.lineno, local)
                        if key in seen:
                            continue
                        if self._py_name_unused(local, lines, node.lineno):
                            seen.add(key)
                            mod = node.module or "?"
                            snippet = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
                            yield Artifact(
                                type=ArtifactType.GHOST_IMPORT,
                                title=f"from {mod} import {alias.name} — never used",
                                description=f"Imported in {rel}:{node.lineno} but '{local}' is never referenced.",
                                code_snippet=snippet,
                                file_path=rel,
                                line_number=node.lineno,
                                tragedy_score=20,
                                tags=["ghost-import", "python"],
                            )

        seen_js: set[tuple[str, int, str]] = set()
        for fp in self._js_files():
            src = _read(fp)
            if src is None:
                continue
            rel = str(fp.relative_to(self.repo_path))
            for a in _js_ghost_imports(fp, rel, src):
                key = (a.file_path, a.line_number or 0, a.title)
                if key not in seen_js:
                    seen_js.add(key)
                    yield a

    def _py_name_unused(self, name: str, lines: list[str], import_lineno: int) -> bool:
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
        return all(not (i != import_lineno and pat.search(line)) for i, line in enumerate(lines, 1))

    # ── Lone Variables ─────────────────────────────────────────────────────

    def find_lone_variables(self) -> Iterator[Artifact]:
        seen: set[tuple[str, int]] = set()

        for fp in self._py_files(skip_tests=True):
            src, tree = self._cache.get(fp)
            if tree is None or src is None:
                continue
            rel = str(fp.relative_to(self.repo_path))
            lines = src.splitlines()

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue

                params: set[str] = set()
                for a in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                    params.add(a.arg)
                if node.args.vararg:
                    params.add(node.args.vararg.arg)
                if node.args.kwarg:
                    params.add(node.args.kwarg.arg)

                assigned = _assigned_names_in_scope(node)
                used = _load_names_in_scope(node)

                for var, lineno in assigned.items():
                    if var in used or var in params:
                        continue
                    key = (rel, lineno)
                    if key in seen:
                        continue
                    seen.add(key)
                    snippet = lines[lineno - 1].strip() if lineno <= len(lines) else ""
                    yield Artifact(
                        type=ArtifactType.LONE_VARIABLE,
                        title=f'"{var}" — assigned, never read',
                        description=(
                            f"Variable '{var}' assigned in {rel}:{lineno} "
                            f"inside `{node.name}()` but never subsequently used."
                        ),
                        code_snippet=snippet,
                        file_path=rel,
                        line_number=lineno,
                        tragedy_score=15,
                        tags=["lone-variable", "python"],
                    )

    # ── Orphaned Comments ──────────────────────────────────────────────────

    def find_orphaned_comments(self) -> Iterator[Artifact]:
        ref_pat = re.compile(r"`(\w+)\(\)`|`(\w+)`|(?<!\w)([a-z][a-z0-9_]{4,})\(\)")
        seen: set[tuple[str, int]] = set()

        try:
            import builtins as _b

            py_builtins: set[str] = set(dir(_b))
        except Exception:
            py_builtins = set()
        py_builtins |= {"self", "cls", "args", "kwargs", "True", "False", "None"}

        for fp in self._py_files(skip_tests=False):
            src, tree = self._cache.get(fp)
            if tree is None or src is None:
                continue
            rel = str(fp.relative_to(self.repo_path))
            lines = src.splitlines()

            current: set[str] = _all_py_names(tree)
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    current.add(n.name)
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            current.add(t.id)

            for i, line in enumerate(lines):
                stripped = line.strip()
                if not stripped.startswith("#"):
                    continue
                comment_text = stripped[1:].strip()
                lineno = i + 1

                for m in ref_pat.finditer(comment_text):
                    ref = m.group(1) or m.group(2) or m.group(3)
                    if not ref or len(ref) < 5:
                        continue
                    if ref in current or ref in py_builtins:
                        continue
                    if ref.lower() in _COMMENT_NOISE_WORDS:
                        continue
                    if not re.match(r"^[a-z][a-z0-9_]+$", ref):
                        continue

                    key = (rel, lineno)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield Artifact(
                        type=ArtifactType.ORPHANED_COMMENT,
                        title=f'Comment references "{ref}" — which no longer exists',
                        description=(
                            f"Line {lineno} in {rel} references `{ref}`, "
                            f"but that name no longer exists in this file."
                        ),
                        code_snippet=stripped,
                        file_path=rel,
                        line_number=lineno,
                        tragedy_score=35,
                        tags=["orphaned-comment", "python"],
                    )
                    break

    # ── Misc ───────────────────────────────────────────────────────────────

    def count_analyzable_files(self) -> int:
        if self._single_file:
            return 1
        count = 0
        for fp in self.repo_path.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in CODE_EXTENSIONS and not _should_skip(fp):
                count += 1
        return count
