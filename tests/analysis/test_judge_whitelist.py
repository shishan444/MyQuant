"""judge 白名单四锚（add-vr-signal-trace 任务 6.1——A6 守卫②，
T1-S2 锚族，D2 裁决的断言路线）。

四锚：①judge 实读键两路（rules 遍历+硬编码 total_trades）⊆常量；
②在产 profile 工厂键集（DEFAULT_PROFILE+tighten_profile 产物）⊆
常量；③注入新披露键（trail_series/mtf_gated_bars）后判定输出逐位
不变；④解析器全量形式（含 rules 键 payload）→422+未知名→422 回归
锁（防 _THRESHOLD_PROFILE_NAMES 扩名单或键集校验放宽使 rules 透传
复活）。
"""
from __future__ import annotations

import numpy as np
import pytest

from core.analysis.thresholds import (
    DEFAULT_PROFILE, JUDGE_METRIC_KEYS, judge, tighten_profile)
from core.analysis.validation_service import (
    _THRESHOLD_PROFILE_NAMES, _resolve_threshold_profile)
from core.analysis.errors import ResearchError


class TestAnchorOneJudgeReadPaths:
    """锚一：judge 实读键两路 ⊆ JUDGE_METRIC_KEYS。"""

    def test_rules_traversal_keys_subset(self):
        for prof in (DEFAULT_PROFILE, tighten_profile()):
            assert set(prof["rules"]) <= JUDGE_METRIC_KEYS

    def test_hardcoded_total_trades_in_whitelist(self):
        # 硬编码路径（证据闸门 metrics.get("total_trades")）的键在集内
        assert "total_trades" in JUDGE_METRIC_KEYS

    def test_gate_line_default_in_profile_or_fallback(self):
        # 闸门线读取 prof["rules"]["total_trades"]["value"]——键域已被
        # 锚一覆盖；缺省兜底 _MIN_TRADES_DEFAULT 不引入新 metrics 键
        assert {"total_return", "max_drawdown", "total_trades"} == set(
            JUDGE_METRIC_KEYS)


class TestAnchorTwoProfileFactories:
    """锚二：在产 profile 工厂键集 ⊆ 常量（未来新 profile 键即红）。"""

    def test_default_profile_keys(self):
        assert set(DEFAULT_PROFILE["rules"]) <= JUDGE_METRIC_KEYS

    def test_tightened_profile_keys(self):
        assert set(tighten_profile()["rules"]) <= JUDGE_METRIC_KEYS

    def test_factory_key_union_bounded(self):
        union = (set(DEFAULT_PROFILE["rules"])
                 | set(tighten_profile()["rules"]))
        assert union <= JUDGE_METRIC_KEYS


class TestAnchorThreeInjectionInvariance:
    """锚三：注入新披露键后判定输出逐位不变（REJ-08 考场无观点）。"""

    METRICS = {
        "total_return": 0.12, "max_drawdown": 0.08,
        "total_trades": 9,
        "sharpe_ratio": 1.4, "win_rate": 0.55, "liquidated": False,
        "signal_counts": {"entries": 20, "exits": 25, "adds": 3,
                          "reduces": 4},
        "sl_series": [{"time": "t0", "value": 1.0}],
    }
    INJECTED = {
        "trail_series": [{"time": "t0", "value": None},
                         {"time": "t1", "value": 42000.5}],
        "mtf_gated_bars": 17,
    }
    RETURNS = np.array([0.02, -0.01, 0.03, 0.015, -0.005, 0.025,
                        0.01, -0.02, 0.022])

    def test_verdict_identical_under_injection(self):
        base = judge(dict(self.METRICS), trade_returns=self.RETURNS)
        inj = judge({**self.METRICS, **self.INJECTED},
                    trade_returns=self.RETURNS)
        assert inj[0] == base[0]
        assert inj[1] == base[1]

    def test_inconclusive_path_also_invariant(self):
        few = np.array([0.01, 0.02])  # n<2 → CI None 路径
        base = judge({**self.METRICS, "total_trades": 2},
                     trade_returns=few)
        inj = judge({**self.METRICS, "total_trades": 2, **self.INJECTED},
                    trade_returns=few)
        assert (inj[0], inj[1]) == (base[0], base[1])

    def test_fail_path_invariant(self):
        base = judge({**self.METRICS, "total_return": -0.05},
                     trade_returns=self.RETURNS)
        inj = judge({**self.METRICS, "total_return": -0.05,
                     **self.INJECTED}, trade_returns=self.RETURNS)
        assert (inj[0], inj[1]) == (base[0], base[1])


class TestAnchorFourResolverRejectionLock:
    """锚四：解析器拒绝行为回归锁（422 两路——全量形式/未知名）。"""

    def test_full_form_with_rules_rejected(self):
        with pytest.raises(ResearchError) as ei:
            _resolve_threshold_profile({
                "name": "default",
                "rules": {"total_return": {"op": "gt", "value": 0.5}}})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_unknown_name_rejected(self):
        with pytest.raises(ResearchError):
            _resolve_threshold_profile({"name": "sneaky"})

    def test_name_whitelist_frozen(self):
        # 名单扩容（如增 "custom"）=调用方注入面复活——锚锁两名冻结
        assert _THRESHOLD_PROFILE_NAMES == ("default", "tightened")

    def test_non_dict_rejected(self):
        with pytest.raises(ResearchError):
            _resolve_threshold_profile(["name", "default"])  # type: ignore[arg-type]
