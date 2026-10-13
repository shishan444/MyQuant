"""价格比较动作模板面特征测试（fix-research-c2-price-actions · tester 组）。

从 change delta 契约推导：Requirement 正文要求动作分派表与「指标目录
声明的能力（含元数据透传层）」一致——掩码经 condition_kernel.mask_for
分派的全部模板面（event_study / counter_profile，与 condition_stats
同走 _evaluate_single_condition）均须获得同一语义与同一拒绝。既有
tests/validation/test_price_actions.py 只测了 condition_stats 链路，
本文件补其余两面：
- 正向：price_above 触发集与手工掩码（close>指标列）逐量守恒；
- 拒绝：未知 action 经模板面同样冒泡结构化 422（RESEARCH_PARAM_INVALID，
  detail.param=condition.action）——契约声称的「mask_for 路径自动获得」。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.counter_profile import counter_profile
from core.analysis.templates.event_study import event_study
from tests.helpers.data_factory import make_ohlcv

RANGES = [{"start": "2024-01-01T00:00:00Z", "end": "2024-01-25T00:00:00Z"}]


def _df():
    """600 bar 确定性数据，ema_20 下移一档保证 price_above 两向非空。"""
    d = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=5)
    d["ema_20"] = d["close"].rolling(20).mean().shift(1)
    return d


PRICE_ABOVE = {"subject": "ema_20", "action": "price_above", "target": 0}
UNKNOWN = {"subject": "ema_20", "action": "stays_above", "target": 0}


class TestEventStudyFace:
    """event_study 模板面：price_above 触发与拒绝。"""

    def test_price_above_event_count_matches_manual(self):
        """正向：price_above 条件的事件数与手工掩码命中数一致
        （close > ema_20 的每个 bar 即一个事件；尾部 window bar 内
        命中因观察窗不完整不计——与模板「丢弃不完整窗口」文档语义
        同口径推导期望）。"""
        d = _df()
        window = 3
        manual = int(((d["close"] > d["ema_20"]).values.nonzero()[0]
                      + window < len(d)).sum())
        res = event_study(d, condition=PRICE_ABOVE, window=window,
                          data_ranges=RANGES, min_events=5)
        assert res.payload["n_events"] == manual

    def test_unknown_action_rejected(self):
        """拒绝：未知 action 经 mask_for 路径冒泡结构化 422——
        不再以 0 事件形态走「小样本拒绝」的误导性报错。"""
        d = _df()
        with pytest.raises(ResearchError) as ei:
            event_study(d, condition=UNKNOWN, window=3, data_ranges=RANGES)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "condition.action"
        assert "price_above" in ei.value.detail["legal_values"]


class TestCounterProfileFace:
    """counter_profile 模板面：price_above 触发与拒绝。"""

    def test_price_above_trigger_conservation(self):
        """正向：分组触发守恒——conform + counter + 尾部窗口丢弃
        == 手工掩码命中数（price_above 真实参与分组判定）。"""
        d = _df()
        manual = int((d["close"] > d["ema_20"]).sum())
        res = counter_profile(d, condition=PRICE_ABOVE, direction="long",
                              data_ranges=RANGES, forward_bars=6,
                              min_samples=10, request_start=d.index[25])
        groups = res.payload["groups"]
        total = (groups["conform"]["count"] + groups["counter"]["count"]
                 + groups["dropped_tail_triggers"])
        assert total == manual
        assert groups["conform"]["count"] > 0 and groups["counter"]["count"] > 0

    def test_unknown_action_rejected(self):
        """拒绝：未知 action 同样经模板面结构化拒绝。"""
        d = _df()
        with pytest.raises(ResearchError) as ei:
            counter_profile(d, condition=UNKNOWN, direction="long",
                            data_ranges=RANGES, forward_bars=6,
                            request_start=d.index[25])
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "condition.action"
