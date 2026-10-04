"""分布对比与相关扫描模板单测（2.2 + 2.3）。"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.correlation_scan import correlation_scan
from core.analysis.templates.distribution_compare import distribution_compare


def _make_df(n: int = 600, seed: int = 7, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    return pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 1000, n),
    }, index=idx)


RANGES = [{"start": "2024-01-01T00:00:00Z", "end": "2024-01-26T00:00:00Z"}]


class TestDistributionCompare:
    def _windows(self, df):
        mid = df.index[len(df) // 2]
        a = {"start": str(df.index[0]), "end": str(mid)}
        b = {"start": str(mid), "end": str(df.index[-1])}
        return a, b

    def test_same_distribution_not_significant(self) -> None:
        """同分布两半（iid 正态收益）→ 不显著。"""
        df = _make_df(600, seed=7)
        a, b = self._windows(df)
        res = distribution_compare(df, column="close", group_a=a, group_b=b,
                                   data_ranges=RANGES)
        assert res.confidence["ks_p_value"] > 0.05
        assert res.payload["significant"] is False

    def test_different_distribution_significant(self) -> None:
        """前半零漂移、后半强漂移 → 分布显著不同。"""
        n = 600
        rng = np.random.default_rng(3)
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC", name="timestamp")
        rets = np.concatenate([
            rng.normal(0, 100, n // 2),          # 无漂移
            rng.normal(300, 100, n // 2),        # 强上行漂移
        ])
        close = 60000 + np.cumsum(rets)
        df = pd.DataFrame({
            "open": close, "high": close + 50, "low": close - 50,
            "close": close, "volume": 1.0,
        }, index=idx)
        a, b = self._windows(df)
        res = distribution_compare(df, column="close", group_a=a, group_b=b,
                                   data_ranges=RANGES)
        assert res.payload["significant"] is True
        assert res.confidence["level"] < 0.05

    def test_missing_column_rejected(self) -> None:
        df = _make_df(100)
        a, b = self._windows(df)
        with pytest.raises(ResearchError) as ei:
            distribution_compare(df, column="nope", group_a=a, group_b=b,
                                 data_ranges=RANGES)
        assert ei.value.code == "RESEARCH_COLUMN_NOT_AVAILABLE"

    def test_empty_window_rejected(self) -> None:
        df = _make_df(100)
        a, b = self._windows(df)
        b["start"] = "2030-01-01"  # 空窗
        with pytest.raises(ValueError, match="区间无数据"):
            distribution_compare(df, column="close", group_a=a, group_b=b,
                                 data_ranges=RANGES)


class TestCorrelationScan:
    def test_peak_at_known_lag(self) -> None:
        """构造：信号驱动 3 bar 后的单 bar 收益 → 峰值应落在 lag=3。

        horizon=1 的干净构造：长前向窗口会让各 lag 的相关被窗口重叠
        污染，峰值不尖——此处只验证滞后峰值定位语义。
        """
        n = 800
        rng = np.random.default_rng(11)
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC", name="timestamp")
        sig = rng.normal(0, 1, n)
        # fwd(t)=ret(t+1)，ret 由 4 拍前的信号驱动 → fwd(t) ∝ sig(t-3) → 峰值 lag=3
        ret = np.roll(sig, 4) * 0.005 + rng.normal(0, 0.002, n)
        ret[:4] = 0.0
        close = 60000 * np.cumprod(1 + ret)

        df = pd.DataFrame({
            "open": close, "high": close * 1.001, "low": close * 0.999,
            "close": close, "volume": 1.0, "lead_signal": sig,
        }, index=idx)

        res = correlation_scan(df, signal_column="lead_signal",
                               horizon=1, max_lag=8, data_ranges=RANGES)
        assert res.payload["peak_lag"] == 3
        assert res.payload["peak_correlation"] > 0.8

    def test_missing_signal_column(self) -> None:
        df = _make_df(100)
        with pytest.raises(ResearchError) as ei:
            correlation_scan(df, signal_column="nope", data_ranges=RANGES)
        assert ei.value.code == "RESEARCH_COLUMN_NOT_AVAILABLE"

    def test_result_carries_three_elements(self) -> None:
        df = _make_df(300)
        df["vol_copy"] = df["volume"]
        res = correlation_scan(df, signal_column="volume", horizon=12, max_lag=4,
                               data_ranges=RANGES)
        assert res.sample_size >= 30
        assert "p_value" in res.confidence
        assert len(res.data_ranges) == 1


class TestGapClosureVariants:
    """阶段四盲区消解：transform 变体、滞后族容错、配对不足拒。"""

    @staticmethod
    def _win(df):
        mid = df.index[len(df) // 2]
        a = {"start": str(df.index[0]), "end": str(mid)}
        b = {"start": str(mid), "end": str(df.index[-1])}
        return a, b

    def test_log_returns变体(self) -> None:
        df = _make_df(600, seed=7)
        a, b = self._win(df)
        res = distribution_compare(df, column="close", group_a=a, group_b=b,
                                   data_ranges=RANGES, transform="log_returns")
        assert res.payload["significant"] is False

    def test_原始值变体_else分支(self) -> None:
        df = _make_df(600, seed=7)
        a, b = self._win(df)
        res = distribution_compare(df, column="close", group_a=a, group_b=b,
                                   data_ranges=RANGES, transform="raw")
        # 原始值形制：随机游走两半水平差异显著（与 returns 形成
        # 行为对照——变体分支的语义证据）
        assert res.confidence["ks_p_value"] < 0.05
        assert res.payload["significant"] is True

    def test_小样本双窗_拒绝(self) -> None:
        df = _make_df(600, seed=7)
        a, b = self._win(df)
        # 0.3.2 卡4 分流：小样本属业务拒绝 422（RESEARCH_PARAM_INVALID，
        # detail 披露组样本量）——原裸 ValueError 锚随缺陷裁定改写
        with pytest.raises(ResearchError, match="小样本") as ei:
            distribution_compare(df, column="close", group_a=a, group_b=b,
                                 data_ranges=RANGES, min_samples=590)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["group_a_n"] == 299
        assert ei.value.detail["group_b_n"] == 299
        assert ei.value.detail["min_samples"] == 590

    def test_相关滞后族_短样本与NaN跳过(self) -> None:
        """max_lag 大于可用样本差 → 多数 lag 不足 min_samples 跳过，
        仍有合法 lag 产出；全族不足 → 422（CARD-10 收编业务拒绝）。"""
        df = _make_df(300, seed=7)
        res = correlation_scan(df, signal_column="close", data_ranges=RANGES,
                               horizon=4, max_lag=200, min_samples=50)
        assert "peak_lag" in res.payload  # 合法 lag 族产出
        # 全族不足：样本需求超出数据量
        from core.analysis.hypothesis_service import ResearchError
        with pytest.raises(ResearchError, match="可用配对不足") as ei:
            correlation_scan(df, signal_column="close", data_ranges=RANGES,
                             horizon=4, max_lag=4, min_samples=299)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["min_samples"] == 299
