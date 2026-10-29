"""fapi SDK import 面白名单断言（任务 1 完成标准①——简版）。

SDK 子树（现役新包 binance_sdk_derivatives_trading_usds_futures +
旧 binance-futures-connector 七子树——并集分置见触金守护 v4.2）全
工程 import 点仅允许 core/data/fapi/stream.py 与 rest.py 两模块
（依赖方向硬约束：数据作业与执行器永不 import SDK）。旧七子树另受
全工程零容忍拦截（任何位置 import 即红——含白名单两文件，扫描归
触金守护；本简版对新包断言白名单面+对旧子树断言零命中双锚）。本
断言为 AST import 节点面；SDK 方法族白名单、python-binance 域限定
与动态字符串双面扫描归常驻触金守护（tests/test_touch_money_guard.py）。

模块白名单与子树常量单源驻触金守护（spec「白名单单源驻测试文件」
——权威载体），本简版引用之不重复定义。
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.test_touch_money_guard import (
    DEPRECATED_SDK_SUBTREES,
    FAPI_SDK_MODULE_WHITELIST,
    SDK_PACKAGE_TOPLEVEL,
)

_SDK_SUBTREES = (SDK_PACKAGE_TOPLEVEL,) + DEPRECATED_SDK_SUBTREES


def _iter_project_python_files(project_root: Path):
    """全工程 .py 清单（排除 venv/外部目录与二进制缓存）。"""
    exclude_parts = {"venv", "__pycache__", ".git", "node_modules",
                     "playwright-report", "dist", "build"}
    for path in sorted(project_root.rglob("*.py")):
        if any(part in exclude_parts for part in path.parts):
            continue
        yield path


def _imported_sdk_modules(tree: ast.AST) -> set[str]:
    """AST import 节点收集 SDK 包引用（import/from 双形制——新包
    顶层与旧 binance.* 子树）。"""
    found: set[str] = set()
    roots = {"binance", SDK_PACKAGE_TOPLEVEL}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in roots:
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in roots:
                found.add(module)
    return found


def test_binance_sdk_import_whitelist():
    """SDK 子树 import 点：新包⊆{stream.py, rest.py} 白名单+旧七子树
    全工程零命中（并集分置双锚）。"""
    project_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in _iter_project_python_files(project_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = _imported_sdk_modules(tree)
        sdk_hits = {
            module for module in modules
            if any(module == sub or module.startswith(sub + ".")
                   for sub in _SDK_SUBTREES)
        }
        if not sdk_hits:
            continue
        relative = path.relative_to(project_root)
        rel = relative.as_posix()
        for module in sorted(sdk_hits):
            if (module == SDK_PACKAGE_TOPLEVEL
                    or module.startswith(SDK_PACKAGE_TOPLEVEL + ".")):
                if rel not in FAPI_SDK_MODULE_WHITELIST:
                    violations.append(
                        f"{relative}: import {module}（现役包白名单外）")
            else:
                violations.append(
                    f"{relative}: import {module}（废弃 SDK 子树——"
                    f"全工程零容忍）")
    assert not violations, (
        "SDK import 面越界（现役包仅允许 core/data/fapi/stream.py 与 "
        f"rest.py；旧七子树全工程零 import）：\n" + "\n".join(violations))
