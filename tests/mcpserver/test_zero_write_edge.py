"""MCP 零写边契约断言（build-strategy-lifecycle 2.4）。

三面：mcpserver/ 域零 lifecycle import（AST import 扫描）+零
strategy status 写路径（字面双书写形制——SQLAlchemy 表达式形与
SQL 字面形）+零状态触发工具名（build_server 工具清单集合断言，
沿 test_contract_parity 反射形制）。

负例自证：同一扫描器对内联已知正例命中——扫描器失效（恒空/恒
过）时负例用例红，防止断言面静默失效。
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_MCP_DIR = Path(__file__).resolve().parents[2] / "mcpserver"

# 状态触发类工具名禁止名单（transit 域动词族——MCP 查询只读，无
# 任何触发工具，契约显式声明）
_FORBIDDEN_TOOL_NAMES = {
    "transit", "transit_strategy", "migrate_strategy",
    "promote_strategy", "archive_strategy", "revalidate_strategy",
    "retire_strategy", "set_strategy_status", "change_strategy_status",
    "update_strategy_status", "strategy_transition",
}

# strategy status 写路径字面双书写形制（模块点形 import 消费面+
# SQL 字面形——「零引用」断言须形制族穷尽，工程坑位）
_STATUS_WRITE_LITERALS = (
    "core.strategy.lifecycle",   # import 面（service 唯一写通道）
    "UPDATE strategy SET status",
    "update(Strategy",
)


def run_async(coro):
    """asyncio.run 的环境兼容版（同 test_contract_parity 形制）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _mcp_sources() -> dict:
    """mcpserver/ 域全部 .py 源码（路径→文本）。"""
    return {p: p.read_text(encoding="utf-8")
            for p in sorted(_MCP_DIR.glob("*.py"))}


def _import_targets(source: str) -> set:
    """AST 扫描源码的全部 import 目标（ImportFrom.module+Import.name）。"""
    targets = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
        elif isinstance(node, ast.Import):
            targets.update(a.name for a in node.names)
    return targets


class TestZeroLifecycleImport:
    """面 1：mcpserver/ 域零 lifecycle import（AST 级）。"""

    def test_no_lifecycle_import_in_mcpserver(self):
        for path, src in _mcp_sources().items():
            targets = _import_targets(src)
            stray = {t for t in targets
                     if t == "core.strategy.lifecycle"
                     or t.startswith("core.strategy.lifecycle.")}
            assert not stray, f"{path.name} 引入 lifecycle 域: {stray}"

    def test_scanner_detects_known_positive(self):
        """负例自证：扫描器对已知正例（lifecycle import 源码）命中。"""
        positive = (
            "from core.strategy.lifecycle import transit\n"
            "from core.strategy.lifecycle.machine import TRANSITION_MATRIX\n"
            "import core.strategy.lifecycle.service\n")
        targets = _import_targets(positive)
        stray = {t for t in targets
                 if t == "core.strategy.lifecycle"
                 or t.startswith("core.strategy.lifecycle.")}
        assert stray == {"core.strategy.lifecycle",
                         "core.strategy.lifecycle.machine",
                         "core.strategy.lifecycle.service"}


class TestZeroStatusWritePath:
    """面 2：mcpserver/ 域零 strategy status 写路径（字面双形制）。"""

    def test_no_status_write_literals_in_mcpserver(self):
        for path, src in _mcp_sources().items():
            for lit in _STATUS_WRITE_LITERALS:
                assert lit not in src, \
                    f"{path.name} 含 status 写路径字面: {lit!r}"

    def test_scanner_detects_known_positive(self):
        """负例自证：字面扫描对已知正例（service CAS 同款写法）命中
        ——三条正例各对应一族书写形制。"""
        positives = [
            "from core.strategy.lifecycle.service import transit",
            "conn.execute(update(Strategy).values(status=target))",
            "UPDATE strategy SET status = 'paper'",
        ]
        for positive in positives:
            hits = sum(1 for lit in _STATUS_WRITE_LITERALS
                       if lit in positive)
            assert hits >= 1, f"扫描器漏检已知正例: {positive}"


class TestZeroTransitionTools:
    """面 3：工具清单零状态触发名（research 档=readonly∪research
    全集，覆盖全部注册工具）。"""

    def test_no_transition_tool_registered(self):
        from mcpserver.server import build_server

        mcp, _ = build_server(profile="research")
        names = {t.name for t in run_async(mcp.list_tools())}
        # 清单真实性锚（反射失效恒空时此处先红）
        assert names, "工具清单反射为空——断言面失效"
        stray = names & _FORBIDDEN_TOOL_NAMES
        assert not stray, f"MCP 注册了状态触发工具: {stray}"
