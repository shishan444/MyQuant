"""组合条件求值内核测试（add-research-analysis-tools 0.4.1 任务 1.5）。

S1 mask_for_all：交集独立对照（手动 pandas 逐条求值后 &——禁调被测
实现生成期望）、内核自洽锚（mask_for_all([c]) ≡ mask_for(c) 逐位
一致）、高周期混合交集（相位灵敏构造——期望按 asof_completed 语义
人工推导）、空数组内部不变量拒绝。
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.condition_kernel import mask_for, mask_for_all


def _make_df(n: int = 300, seed: int = 42, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    return pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 1000, n),
    }, index=idx)


class TestMaskForAll:
    def test_intersection_matches_manual_and(self) -> None:
        """独立对照：手动 pandas 表达式逐条求值后按位 &（期望不来自
        被测实现）。"""
        df = _make_df(300, seed=7)
        med = float(df["close"].median())
        hi = med + 0.3 * (float(df["close"].max()) - med)
        conds = [
            {"subject": "close", "action": "gt", "target": med},
            {"subject": "close", "action": "lt", "target": hi},
            {"subject": "volume", "action": "gt", "target": 550.0},
        ]
        expected = ((df["close"] > med) & (df["close"] < hi)
                    & (df["volume"] > 550.0))
        got = mask_for_all(df, conds)
        pd.testing.assert_series_equal(
            got, expected.astype(bool), check_names=False)
        assert int(got.sum()) == int(expected.sum())

    @pytest.mark.parametrize("n", [2, 5], ids=["最小2条", "上限5条"])
    def test_two_and_five_condition_intersection(self, n: int) -> None:
        """最小组合与上限条数均正确求 AND（条数域校验归路由层——内核
        只验证求值）。"""
        df = _make_df(300, seed=13)
        thr = float(df["volume"].quantile(0.8))
        conds = [{"subject": "volume", "action": "gt", "target": thr}] * n
        expected = df["volume"] > thr
        got = mask_for_all(df, conds)
        pd.testing.assert_series_equal(
            got, expected.astype(bool), check_names=False)

    def test_single_condition_identity(self) -> None:
        """内核自洽锚：mask_for_all([c]) 与 mask_for(c) 同 work df
        逐位一致（单条 AND 数学恒等——键形制判据 D9 的求值侧依据）。"""
        df = _make_df(250, seed=11)
        c = {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"},
             "action": "gt", "target": 55}
        single = mask_for(df.copy(), c)
        wrapped = mask_for_all(df.copy(), [c])
        pd.testing.assert_series_equal(wrapped, single, check_names=False)
        assert int(wrapped.sum()) == int(single.sum())

    def test_structured_subject_mixed_intersection(self) -> None:
        """结构化 subject 条 × 字符串 subject 条混合交集（独立对照——
        RSI 手算 via 指标库单独现算后与基础列条件 &）。"""
        from core.features.indicators import _compute_indicator
        df = _make_df(300, seed=17)
        c_rsi = {"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"},
                 "action": "gt", "target": 50}
        c_close = {"subject": "close", "action": "gt",
                   "target": float(df["close"].median())}
        rsi = _compute_indicator(df, "RSI", {"period": 14})["rsi_14"]
        expected = (rsi > 50) & (df["close"] > df["close"].median())
        got = mask_for_all(df.copy(), [c_rsi, c_close])
        pd.testing.assert_series_equal(
            got, expected.fillna(False).astype(bool), check_names=False)

    def test_empty_conditions_internal_reject(self) -> None:
        """空数组到达内核=调用方接线缺陷——内部不变量 ValueError
        （非外部 422 语义；条数域校验归路由/校验层）。"""
        df = _make_df(50)
        with pytest.raises(ValueError, match="空 conditions"):
            mask_for_all(df, [])

    def test_higher_tf_mixed_intersection(self, tmp_path) -> None:
        """高周期混合交集（基准条 × tf 条）：期望按 asof_completed
        语义人工推导——True 4h bar（开盘 k、收盘 k+4h）投影到基准
        [k+4h, k+8h) 的 bar；构造唯一 True 高周期 bar（阈值=次大
        close → 恰一根越界），基准条恒 True——交集即投影窗自身。"""
        from core.analysis.condition_kernel import MtfEvalContext
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir()
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        base.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        high.to_parquet(data_dir / "BTCUSDT_4h.parquet")

        second = float(high["close"].nlargest(2).iloc[1])
        tf_cond = {"subject": "close", "action": "gt", "target": second,
                   "timeframe": "4h"}
        assert int((high["close"] > second).sum()) == 1  # 恰一根 True
        k = high.index[high["close"] > second][0]
        done = k + pd.Timedelta(hours=4)
        expected = pd.Series(
            (base.index >= done) & (base.index < done + pd.Timedelta(hours=4)),
            index=base.index)

        base_cond = {"subject": "close", "action": "gt", "target": 0.0}
        ctx = MtfEvalContext(data_dir, "BTCUSDT", "1h")
        got = mask_for_all(base.copy(), [base_cond, tf_cond], mtf=ctx)
        pd.testing.assert_series_equal(got, expected, check_names=False)
        assert int(got.sum()) == 4  # 投影窗恰覆盖 4 根 1h bar
