"""条件统计模板与结果构造器单测（2.1 + 2.6；
fix-condition-stats-forward-return 前向收益语义）。"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.result import build_result
from core.analysis.templates import condition_stats


def _make_df(n: int = 300, seed: int = 42, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    return pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 1000, n),
    }, index=idx)

RANGES = [{"start": "2024-01-01T00:00:00Z", "end": "2024-01-14T00:00:00Z"}]

def _gt_median_kwargs(df: pd.DataFrame, period: int = 10) -> dict:
    med = df["close"].median()
    return dict(
        indicator="EMA", params={"period": period},
        condition={"subject": "close", "action": "gt", "target": float(med)},
        data_ranges=RANGES)

class TestBuildResultThreeElements:
    def test_valid_result_constructs(self) -> None:
        r = build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=RANGES)
        assert r.sample_size == 100

    def test_missing_confidence_rejected(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            build_result("t", sample_size=100, confidence=None, data_ranges=RANGES)

    def test_confidence_without_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="interval"):
            build_result("t", sample_size=100,
                         confidence={"method": "m"}, data_ranges=RANGES)

    def test_missing_ranges_rejected(self) -> None:
        with pytest.raises(ValueError, match="data_ranges"):
            build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=None)

    def test_malformed_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="半开区间"):
            build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=[{"start": "2024-01-01"}])

    def test_small_sample_rejected(self) -> None:
        # CARD-10 收编：小样本属业务拒绝 422（原裸 ValueError→500）
        from core.analysis.hypothesis_service import ResearchError
        with pytest.raises(ResearchError, match="小样本") as ei:
            build_result("t", sample_size=10,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=RANGES)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["sample_size"] == 10
        assert ei.value.detail["min_samples"] == 30

class TestConditionStats:
    def test_known_hit_rate(self) -> None:
        """已知数据核对统计量：close > 全程中位数的占比（request_start=None
        → 统计段=全 df，向后兼容口径）。sample_size 口径为有效前向信号数：
        命中中位于尾部 forward_bars=24 根内的（前向窗不完整）计入
        dropped_tail_signals，不进 sample_size。"""
        df = _make_df(300)
        kw = _gt_median_kwargs(df)
        hit = (df["close"] > df["close"].median()).values

        res = condition_stats(df, **kw)

        expected_valid = int(hit[:-24].sum())  # fwd[i] 非空 ⇔ i <= 275
        assert res.payload["hit_rate"] == pytest.approx(float(hit.mean()), abs=1e-12)
        assert res.payload["hit_count"] == int(hit.sum())
        assert res.sample_size == expected_valid
        assert res.payload["dropped_tail_signals"] == int(hit.sum()) - expected_valid
        assert res.confidence["method"] == "wilson_interval"
        assert len(res.confidence["interval"]) == 2
        assert res.confidence["forward_significance"]["n_total_signals"] == expected_valid
        assert res.confidence["n_total"] == 300

    def test_indicator_column_as_subject(self) -> None:
        """subject 引用指标输出列（ema_10 vs close 的交叉）。"""
        df = _make_df(200)
        res = condition_stats(
            df, indicator="EMA", params={"period": 10},
            condition={"subject": "close", "action": "cross_above", "target": "ema_10"},
            data_ranges=RANGES, min_samples=5)
        assert res.payload["hit_count"] >= 0  # 交叉事件天然稀疏，不假设次数
        assert res.payload["condition"]["subject"] == "close"

    def test_unknown_indicator_rejected(self) -> None:
        df = _make_df(100)
        with pytest.raises(ResearchError) as ei:
            condition_stats(
                df, indicator="NOPE", params={},
                condition={"subject": "close", "action": "gt", "target": 1},
                data_ranges=RANGES)
        assert ei.value.code == "RESEARCH_INDICATOR_UNKNOWN"

    def test_small_sample_rejected(self) -> None:
        # 口径变化（前向收益语义）：小样本拒绝现作用于有效前向信号数——
        # 20 bar 全命中、forward_bars=5 → 尾部 5 根丢弃 → 15 有效 < 30。
        # （不显式传 forward_bars 时默认 24 > len-1=19，会先触发
        # RESEARCH_PARAM_INVALID，由 test_forward_bars_invalid_rejected 覆盖。）
        df = _make_df(20)
        # CARD-10 收编：模板级同款 422（detail 披露有效信号数口径）
        from core.analysis.hypothesis_service import ResearchError
        with pytest.raises(ResearchError, match="小样本") as ei:
            condition_stats(
                df, indicator="EMA", params={"period": 5},
                condition={"subject": "close", "action": "gt", "target": 0},
                data_ranges=RANGES, forward_bars=5)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["min_samples"] == 30

    def test_forward_return_known_values(self) -> None:
        """已知序列核对前向收益分布（close(t)→close(t+2)）、win_rate
        与 baseline 均值——数值全手工推导。"""
        close = [100, 110, 120, 110, 100, 90, 80, 90, 100, 110]
        idx = pd.date_range("2024-01-01", periods=10, freq="1h",
                            tz="UTC", name="timestamp")
        df = pd.DataFrame({
            "open": close, "high": [c + 1 for c in close],
            "low": [c - 1 for c in close], "close": close,
            "volume": [100.0] * 10,
        }, index=idx)
        # median=100；命中位置（close>100）：1,2,3,9；fwd2 尾 2 根 NaN → 9 丢弃
        # period=5：EMA 值域内（CARD-20 前置校验接入后，原 period=3
        # 低于 min=5 被正确拦截——断言只依赖 close 序列，值域内任意
        # period 零影响）
        res = condition_stats(
            df, indicator="EMA", params={"period": 5},
            condition={"subject": "close", "action": "gt", "target": 100.0},
            data_ranges=RANGES, forward_bars=2, min_samples=3)

        assert res.payload["dropped_tail_signals"] == 1
        expected_valid = [110 / 110 - 1, 100 / 120 - 1, 90 / 110 - 1]
        fwd_stats = res.payload["forward_return_2b"]
        assert fwd_stats["count"] == 3
        assert fwd_stats["mean"] == pytest.approx(float(np.mean(expected_valid)))
        assert fwd_stats["median"] == pytest.approx(float(np.median(expected_valid)))
        assert res.payload["win_rate"] == pytest.approx(0.0)  # 3 个值均 <= 0
        expected_baseline = float(np.mean(
            [120 / 100 - 1, 110 / 110 - 1, 100 / 120 - 1, 90 / 110 - 1,
             80 / 100 - 1, 90 / 90 - 1, 100 / 80 - 1, 110 / 90 - 1]))
        assert res.payload["baseline_2b"]["mean"] == pytest.approx(expected_baseline)

    def test_forward_bars_invalid_rejected(self) -> None:
        """forward_bars 非正整数/bool/浮点/字符串/超数据长度 →
        结构化拒绝（附参数名与合法范围）。"""
        df = _make_df(100)
        for bad in (0, -1, True, False, 1.5, "24", 100, 10_000):
            with pytest.raises(ResearchError) as ei:
                condition_stats(df, forward_bars=bad,
                                indicator="EMA", params={"period": 5},
                                condition={"subject": "close", "action": "gt",
                                           "target": 0},
                                data_ranges=RANGES)
            assert ei.value.code == "RESEARCH_PARAM_INVALID"
            assert ei.value.detail == {"param": "forward_bars",
                                       "legal_range": "1..99"}

    def test_dropped_tail_signals_count(self) -> None:
        """dropped_tail_signals = 命中且落在尾部 N 根（前向窗不完整）数。"""
        df = _make_df(200)
        kw = _gt_median_kwargs(df)
        hit = (df["close"] > df["close"].median()).values

        res = condition_stats(df, **kw)

        expected_dropped = int(hit[-24:].sum())
        assert res.payload["dropped_tail_signals"] == expected_dropped
        assert res.payload["hit_count"] == int(hit.sum())
        assert res.sample_size == int(hit.sum()) - expected_dropped

    def test_request_start_prefix_mode(self) -> None:
        """warmup 前缀模式：前缀只用不算——统计段从 request_start 起，
        warmup_bars/total_bars 声明前缀，hit 与 sample_size 只计统计段。"""
        df = _make_df(350)
        kw = _gt_median_kwargs(df)
        hit = (df["close"] > df["close"].median()).values

        # min_samples 放宽：本用例焦点是前缀切分语义，随机数据下统计段
        # 有效前向信号约 24，不必与阈值纠缠（阈值语义另有用例覆盖）
        res = condition_stats(df, request_start=df.index[200],
                              min_samples=10, **kw)

        stat_hit = hit[200:]
        assert res.payload["warmup_bars"] == 200
        assert res.payload["total_bars"] == 350  # 全 df（含前缀）
        assert res.payload["hit_count"] == int(stat_hit.sum())  # 前缀命中不计
        assert res.sample_size == int(stat_hit[:-24].sum())
        assert res.confidence["n_total"] == 150  # hit_rate 分母 = 统计段 bar 数
        # 前缀上的当根收益 / close 统计不混入
        assert res.payload["same_bar_return_stats"]["count"] == int(stat_hit.sum())
        assert res.payload["close_stats_when_hit"]["count"] == int(stat_hit.sum())

    def test_same_bar_return_stats_renamed(self) -> None:
        """当根收益字段改名：same_bar_return_stats 存在且承载原口径值
        （统计段命中 bar 的 close.pct_change()）；旧名必须不存在。"""
        df = _make_df(300)
        kw = _gt_median_kwargs(df)
        hit = df["close"] > df["close"].median()

        res = condition_stats(df, **kw)

        assert "same_bar_return_stats" in res.payload
        assert "return_stats_when_hit" not in res.payload
        ret = df["close"].pct_change()
        s = ret[hit]
        assert res.payload["same_bar_return_stats"]["count"] == int(hit.sum())
        assert res.payload["same_bar_return_stats"]["mean"] == pytest.approx(
            float(np.nanmean(s)))

    def test_overlapping_signals_dedup_for_p_value(self) -> None:
        """密集命中去重叠：p 值样本仅为间隔 >= forward_bars 的子集
        （n_overlapped < n_total_signals），两组样本量分别声明。"""
        df = _make_df(120)
        res = condition_stats(
            df, indicator="EMA", params={"period": 5},
            condition={"subject": "close", "action": "gt", "target": 0},
            data_ranges=RANGES, forward_bars=10)  # 120 bar 全命中

        sig = res.confidence["forward_significance"]
        # 命中位置 0..119；尾部 10 根丢弃 → 有效全样本 110
        assert sig["n_total_signals"] == 110
        # 贪心间隔 >= 10：0,10,...,110 共 12 个；其中 110 号在尾部（NaN）
        # dropna 后 11 个 → 间隔 >= forward_bars 由 11 = floor(109/10)+1 互证
        assert sig["n_overlapped"] == 11
        assert sig["n_overlapped"] < sig["n_total_signals"]
        assert sig["method"] == "t_test_vs_baseline"
        assert sig["p_value"] is not None and 0.0 <= sig["p_value"] <= 1.0
        assert res.payload["dropped_tail_signals"] == 10


class TestTimeframeKey:
    """0.2.14 timeframe 键透传：condition 携带高周期键经 _mask_for
    掩码投影求值（spec research-validation-protocol「混合周期假设条件
    求值端到端成功」的条件统计族模板面）。"""

    def test_高周期键条件_掩码投影轴分离生效(self, tmp_path):
        """阈值超出 4h close 全域：带键（4h 轴）hit_count=0；同阈值
        去键（1h 轴，固定种子存在越界 bar）hit_count>0。"""
        from core.analysis.condition_kernel import MtfEvalContext
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir()
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        base.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        high.to_parquet(data_dir / "BTCUSDT_4h.parquet")
        ctx = MtfEvalContext(data_dir, "BTCUSDT", "1h")
        beyond = float(high["close"].max()) + 1.0

        def hit(cond):
            return condition_stats(
                base.copy(), indicator="EMA", params={"period": 10},
                condition=cond, data_ranges=RANGES, forward_bars=8,
                mtf=ctx).to_dict()["payload"]["hit_count"]

        # 带键：4h 轴全 False → 0 命中 → 小样本拒绝（投影路径生效；
        # CARD-10 收编后为 422，sample_size=0 经公共层 detail 披露）
        from core.analysis.hypothesis_service import ResearchError
        with pytest.raises(ResearchError, match="小样本") as ei:
            hit({"subject": "close", "action": "gt",
                 "target": beyond, "timeframe": "4h"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["sample_size"] == 0
        # 同阈值去键：1h 轴存在越界 bar → 命中 >0（两轴分离）
        assert hit({"subject": "close", "action": "gt",
                    "target": beyond}) > 0



class TestParamsPrecheck:
    """CARD-20：坏 params 前置校验——422 结构化拒绝（原模板内
    KeyError→路由快路径吞成 failed 任务）。共享校验权威
    indicator_query.validate_params（读数通道 D7 同款）。"""

    def test_正常参数_放行(self) -> None:
        df = _make_df(300)
        kw = _gt_median_kwargs(df)
        res = condition_stats(df, **kw)
        assert res.payload["hit_count"] >= 0  # 正常求值返回（构造即放行证）

    @pytest.mark.parametrize(
        ("params", "kind"),
        [
            ({"peroid": 14}, "拼错键"),
            ({"period": 99999}, "越界值"),
            ({"period": "abc"}, "坏类型"),
            ({"period": 3}, "EMA 低于值域下限 5"),
        ],
        ids=["拼错键", "越界值", "坏类型", "低于值域下限"])
    def test_坏参数_422结构化拒绝(self, params, kind) -> None:
        df = _make_df(300)
        kw = _gt_median_kwargs(df)
        with pytest.raises(ResearchError) as ei:
            condition_stats(df, **dict(kw, params=params))
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.status == 422
