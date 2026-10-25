"""T⑦ within_bars_after 语义差锚族（fix-mtf-execution-consistency 组 3）。

执行链语义（T3.1 面）：
- ⑦.1 当根排除：同参数 vs lookback_any 输出差恰为当根（人工构造
  序列手算对照——内层事件仅当根成立时 within False 而 lookback True）；
- ⑦.2 轴内开窗：4h 标注 N=3 基准 30m——N 按 4h 轴根数独立直算对照
  （投影后不放大）；
- ⑦.5 边界（执行链子集）：window 值域三态（1 合法/越界拒/坏值拒）。

登记面全链（⑦.3 嵌套拒绝/⑦.4 登记面可达）与词表基数（T⑥）归
T3.2 落地后补齐（tests/analysis/ 侧）。
"""
import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.errors import ResearchError
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA,
)
from core.strategy.executor import (
    EVALUABLE_CONDITION_TYPES, dna_to_signal_set, evaluate_condition,
)


def _gene(cond: dict, tf: str | None = None) -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi", cond, timeframe=tf)


def _df(n: int = 300, seed: int = 51) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    rets = rng.normal(0.0003, 0.01, n)
    close = 60000.0 * np.cumprod(1.0 + rets)
    open_ = np.empty(n)
    open_[0] = 60000.0
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, 0.005, n)) * close
    idx = pd.date_range("2024-01-01", periods=n, freq="30min", tz="UTC")
    return pd.DataFrame({"open": open_,
                         "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread,
                         "close": close,
                         "volume": np.abs(rng.normal(500, 150, n)) + 1.0},
                        index=idx)


class TestT7WithinBarsAfterSemantics:
    def test_t7_1_current_bar_exclusion(self):
        """当根排除：同参数 within_bars_after vs lookback_any 输出差
        恰为当根（内层事件当根首次成立的位置，within False 而
        lookback True；此后 N 根窗内两者同 True）。"""
        n = 200
        rsi = pd.Series(np.linspace(70, 10, n))  # 单调下行穿越 30
        close = pd.Series(np.full(n, 100.0))
        cond_inner = {"window": 5,
                      "inner": {"type": "lt", "threshold": 30}}
        within = evaluate_condition(
            rsi, close, {"type": "within_bars_after", **cond_inner})
        lookback = evaluate_condition(
            rsi, close, {"type": "lookback_any", **cond_inner})
        # 手算对照：lt(30) 首次 True 的位置 p（rsi 单调下行恰一处）
        inner_true = (rsi < 30)
        p = int(np.flatnonzero(inner_true.to_numpy())[0])
        assert within.iloc[p] is False or within.iloc[p] == False  # noqa: E712
        assert lookback.iloc[p] == True  # noqa: E712
        # 差集恰为当根 p（后续窗内两者同 True；p 前同 False）
        diff = (within != lookback)
        assert list(np.flatnonzero(diff.to_numpy())) == [p], (
            "两动作输出差必须恰为当根排除位")

    def test_t7_2_high_tf_axis_window_not_inflated(self):
        """轴内开窗：4h 标注 N=3 基准 30m——N 按 4h 轴根数求值（投影
        后不放大为 30m 根数）。对照=同基因在 4h 轴独立直算投影。"""
        from core.data.mtf_alignment import project_completed_bool
        base = _df(3000)
        hi = pd.DataFrame({
            "open": base["open"].resample("4h").first(),
            "high": base["high"].resample("4h").max(),
            "low": base["low"].resample("4h").min(),
            "close": base["close"].resample("4h").last(),
            "volume": base["volume"].resample("4h").sum(),
        }).dropna()

        cond = {"type": "within_bars_after", "window": 3,
                "inner": {"type": "lt", "threshold": 45}}
        gene = _gene(cond, tf="4h")
        dna = StrategyDNA(
            signal_genes=[gene],
            execution_genes=ExecutionGenes(timeframe="30m",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(),
            signal_sources=[{"type": "rule"}])
        sig = dna_to_signal_set(dna, base, {"30m": base, "4h": hi})

        # 独立直算：4h 轴 RSI 求值→within 语义手算→投影 30m
        from core.features.indicators import _compute_indicator
        hi2 = hi.copy()
        for col, series in _compute_indicator(hi2, "RSI",
                                              {"period": 14}).items():
            hi2[col] = series
        inner = (hi2["rsi_14"] < 45).astype(float)
        direct = (inner.shift(1).rolling(window=3, min_periods=1).sum()
                  > 0).fillna(False)
        projected = project_completed_bool(
            direct.astype(bool), base.index, "4h")

        pd.testing.assert_series_equal(
            sig.entries.fillna(False).astype(bool),
            projected.fillna(False).astype(bool),
            check_names=False, check_freq=False)
        assert int(projected.sum()) > 0, "对照侧应有触发"

    def test_t7_5_window_value_domain(self):
        """window 值域：1 合法；0/负数/坏值结构化拒绝（沿 _cond_window
        先例域）。"""
        rsi = pd.Series(np.linspace(70, 10, 50))
        close = pd.Series(np.full(50, 100.0))
        ok = evaluate_condition(
            rsi, close, {"type": "within_bars_after", "window": 1,
                         "inner": {"type": "lt", "threshold": 30}})
        assert len(ok) == 50
        for bad in (0, -3, "x", 2.5, True):
            with pytest.raises(ResearchError) as exc:
                evaluate_condition(
                    rsi, close, {"type": "within_bars_after",
                                 "window": bad,
                                 "inner": {"type": "lt",
                                           "threshold": 30}})
            assert exc.value.code == "RESEARCH_PARAM_INVALID"

    def test_vocab_includes_within_bars_after(self):
        """词表收录（基数 17 详锚在 T3.2/S6b 更新——此处类型在场断言）。"""
        assert "within_bars_after" in EVALUABLE_CONDITION_TYPES
