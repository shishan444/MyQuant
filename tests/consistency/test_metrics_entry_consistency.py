"""跨入口绩效口径一致性（fix-indicator-output-mapping 阶段 1，必红测试）。

病根注记（2026-09-24 普查实锤；xfail strict = 收口完成转绿时报错
提醒摘牌，红→绿即阶段 1 验收主证据）：

- win_rate 绩效口径多处独立实现：
  * backtest/engine.py:583 逐笔小数（winning / len(pnl_arr)）
  * scoring/metrics.py:107-114 逐笔优先小数 + bar 级 fallback，
    零交易落 0.0（应缺失）
  * validation/rule_engine.py:181 百分比口径（*100）
- rule_engine 以信号配对判胜负、回测/模拟盘以逐笔平仓 pnl 判胜：
  两套配对逻辑在部分平仓/信号未平仓边界可能结构性不等，本测试
  仅断言同口径内一致与缺失语义，不要求跨口径逐值相等（实施评估
  预声明）。
- forward_stats 的 win_rate 是触发点前向窗口统计（非交易绩效），
  属展示统计口径；域差异在此登记，处置随 6.1 前置调查裁决。

retire-legacy 10.4 分拣：rule_engine 两用例（零交易缺失语义、小数
口径）与 forward_stats 口径登记用例已随 core/validation 壳退役删除
（守护对象消失）；compute_metrics 用例保留——计算层口径仍由
core/scoring/metrics.py 存活承载。上文病根注记为历史普查记录，
rule_engine/forward_stats 两入口已不存在。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]


def _flat_equity(n: int = 100, cash: float = 10_000.0) -> pd.Series:
    return pd.Series([cash] * n, index=pd.date_range("2026-01-01", periods=n, freq="h"))


# ---------------------------------------------------------------------------
# 零交易缺失语义：任一绩效入口收到零交易数据，win_rate 应为缺失
# （None/NaN，不参与评分维度），而非 0.0
# ---------------------------------------------------------------------------


class TestZeroTradeMissingSemantics:
    """spec：numerical-accuracy-verification「无交易缺省语义」。"""

    def test_compute_metrics_zero_trades_win_rate_missing(self):
        from core.scoring.metrics import compute_metrics

        metrics = compute_metrics(
            _flat_equity(), total_trades=0, trade_win_rate=None, trade_returns=None
        )
        assert metrics["win_rate"] is None or np.isnan(metrics["win_rate"])

