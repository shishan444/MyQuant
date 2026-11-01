"""trail 观测序列测试（paper-trading-revamp 组 E）。

零引用符号级断言（spec paper-qualification 末 Scenario）：trail_sl_price
白名单=写入点（executor equity dict+repo append_bar）+equity 查询面
（响应模型）；白名单外决策路径与 qualification 零 import/零读取。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_REPO = Path(__file__).resolve().parents[2]
# 白名单（双书写形制：属性访问 .trail_sl_price 与字符串 "trail_sl_price"）
_ALLOWED = {
    "core/persistence/orm/models.py",
    "core/persistence/repository/paper.py",
    "core/paper_trading/runtime/executor.py",
    "api/routes/paper_trading.py",
    "core/paper_trading/qualification.py",  # 注释提及（零读取——下面语义断言）
}


class TestObservationSurfaceZeroReference:
    def test_decision_paths_zero_reference_symbol_level(self):
        """符号级：白名单外 core/ 决策模块对 trail_sl_price 零引用。"""
        hits = []
        for py in (_REPO / "core").rglob("*.py"):
            rel = py.relative_to(_REPO).as_posix()
            if rel in _ALLOWED or "__pycache__" in rel:
                continue
            text = py.read_text(encoding="utf-8")
            if "trail_sl_price" in text:
                hits.append(rel)
        assert hits == []

    def test_qualification_zero_read(self):
        """qualification.py 对 trail_sl_price 零读取（判定用 ledger/终态 equity）。"""
        text = (_REPO / "core/paper_trading/qualification.py").read_text(encoding="utf-8")
        # 允许出现在注释；不允许任何属性读取形制
        reads = re.findall(r"\[['\"]trail_sl_price['\"]\]|\.trail_sl_price\b", text)
        assert reads == []
