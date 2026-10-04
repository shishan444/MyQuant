"""条件内核判定链单测（fix-mcp-error-channel 卡3b，0.3.2）。

_mask_base target 四段判定链三分支直测（design 测试设计 unit②）：
列在幂等 / 数值透传 / decode 现算 / 不可解码结构化拒绝——修正案
3b 的修复点在求值内核，路由层 integration 只覆盖 decode 成功分支，
幂等与拒绝分支在此钉死（D18：断言面与修复面同层）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.condition_kernel import _mask_base
from core.analysis.hypothesis_service import ResearchError


def _work(n: int = 60) -> pd.DataFrame:
    """纯 OHLCV 基准帧（无指标矩阵列——判定链输入原形）。"""
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = np.linspace(100, 160, n) + np.sin(np.arange(n)) * 5
    return pd.DataFrame({
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": np.linspace(10, 100, n), "trades": 10,
    }, index=idx)


class TestMaskBaseTargetChain:
    """target 四段判定链（卡3b）：三分支 + 透传边界。"""

    def test_column_present_idempotent_skip(self):
        """分支1 列在 → 幂等跳过：预置假值列不被现算覆盖（列在优先
        于 decode——可解码列名已在列时不得重算）。"""
        w = _work()
        w["sma_20"] = np.linspace(1.0, 2.0, n := len(w))
        before = w["sma_20"].copy()
        mask = _mask_base(w, {"subject": "close",
                              "action": "cross_above_series",
                              "target": "sma_20"})
        assert before.equals(w["sma_20"]), "列在必须幂等——不得现算覆盖"
        assert len(mask) == n
        assert not mask.any()  # close(≈100+) 上穿 sma_20(≈1-2) 恒 False

    def test_numeric_string_target_passthrough(self):
        """分支2 数值字符串 → 不进链透传：不生成任何列（值比较语义
        归 engine 既有链）。"""
        w = _work()
        n_cols = len(w.columns)
        mask = _mask_base(w, {"subject": "close", "action": "gt",
                              "target": "70"})
        assert len(w.columns) == n_cols, "数值 target 不得触发列现算"
        assert bool(mask.all())  # close 全程 > 70

    def test_decodable_target_computed_into_work(self):
        """分支3 decode 成功 → _compute_into 现算入 work（「入口放行
        +求值不算」假象型病灶的修复点：列须真实产出且收敛）。"""
        w = _work()
        mask = _mask_base(w, {"subject": "close", "action": "gt",
                              "target": "rsi_14"})
        assert "rsi_14" in w.columns, "可解码 target 必须现算入 work"
        assert w["rsi_14"].notna().any(), "现算列须收敛（非全 NaN）"
        assert len(mask) == len(w)
        assert bool(mask.any())

    def test_undecodable_non_base_target_rejected(self):
        """分支4 decode 失败且非基础列 → 结构化拒绝（非静默 0 样本、
        非裸 KeyError）。"""
        w = _work()
        with pytest.raises(ResearchError) as ei:
            _mask_base(w, {"subject": "close", "action": "gt",
                           "target": "no_such_col_x"})
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
        assert ei.value.detail["param"] == "condition.target"
        assert ei.value.detail["value"] == "no_such_col_x"
