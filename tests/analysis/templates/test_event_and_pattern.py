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


class TestEventStudyTimeframeKey:
    """0.2.14 第七求值点收口：event_study 条件携带 timeframe 键经
    _mask_for 掩码投影；无 ctx 带键=显式接线错误拒。"""

    @staticmethod
    def _mtf_env(tmp_path):
        from core.analysis.condition_kernel import MtfEvalContext
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir(parents=True, exist_ok=True)
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        base.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        high.to_parquet(data_dir / "BTCUSDT_4h.parquet")
        return base, high, MtfEvalContext(data_dir, "BTCUSDT", "1h")

    def test_高周期键条件_掩码投影求值成功(self, tmp_path):
        """阈值取 4h close 最小值-1 → 4h 轴全 True；投影后首窗（首根
        4h 收盘前）False——1h 600 根中前 4 根 False，尾部丢窗=8，
        手算 n_events = 596 - 8 = 588。"""
        base, high, ctx = self._mtf_env(tmp_path)
        cond = {"subject": "close", "action": "gt",
                "target": float(high["close"].min()) - 1.0,
                "timeframe": "4h"}
        res = event_study(base, condition=cond, window=8,
                          data_ranges=RANGES, min_events=10, mtf=ctx)
        assert res.payload["n_events"] == 588

    def test_无ctx带高周期键_显式接线错误拒(self, tmp_path):
        from core.analysis.hypothesis_service import ResearchError

        base, _, _ = self._mtf_env(tmp_path)
        with pytest.raises(ResearchError) as ei:
            event_study(base, condition={"subject": "close", "action": "gt",
                                         "target": 0.0, "timeframe": "4h"},
                        window=8, data_ranges=RANGES)
        assert ei.value.code == "RESEARCH_MTF_CONTEXT_MISSING"


class TestAsOfTimezoneEquivalence:
    """CARD-06 半变体统一等价锚：aware 非 UTC as_of 与 UTC 形输入
    输出一致（to_utc 统一后 aware 显式转 UTC 再比较——pandas 按绝对
    时刻比较，语义等价；统一前 aware 非 UTC 走"不转直接比较"路径）。"""

    def test_aware非UTC_as_of_与UTC形_逐字段一致(self) -> None:
        df = _make_df(300)
        utc = str(df.index[-60])
        # 同一绝对时刻的 +08:00 表达（aware 非 UTC）
        aware = (pd.Timestamp(utc).tz_convert("Asia/Shanghai")
                 .isoformat())

        r_utc = similar_at_time(df, as_of=utc, n_neighbors=30,
                                horizon=12, data_ranges=RANGES)
        r_aware = similar_at_time(df, as_of=aware, n_neighbors=30,
                                  horizon=12, data_ranges=RANGES)

        assert r_aware.payload == r_utc.payload
