"""独立性守护：tests/reference/ 的 import 白名单。

参照实现出现 core.backtest / core.scoring / vectorbt / numba 引用即红
（spec: 参照实现独立性——零共享，路径异构）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REF_DIR = Path(__file__).resolve().parents[1] / "reference"

# 黑名单：被测计算模块与其计算内核（出现即独立性被破坏）
FORBIDDEN_SUBSTRINGS = ("core.backtest", "core.scoring", "vectorbt", "numba")

# 白名单：允许的第三方/项目内依赖
ALLOWED_ROOTS = {"numpy", "np", "pandas", "pd", "math", "dataclasses",
                 "typing", "__future__", "tests", "pathlib", "pytest"}


def test_reference_import_whitelist():
    violations: list[str] = []
    files = sorted(REF_DIR.glob("*.py"))
    assert files, "tests/reference/ 不应为空"
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root not in ALLOWED_ROOTS:
                    violations.append(f"{f.name}: import {name}")
                for bad in FORBIDDEN_SUBSTRINGS:
                    if bad in name:
                        violations.append(f"{f.name}: FORBIDDEN import {name}")
    assert not violations, "参照实现独立性被破坏:\n" + "\n".join(violations)


def test_reference_no_from_core_import():
    """更严格的语义级检查：任何 core.* 引用都不得出现在 import 语句。"""
    for f in sorted(REF_DIR.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("core"):
                pytest.fail(f"{f.name}: from {node.module} import ... —— 参照层禁止依赖被测 core 模块")
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("core"):
                        pytest.fail(f"{f.name}: import {a.name} —— 参照层禁止依赖被测 core 模块")
