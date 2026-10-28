"""fapi SDK import 面白名单断言（任务 1 完成标准①——简版）。

binance SDK 子树（binance.um_futures / binance.websocket——期货
connector 包的 import 面）全工程 import 点仅允许 core/data/fapi/
stream.py 与 rest.py 两模块（依赖方向硬约束：数据作业与执行器永不
import SDK）。本断言为 AST import 节点面；binance-connector 方法
族白名单、python-binance 域限定与动态字符串双面扫描归常驻触金
守护（tests/test_touch_money_guard.py，change-1 任务 6）。
"""
from __future__ import annotations

import ast
from pathlib import Path

# binance SDK import 面允许的模块白名单（fapi 接入层两模块单源）
_FAPI_MODULE_WHITELIST = {
    Path("core/data/fapi/stream.py"),
    Path("core/data/fapi/rest.py"),
}

# 期货 connector 包的 import 面（顶层包名 binance 三包共享，按子树
# 区分：binance.um_futures / binance.websocket[.um_futures] 归
# binance-futures-connector；binance.client 归 python-binance——
# 其域限定归触金守护，不在本断言面）
_SDK_SUBTREES = ("binance.um_futures", "binance.websocket")


def _iter_project_python_files(project_root: Path):
    """全工程 .py 清单（排除 venv/外部目录与二进制缓存）。"""
    exclude_parts = {"venv", "__pycache__", ".git", "node_modules",
                     "playwright-report", "dist", "build"}
    for path in sorted(project_root.rglob("*.py")):
        if any(part in exclude_parts for part in path.parts):
            continue
        yield path


def _imported_binance_modules(tree: ast.AST) -> set[str]:
    """AST import 节点收集 binance 子树引用（import/from 双形制）。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "binance":
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] == "binance":
                found.add(module)
    return found


def test_binance_sdk_import_whitelist():
    """SDK 子树 import 点⊆{stream.py, rest.py}（模块白名单断言）。"""
    project_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in _iter_project_python_files(project_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = _imported_binance_modules(tree)
        sdk_hits = {
            module for module in modules
            if any(module == sub or module.startswith(sub + ".")
                   for sub in _SDK_SUBTREES)
        }
        if not sdk_hits:
            continue
        relative = path.relative_to(project_root)
        if relative not in _FAPI_MODULE_WHITELIST:
            violations.append(
                f"{relative}: import {sorted(sdk_hits)}（白名单外）")
    assert not violations, (
        "binance SDK import 面越界（仅允许 core/data/fapi/stream.py "
        f"与 rest.py）：\n" + "\n".join(violations))
