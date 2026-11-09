"""specs-实现一致核查（add-evaluation-ruler 任务 7.1）。

三件套一致性以单测断言承载：registry 结构锚（键数 48、无浪/箱条目
——清单列对条件链不可达为负向锚）、注册同步结构面（_TEMPLATES
计数 15、ruler_compare 必填键组、必填键表为权威源）、spec 条款
行为锚（「其他」类单独披露/跨查询禁混用披露/沙盒引用拒绝——行为
级断言汇总，细节用例在各模板测试文件）。

MODIFIED 块与主 specs 的 scenario 名集核对（ai-research-tools
条件统计条款 9 条守恒+新增 bootstrap 可选路径 1 条；research-error-
channel 必填键条款 1 条守恒）为实施时人工核对（openspec 库在仓库
外，不进单测依赖）——核对结论随交付报告登记。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]


class TestRegistryStructureAnchor:
    """evaluation-ruler「注册同步义务」Requirement 结构锚。"""

    def test_registry_key_count_unchanged(self):
        from core.features.registry import INDICATOR_REGISTRY

        assert len(INDICATOR_REGISTRY) == 48, \
            "指标注册表键数须与交付前一致（48）"

    def test_no_wave_or_box_entries(self):
        from core.features.registry import INDICATOR_REGISTRY

        forbidden = [k for k in INDICATOR_REGISTRY
                     if any(t in k.lower()
                            for t in ("wave", "box", "zigzag"))]
        assert forbidden == [], \
            f"浪/箱清单不得进指标注册表（防前视前提不相容）: {forbidden}"

    def test_ruler_columns_not_registry_outputs(self):
        """清单列名作条件 subject 不可达（负向锚）：盘点/对照输出列
        名不出现在任何注册指标的输出列集。"""
        from core.features.registry import INDICATOR_REGISTRY

        ruler_cols = {"waves", "boxes", "identification_rate",
                      "opposite_rate", "boundary_effect", "box_height",
                      "truncated_start", "per_trade"}
        outputs = set()
        for defn in INDICATOR_REGISTRY.values():
            meta = getattr(defn, "output_columns", None) or \
                getattr(defn, "outputs", None) or []
            outputs.update(str(c) for c in meta)
        overlap = outputs & ruler_cols
        assert overlap == set(), f"清单列名泄漏进条件链: {overlap}"


class TestRegistrationSyncStructure:
    """research-error-channel「模板必填键前置校验」MODIFIED 对齐。"""

    def test_templates_total_15(self):
        from api.routes.research.analysis import _TEMPLATES

        assert len(_TEMPLATES) == 15, \
            "本 change 落地后模板总数 15（存量 12+新增 3）"
        for name in ("wave_inventory", "box_inventory", "ruler_compare"):
            assert name in _TEMPLATES

    def test_ruler_required_keys_group(self):
        from api.routes.research.analysis import _TEMPLATE_REQUIRED_KEYS

        assert _TEMPLATE_REQUIRED_KEYS["ruler_compare"] == (
            ("mode", "trade_source"),)

    def test_required_keys_table_subset_of_enum(self):
        """键组表（权威源）⊆ 模板枚举——表引用不悬空。"""
        from api.routes.research.analysis import (
            _TEMPLATE_REQUIRED_KEYS, _TEMPLATES)

        assert set(_TEMPLATE_REQUIRED_KEYS) <= set(_TEMPLATES)


class TestSpecBehaviorAnchors:
    """evaluation-ruler 条款行为锚（汇总级——细节用例见各模板测试）。"""

    @staticmethod
    def _box_df(closes):
        import pandas as pd

        idx = pd.date_range("2026-07-01", periods=len(closes), freq="1h",
                            tz="UTC", name="timestamp")
        c = pd.Series(closes, index=idx, dtype=float)
        return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                             "volume": 1.0})

    def test_other_class_disclosed_separately(self):
        """「其他」类计入分母并单独披露（箱模式条款 scenario）。"""
        from core.analysis.templates.ruler_compare import compare_box

        df = self._box_df([100.0] * 8 + [95.0] * 4)
        idx = df.index
        trades = [{
            "seq": (0, 0), "entry_time": idx[6], "exit_time": idx[9],
            "entry_price": 100.0, "exit_price": 95.0, "size": 1.0,
            "direction": "long", "pnl": -1.0,
        }]
        out = compare_box(trades, df, edge_window=1)
        # 该笔触止损（95≤sl=100）——三类计数披露族在场
        assert {"n_opposite", "n_stop_line", "n_other"} <= set(out)
        assert out["n_opposite"] + out["n_stop_line"] + out["n_other"] == \
            out["n_trades"], "三类计数守恒（其他计入分母）"

    def test_comparability_disclosure_in_docstring(self):
        """跨查询禁混用披露义务（浪清单条款）：模板模块 docstring 承载
        （包 re-export 使函数遮蔽模块名——经 sys.modules 取模块）。"""
        import sys

        doc = sys.modules[
            "core.analysis.templates.wave_inventory"].__doc__
        assert "起点效应" in doc
        assert "混用拼接" in doc

    def test_sandbox_reference_rejection_is_spec_caliber(self):
        """沙盒引用拒绝（对照·数据来源条款 scenario）：文案锚与
        detail 指引双在场（拒绝面细节用例在 test_ruler_compare）。"""
        from core.analysis.templates.ruler_compare import _BACKTEST_GUIDANCE

        assert "沙盒回测不落库逐笔（防污染纪律）" in _BACKTEST_GUIDANCE
        assert "隔离验证记录 record_id" in _BACKTEST_GUIDANCE

    def test_default_t_test_anchor_unchanged(self):
        """condition_stats 缺省 t 检验存量锚（ai-research-tools MODIFIED
        条款：缺省输出与既有行为逐位一致——method 值与键序锚）。"""
        import numpy as np
        import pandas as pd

        from core.analysis.templates import condition_stats

        rng = np.random.default_rng(3)
        n = 300
        close = 60000 + np.cumsum(rng.normal(0, 100, n))
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({"open": close, "high": close + 50,
                           "low": close - 50, "close": close,
                           "volume": 1.0}, index=idx)
        out = condition_stats(
            df, indicator="EMA", params={"period": 10},
            condition={"subject": "close", "action": "gt",
                       "target": float(df["close"].median())},
            data_ranges=[{"symbol": "T", "timeframe": "1h",
                          "start": "2024-01-01",
                          "end": "2024-01-14"}]).to_dict()
        fs = out["confidence"]["forward_significance"]
        assert fs["method"] == "t_test_vs_baseline"
        assert list(fs) == ["method", "p_value", "for", "n_overlapped",
                            "n_total_signals", "baseline_mean"], \
            "缺省显著性段键序冻结（存量零变化锚）"
