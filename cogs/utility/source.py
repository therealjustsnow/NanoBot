"""Source-viewer helpers for the `source` command: GitHub URL building, related
callable discovery, and symbol search over the codebase."""

import ast
import inspect
import os
import re

_GITHUB_BASE = "https://github.com/therealjustsnow/nanobot/blob/main/"
_SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", ".mypy_cache"}


def _gh_url(repo_root: str, filepath: str, start: int, end: int) -> str:
    rel = os.path.relpath(filepath, repo_root).replace(os.sep, "/")
    return f"{_GITHUB_BASE}{rel}#L{start}-L{end}"


def _related_callables(callback, repo_root: str) -> list[tuple[str, str, int, int]]:
    """Return module-level functions/classes in same file that callback references."""
    try:
        cb_src = inspect.getsource(callback)
        cb_file = os.path.abspath(inspect.getfile(callback))
    except (OSError, TypeError):
        return []

    module = inspect.getmodule(callback)
    if module is None:
        return []

    results = []
    for name, obj in vars(module).items():
        if name.startswith("__"):
            continue
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        if obj is callback:
            continue
        if not re.search(r"\b" + re.escape(name) + r"\b", cb_src):
            continue
        try:
            obj_file = os.path.abspath(inspect.getfile(obj))
        except TypeError:
            continue
        if obj_file != cb_file:
            continue
        try:
            lines, start = inspect.getsourcelines(obj)
            end = start + len(lines) - 1
        except (OSError, TypeError):
            continue
        results.append((name, obj_file, start, end))

    return sorted(results, key=lambda x: x[2])


def _symbol_search(repo_root: str, symbol: str) -> list[tuple[str, int, int]]:
    """Walk repo .py files, return (filepath, start, end) for each definition of symbol."""
    results = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    src = f.read()
                tree = ast.parse(src, filename=fpath)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    if node.name == symbol:
                        end = getattr(node, "end_lineno", node.lineno)
                        results.append((fpath, node.lineno, end))
                elif isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == symbol:
                            end = getattr(node, "end_lineno", node.lineno)
                            results.append((fpath, node.lineno, end))
    return results
