"""事件研究与模式挖掘模板单测（2.4 + 2.5）。"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.templates.event_study import event_study
from core.analysis.templates.pattern_mining import similar_at_time


def _make_df(n: int = 400, seed: int = 5, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    return pd.DataFrame({
        "open": close, "high": close + 60, "low": close - 60,
        "close": close, "volume": rng.uniform(100, 1000, n),
        "ema_20": pd.Series(close).ewm(span=20).mean().values,
        "rsi_14": pd.Series(close).rolling(14).apply(
            lambda x: 50 + (x.iloc[-1] - x.mean()) / (x.std() + 1e-9) * 10).values,
    }, index=idx)


RANGES = [{"start": "2024-01-01T00:00:00Z", "end": "2024-01-17T00:00:00Z"}]


class TestEventStudy:
    def test_window_alignment_and_stats(self) -> None:
        """已知构造核对窗口对齐：事件后 k bar 价格为 close[e]*(1+0.005*k)。"""
        n = 300
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC", name="timestamp")
        marker = np.zeros(n)
        close = np.full(n, 100.0)
        events = list(range(10, n - 20, 10))
        for e in events:
            marker[e] = 1
            for k in range(1, 4):
                close[e + k] = close[e] * (1 + 0.005 * k)
        df = pd.DataFrame({
            "open": close, "high": close, "low": close,
            "close": close, "volume": 1.0, "marker": marker,
        }, index=idx)

        res = event_study(df, condition={"subject": "marker", "action": "gt", "target": 0},
                          window=3, data_ranges=RANGES, min_events=5)
        assert res.payload["path"]["mean"] == pytest.approx([0.005, 0.010, 0.015], rel=1e-6)
        assert res.payload["n_events"] == len(events)
        assert res.payload["terminal_return"]["mean"] == pytest.approx(0.015, rel=1e-6)

    def test_insufficient_events_rejected(self) -> None:
        df = _make_df(200)
        # 罕见条件：close > 极大值 → 0 事件
        with pytest.raises(ValueError, match="事件数"):
            event_study(df, condition={"subject": "close", "action": "gt",
                                       "target": 1e12}, window=5, data_ranges=RANGES)

    def test_three_elements(self) -> None:
        df = _make_df(400)
        res = event_study(df, condition={"subject": "close", "action": "gt",
                                         "target": float(df["close"].median())},
                          window=8, data_ranges=RANGES, min_events=10)
        assert res.sample_size >= 10
        assert "p_value" in res.confidence
        assert res.payload["window"] == 8


class TestSimilarAtTime:
    def test_matches_manual_engine_call(self) -> None:
        """与手工引擎路径逐项一致（包装不改引擎语义）。"""
        from core.discovery.knn_engine import SimilarCaseEngine

        df = _make_df(300)
        as_of = str(df.index[-60])

        res = similar_at_time(df, as_of=as_of, n_neighbors=30, horizon=12,
                              data_ranges=RANGES)

        # 手工重放
        as_of_ts = pd.Timestamp(as_of)
        hist = df[df.index <= as_of_ts]
        engine = SimilarCaseEngine(n_neighbors=30, horizon=12).fit(hist)
        feats = engine.encoder.transform(hist.iloc[[-1]]).reshape(-1)
        manual = engine.find_similar(feats, n_neighbors=30)
        manual_pred = engine.predict(feats)

        assert res.payload["n_neighbors"] == len(manual)
        assert res.payload["neighbors"][0]["timestamp"] == manual[0].timestamp
        assert res.payload["prediction"]["avg_return"] == pytest.approx(manual_pred.avg_return)

    def test_no_lookahead_in_neighbors(self) -> None:
        """邻域时刻必须严格早于 as_of（模板层防前视）。"""
        df = _make_df(300)
        as_of = str(df.index[-60])
        res = similar_at_time(df, as_of=as_of, n_neighbors=30, data_ranges=RANGES)
        assert res.payload["neighbor_time_max"] is not None
        assert pd.Timestamp(res.payload["neighbor_time_max"]) <= pd.Timestamp(as_of)
        assert pd.Timestamp(res.payload["as_of"]) == pd.Timestamp(as_of)

    def test_as_of_not_in_data_rejected(self) -> None:
        df = _make_df(100)
        with pytest.raises(ValueError, match="as_of 时点无数据"):
            similar_at_time(df, as_of="2030-01-01", data_ranges=RANGES)

    def test_insufficient_history_rejected(self) -> None:
        df = _make_df(50)
        with pytest.raises(ValueError, match="历史不足"):
            similar_at_time(df, as_of=str(df.index[-5]), n_neighbors=50,
                            horizon=12, data_ranges=RANGES)
