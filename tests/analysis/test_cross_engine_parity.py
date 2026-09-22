"""condition_stats 与假设验证引擎 change_pct 互证（U5 / tasks 4.1）。

同一确定性 parquet、同一条件、同一区间喂两侧独立实现：

- 侧 A（引擎）：validate_hypothesis 的 triggers（time/trigger_price 为引擎
  真实输出；change_pct 在 TriggerRecord 内 round 2 不可用——design
  Decision 9 ①，窗口末根价按 engine 的 end_loc=min(loc+W+1, len) 规则
  即 close.iloc[min(loc+N, len-1)] 重算未 round 值）；
- 侧 B（模板）：condition_stats 的前向收益向量化式
  close.pct_change(N).shift(-N)（模板与 correlation_scan.py:45 同款；模板
  聚合输出 _series_stats 未 round，mean/std 等可直接与引擎重算值对拍）。

比较器三则（design Decision 9）：① 量纲——引擎 ×100 vs 模板小数×100；
② 尾部触发点排除——loc+N > len-1 的引擎用窗口末根 close 计算而模板丢弃
（dropped_tail_signals），只比较完整窗口触发点；③ then_window == forward_bars。

指标条件用例说明：引擎 indicator_params 形参在函数体内未被消费（死参数），
EMA(50) 经 compute_all_indicators 的 _DEFAULT_PARAMS 默认周期集（10/20/50/
100/200）进入引擎侧，与模板 _compute_indicator("EMA", {"period": 50})
同源同值——互证不依赖 indicator_params 覆盖机制。
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.templates import condition_stats
from core.data.storage import load_parquet
from core.validation.engine import validate_hypothesis
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"
N_FORWARD = 6  # 比较器③：then_window == forward_bars


@pytest.fixture()
def parity_env(tmp_path):
    """400 bar 4h 确定性数据 + 请求区间切片。"""
    df = make_ohlcv(n=400, freq="4h", start="2024-01-01", seed=7)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    start = df.index[0].isoformat()
    end = df.index[-1].isoformat()
    return df, data_dir, start, end


def _both_sides(parity_env, condition, indicator="EMA", params=None):
    """跑两侧并归位公共结构：(df_s, 引擎结果, 模板结果)。"""
    df, data_dir, start, end = parity_env
    engine = validate_hypothesis(
        SYMBOL, "4h", start, end,
        when_conditions=[condition], then_conditions=[],  # 空 THEN：change_pct 独立于 then 匹配产出
        then_window=N_FORWARD, data_dir=str(data_dir))

    df_s = load_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    df_s = df_s[(df_s.index >= pd.Timestamp(start))
                & (df_s.index <= pd.Timestamp(end))]
    tpl = condition_stats(
        df_s, indicator=indicator, params=params or {"period": 50},
        condition=condition, data_ranges=[{"start": start, "end": end}],
        forward_bars=N_FORWARD, request_start=None)
    return df_s, engine, tpl


def _compare(df_s, engine, tpl_payload, tpl_sample_size):
    """三则比较器主体：返回完整窗口触发点的引擎侧逐点值（未 round ×100）。"""
    n = len(df_s)
    locs = [df_s.index.get_loc(pd.Timestamp(t.time)) for t in engine.triggers]
    prices = [t.trigger_price for t in engine.triggers]
    full = [l for l in locs if l + N_FORWARD <= n - 1]  # 完整窗口触发点
    tail = [l for l in locs if l + N_FORWARD > n - 1]

    # ② 尾部对齐：模板 dropped_tail_signals = 引擎尾部触发点
    # （loc ∈ [n-N, n-2]，不满窗口仍以窗口末根 close 产出 change）+ 末根
    # 命中（loc == n-1 时 window_df 为空被引擎 continue 跳过、不产出
    # trigger——该命中只存在于模板侧的 NaN 丢弃）。末根命中不在引擎
    # triggers 内、无法从其恢复，故以差值 ∈ {0, 1} 刻画这一语义差
    assert 0 <= tpl_payload["dropped_tail_signals"] - len(tail) <= 1

    # ① 量纲 + 逐点（完整窗口）：引擎未 round ×100 vs 模板向量化式 ×100
    engine_changes = [
        (df_s["close"].iloc[l + N_FORWARD] / tp - 1.0) * 100.0
        for l, tp in zip(locs, prices) if l + N_FORWARD <= n - 1]
    fwd = df_s["close"].pct_change(N_FORWARD).shift(-N_FORWARD)
    tpl_vals = fwd.iloc[full].to_numpy() * 100.0
    assert len(engine_changes) == len(tpl_vals) > 0
    np.testing.assert_allclose(sorted(engine_changes), sorted(tpl_vals),
                               rtol=1e-9)

    # mean 一致性（模板真实输出未 round）：forward_return_{N}b.mean×100
    # ≈ 引擎完整窗口 changes 均值；std/min/max 顺带同比（零成本加强）
    stats = tpl_payload[f"forward_return_{N_FORWARD}b"]
    np.testing.assert_allclose(stats["mean"] * 100.0,
                               float(np.mean(engine_changes)), rtol=1e-9)
    np.testing.assert_allclose(stats["std"] * 100.0,
                               float(np.std(engine_changes, ddof=1)), rtol=1e-9)
    assert stats["min"] * 100.0 == pytest.approx(min(engine_changes), rel=1e-9)
    assert stats["max"] * 100.0 == pytest.approx(max(engine_changes), rel=1e-9)

    # 样本量口径：模板有效前向信号 == 完整窗口触发点数
    assert tpl_sample_size == len(full)
    return full, locs, tail


class TestCrossEngineParity:
    def test_close_condition_parity(self, parity_env):
        """close > 中位数阈值：逐点/均值/触发点集合/尾部数四线一致。"""
        df, _, _, _ = parity_env
        med = float(df["close"].median())
        cond = {"subject": "close", "action": "gt", "target": med}
        df_s, engine, tpl = _both_sides(parity_env, cond)

        assert engine.total_count > 0
        full, locs, tail = _compare(df_s, engine, tpl.payload, tpl.sample_size)

        # 触发点集合一致（尾部排除后；独立重算掩码，非两侧同源）
        expect = {l for l in range(len(df_s))
                  if df_s["close"].iloc[l] > med and l + N_FORWARD <= len(df_s) - 1}
        assert set(full) == expect
        assert tpl.payload["hit_count"] == int((df_s["close"] > med).sum())
        # 触发点全集一致（含尾部触发点；末根 loc == n-1 引擎不产出）
        assert set(locs) == {l for l in range(len(df_s))
                             if df_s["close"].iloc[l] > med and l < len(df_s) - 1}

    def test_ema_indicator_condition_parity(self, parity_env):
        """ema_50 > close：指标条件双侧同源（同一 ta.ema(period=50) 实现，
        引擎经 _DEFAULT_PARAMS 默认集、模板经 _compute_indicator）——
        触发点/逐点/均值一致证明指标列解析与数值无第二套。"""
        cond = {"subject": "ema_50", "action": "gt", "target": "close"}
        df_s, engine, tpl = _both_sides(parity_env, cond)

        assert engine.total_count > 0
        full, locs, tail = _compare(df_s, engine, tpl.payload, tpl.sample_size)
        # 引擎触发全集 == 模板命中全集 − 末根命中（末根被引擎 continue
        # 跳过不产出；末根命中数 = dropped − 引擎尾部数，∈ {0,1} 已锁定）
        assert engine.total_count == (tpl.payload["hit_count"]
                                      - (tpl.payload["dropped_tail_signals"]
                                         - len(tail)))
