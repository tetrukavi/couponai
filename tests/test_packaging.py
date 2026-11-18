"""Every third-party module imported by app/ must be a declared *runtime* dependency.

Guards against the server image failing to start because a package was only installed
locally as a dev/test dependency (this happened once with httpx).
"""

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _imported_top_level_modules() -> set[str]:
    mods = set()
    for f in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
    return mods - set(sys.stdlib_module_names) - {"app"}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_imports_are_runtime_dependencies():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    declared = {_norm(re.split(r"[<>=!~\[ ;]", d, maxsplit=1)[0]) for d in deps}
    dist_of = packages_distributions()
    missing = {}
    for mod in _imported_top_level_modules():
        dists = {_norm(d) for d in dist_of.get(mod, [mod])}
        if not dists & declared:
            missing[mod] = sorted(dists)
    assert not missing, f"imported by app/ but not in [project].dependencies: {missing}"
