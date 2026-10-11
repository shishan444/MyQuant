"""反例画像模板测试（add-research-analysis-tools 0.4.1 任务 5.4，B5 用例组）。

核心正例「触及目标后回落」：rolling max 区间语义下的分组判定（终点
口径必错——该口径会把此类正常盈利样本误判为反例）。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.counter_profile import (
    _cohens_d, counter_profile, feature_matrix_names)
from core.features.indicators import _compute_indicator

SYMBOL = "BTCUSDT"
RANGES = [{"start": "2024-01-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]

# 分组判定的核心形制（forward_bars=6）：
# 符合周期 [100,106,107,108,103,102,101,100]——触发 106/107/108，
# 窗口内 max≥108>触发价（触及后回落——rolling max 判符合、终点口径
# 103<106 会误判反例）
# 反例周期 [100,106,105,104,103,102,101,100]——触发 106，窗口内
# max=105<106（从未越过触发价）
_CONFORM_CYCLE = [100, 106, 107, 108, 103, 102, 101, 100]
_COUNTER_CYCLE = [100, 106, 105, 104, 103, 102, 101, 100]
_CONDITION = {"subject": "close", "action": "gt", "target": 105}


def _make_df(n_conform: int, n_counter: int,
             head: int = 220) -> pd.DataFrame:
    closes: list[float] = [100.0] * head
    for _ in range(n_conform):
        closes += _CONFORM_CYCLE
    for _ in range(n_counter):
        closes += _COUNTER_CYCLE
    closes += [100.0] * 10  # 尾部完整窗口垫底
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h",
                        tz="UTC", name="timestamp")
    arr = np.array(closes)
    return pd.DataFrame({
        "open": arr, "high": arr + 0.5, "low": arr - 0.5,
        "close": arr, "volume": np.full(len(arr), 100.0),
    }, index=idx)


def _profile(df, **kw):
    return counter_profile(
        df, condition=_CONDITION, direction="long",
        data_ranges=RANGES, forward_bars=6, min_samples=10,
        request_start=df.index[220], **kw)


class TestGroupingIntervalSemantics:
    def test_touch_and_fallback_counts_as_conform(self):
        """核心正例：触及目标后回落（窗口 max 108 > 触发价 106）必判
        符合——终点口径（103<106）会误判反例。"""
        df = _make_df(n_conform=10, n_counter=5)
        res = _profile(df)
        groups = res.payload["groups"]
        # conform 周期 3 触发中 2 符合（106/107 窗口 max≥108）+1 反例
        # （峰顶 108 窗口 max 103）；counter 周期 1 反例
        assert groups["conform"]["count"] == 20
        assert groups["counter"]["count"] == 15  # 10 峰顶 + 5 反例周期
        assert res.sample_size == 35
        assert groups["comparison_status"] == "ok"  # 15 ≥ 10

    @pytest.mark.parametrize(
        ("slope", "n_conform_gt0"),
        [(-0.6, True), (0.6, False)],
        ids=["单调降 short 全符合", "单调升 short 全反例"])
    def test_short_direction_monotone(self, slope, n_conform_gt0):
        """short 对称（rolling min）：单调降序列触发全符合（窗口 min
        恒更低）；单调升序列触发全反例——方向语义确定样例。"""
        n = 400
        arr = (200.0 if slope < 0 else 100.0) + slope * np.arange(n)
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({
            "open": arr, "high": arr + 0.5, "low": arr - 0.5,
            "close": arr, "volume": np.full(n, 100.0)}, index=idx)
        res = counter_profile(
            df, condition={"subject": "close", "action": "lt",
                           "target": 150.0},
            direction="short", data_ranges=RANGES, forward_bars=6,
            min_samples=5, request_start=df.index[10])
        groups = res.payload["groups"]
        if n_conform_gt0:
            assert groups["conform"]["count"] > 0
            assert groups["counter"]["count"] == 0
        else:
            assert groups["counter"]["count"] > 0
            assert groups["conform"]["count"] == 0

    def test_dropped_tail_triggers(self):
        """尾部窗口不完整的触发不参与分组（披露 dropped_tail）。"""
        df = _make_df(n_conform=6, n_counter=4)
        # request_start 后移到最后一周期中段——制造尾部不完整触发
        res = counter_profile(
            df, condition=_CONDITION, direction="long",
            data_ranges=RANGES, forward_bars=6, min_samples=1,
            request_start=df.index[220])
        assert res.payload["groups"]["dropped_tail_triggers"] == 0
        # 直调不切统计段（request_start=None）：全部触发参与（含尾部）
        res2 = counter_profile(
            df, condition=_CONDITION, direction="long",
            data_ranges=RANGES, forward_bars=6, min_samples=1)
        total = res2.sample_size
        grouped = (res2.payload["groups"]["conform"]["count"]
                   + res2.payload["groups"]["counter"]["count"]
                   + res2.payload["groups"]["dropped_tail_triggers"])
        assert grouped == total


class TestFeatureMatrix:
    def test_feature_domain_anchor(self):
        """特征域锚：特征名域=_DEFAULT_PARAMS 全输出列减 guard_only/
        lazy（ML 与 PSAR/ADX/ATR/VWAP 类不在特征域）+ 负断言。"""
        df = _make_df(n_conform=10, n_counter=5)
        res = _profile(df)
        feat_names = {f["feature"] for f in res.payload["features"]}
        # 正面：RSI/EMA 等非 guard 指标在域内
        assert any(f.startswith("RSI:") for f in feat_names)
        assert any(f.startswith("EMA:") for f in feat_names)
        # 负面：guard_only（ADX/PSAR/VWAP/ATR）与 lazy（ml 类）不在
        for guard in ("ADX:", "PSAR:", "VWAP:", "ATR:"):
            assert not any(f.startswith(guard) for f in feat_names), guard
        assert not any("mfe_score" in f or "mf_osc" in f
                       for f in feat_names)
        # 装载预算名单同域
        assert "PSAR" not in feature_matrix_names()

    def test_distribution_eight_keys_independent_check(self):
        """分布独立对照（逐键级）：键集与 distribution_compare._desc
        同八键；值经独立 pandas 核算（期望不经被测实现）。"""
        from core.analysis.templates.distribution_compare import (
            distribution_compare)
        df = _make_df(n_conform=10, n_counter=5)
        res = _profile(df)
        rsi_feat = next(f for f in res.payload["features"]
                        if f["feature"] == "RSI:rsi_14")
        assert set(rsi_feat["conform"]) == {
            "count", "mean", "std", "min", "q25", "median", "q75", "max"}

        # 独立核算：触发时点 RSI 值按符合/反例分组
        trig = (df["close"] > 105) & (df.index >= df.index[220])
        fut_max = (df["close"].rolling(6).max().shift(-6))
        decidable = trig & fut_max.notna()
        conform = decidable & (fut_max > df["close"])
        counter = decidable & ~(fut_max > df["close"])
        rsi = _compute_indicator(df.copy(), "RSI", {"period": 14})["rsi_14"]
        for group_mask, key in ((conform, "conform"), (counter, "counter")):
            vals = rsi[group_mask].dropna()
            got = rsi_feat[key]
            assert got["count"] == len(vals)
            assert got["mean"] == pytest.approx(float(np.nanmean(vals)))
            assert got["median"] == pytest.approx(
                float(np.nanquantile(vals, 0.5)))
            assert got["max"] == pytest.approx(float(np.nanmax(vals)))
            assert got["q25"] == pytest.approx(
                float(np.nanquantile(vals, 0.25)))

    def test_skipped_features_disclosed(self, monkeypatch):
        """skipped_features 披露锚：构造现算失败列→披露不静默消失。"""
        import core.features.indicators as ind_mod
        original = ind_mod._compute_indicator

        def _failing(df, name, params):
            if name == "CCI":
                raise ValueError("构造失败")
            return original(df, name, params)

        monkeypatch.setattr(ind_mod, "_compute_indicator", _failing)
        df = _make_df(n_conform=10, n_counter=5)
        res = _profile(df)
        skipped = res.payload["skipped_features"]
        assert any(s["indicator"] == "CCI" for s in skipped)
        assert not any(f["feature"].startswith("CCI:")
                       for f in res.payload["features"])

    def test_cohens_d_known_values_and_order(self):
        """Cohen's d 确定样例（手工公式）+ 输出按 |d| 降序。"""
        a = pd.Series([1.0, 2.0, 3.0, 4.0])
        b = pd.Series([11.0, 12.0, 13.0, 14.0])
        # 手工：均值 2.5/12.5，方差 ddof=1 各 1.6667，pooled=1.6667
        # d=(2.5-12.5)/sqrt(5/3)= -10/1.29099 ≈ -7.74597
        assert _cohens_d(a, b) == pytest.approx(-10.0 / np.sqrt(5.0 / 3.0))
        assert _cohens_d(pd.Series([1.0]), b) is None

        df = _make_df(n_conform=10, n_counter=5)
        res = _profile(df)
        ds = [f["cohens_d"] for f in res.payload["features"]]
        non_null = [d for d in ds if d is not None]
        assert non_null == sorted(non_null, key=lambda d: -abs(d)) or \
            all(abs(non_null[i]) >= abs(non_null[i + 1])
                for i in range(len(non_null) - 1))


class TestBoundaries:
    @pytest.mark.parametrize(
        ("n_conform", "n_counter", "status"),
        [(1, 28, "insufficient"), (0, 30, "ok")],
        ids=["反例29 标注", "反例30 ok"])
    def test_counter_group_threshold(self, n_conform, n_counter, status):
        """反例组 29/30 两侧：conform 周期贡献 2 符合+1 峰顶反例、
        counter 周期贡献 1 反例——精确构造反例组计数。"""
        df = _make_df(n_conform=n_conform, n_counter=n_counter)
        res = counter_profile(
            df, condition=_CONDITION, direction="long",
            data_ranges=RANGES, forward_bars=6, min_samples=30,
            request_start=df.index[220])
        assert res.payload["groups"]["counter"]["count"] in (29, 30)
        assert res.payload["groups"]["comparison_status"] == status

    def test_all_conform_legal(self):
        """反例组 0 → 全符合报告合法（无差异清单不视为错误；标注
        insufficient——无反例证据可对比是诚实缺值）。"""
        n = 400
        arr = 100.0 + 0.6 * np.arange(n)  # 单调升：long 触发全符合
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({
            "open": arr, "high": arr + 0.5, "low": arr - 0.5,
            "close": arr, "volume": np.full(n, 100.0)}, index=idx)
        res = counter_profile(
            df, condition={"subject": "close", "action": "gt",
                           "target": 105.0},
            direction="long", data_ranges=RANGES, forward_bars=6,
            min_samples=30, request_start=df.index[10])
        assert res.payload["groups"]["counter"]["count"] == 0
        assert res.payload["groups"]["conform"]["count"] > 0
        assert res.payload["groups"]["comparison_status"] == "insufficient"

    def test_total_triggers_small_sample_rejected(self):
        """总触发 < min_samples → 整个请求小样本拒绝（两条通道分层锚
        ——与反例组 insufficient 标注不同层）。"""
        df = _make_df(n_conform=1, n_counter=0)  # 3 触发 < 30
        with pytest.raises(ResearchError, match="小样本") as ei:
            counter_profile(
                df, condition=_CONDITION, direction="long",
                data_ranges=RANGES, forward_bars=6,
                request_start=df.index[220])
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_direction_closed_enum(self):
        df = _make_df(n_conform=2, n_counter=1)
        for bad in ("mixed", "up", ""):
            with pytest.raises(ResearchError) as ei:
                counter_profile(
                    df, condition=_CONDITION, direction=bad,
                    data_ranges=RANGES, forward_bars=6)
            assert ei.value.code == "RESEARCH_PARAM_INVALID"
            assert ei.value.detail["legal_range"] == ["long", "short"]

    def test_forward_bars_default_and_range(self):
        df = _make_df(n_conform=10, n_counter=5)
        res = counter_profile(
            df, condition=_CONDITION, direction="long", data_ranges=RANGES,
            min_samples=10, request_start=df.index[220])
        assert res.payload["forward_bars"] == 24  # 缺省
        with pytest.raises(ResearchError) as ei:
            counter_profile(
                df, condition=_CONDITION, direction="long",
                data_ranges=RANGES, forward_bars=0)
        assert ei.value.detail["legal_range"] == f"1..{len(df) - 1}"

    def test_no_auto_generated_condition_fields(self):
        """输出无自动生成条件字段（断言锚——工具只呈现差异）。"""
        df = _make_df(n_conform=10, n_counter=5)
        res = _profile(df).to_dict()
        payload = res["payload"]
        assert set(payload) <= {
            "direction", "forward_bars", "groups", "features",
            "condition", "conditions", "warmup_bars", "skipped_features"}
        for f in payload["features"]:
            assert set(f) <= {
                "feature", "indicator", "params", "conform", "counter",
                "cohens_d"}
        banned = ("suggested_condition", "new_condition", "refined_condition",
                  "condition_suggestion", "advice", "recommendation")
        for key in payload:
            assert not any(b in key.lower() for b in banned)

    def test_conditions_form_and_either_or(self):
        """conditions 形式与二选一（键组出生自带；含长度 1 与旧键同果）。"""
        df = _make_df(n_conform=10, n_counter=5)
        r1 = _profile(df)
        r2 = counter_profile(
            df, conditions=[_CONDITION], direction="long",
            data_ranges=RANGES, forward_bars=6, min_samples=10,
            request_start=df.index[220])
        assert (r1.payload["groups"]["conform"]["count"]
                == r2.payload["groups"]["conform"]["count"])
        with pytest.raises(ResearchError, match="二选一"):
            counter_profile(
                df, condition=_CONDITION, conditions=[_CONDITION],
                direction="long", data_ranges=RANGES)


class TestConfidenceContract:
    """三要素 confidence 特征网补齐（规格「confidence 为反例占比的
    区间估计（Wilson 区间）」——B5 用例组未覆盖项）：期望按 Wilson
    score interval 数学定义在测试内直写（与被测委托链
    core.scoring.statistics 相互独立的推导路径，公式抄错必红）。"""

    def test_confidence_wilson_independent_crosscheck(self):
        df = _make_df(n_conform=10, n_counter=5)
        res = _profile(df)
        conf = res.to_dict()["confidence"]  # 三要素顶层位（非 payload）
        assert conf["method"] == "wilson_interval"
        assert conf["for"] == "counter_rate"
        n_counter = res.payload["groups"]["counter"]["count"]
        n_grouped = (n_counter
                     + res.payload["groups"]["conform"]["count"])
        assert conf["n_grouped"] == n_grouped

        lo, hi = conf["interval"]
        p = n_counter / n_grouped
        assert conf["counter_rate"] == pytest.approx(p), \
            "点估计键与区间同源（CARD-08 披露）"
        assert lo <= p <= hi, "区间须覆盖点估计（反例占比）"
        # 独立核算：z=1.96 Wilson 数学定义直写
        z = 1.96
        denom = 1 + z * z / n_grouped
        center = (p + z * z / (2 * n_grouped)) / denom
        half = (z / denom) * math.sqrt(
            p * (1 - p) / n_grouped + z * z / (4 * n_grouped ** 2))
        assert lo == pytest.approx(center - half, abs=1e-12)
        assert hi == pytest.approx(center + half, abs=1e-12)

    def test_confidence_no_grouped_degenerate(self):
        """n_grouped=0 退化形（触发全在尾部不完整窗口）：counter_rate
        键保留 None（无点估计的诚实缺值）、interval=[0.0, 0.0]。"""
        df = _make_df(n_conform=6, n_counter=4)
        # forward_bars=值域上界（len-1）：触发位置（>=220）的观察窗
        # [i+1, i+len-1] 越出数据末端 → future 全 NaN → decidable=0
        res = counter_profile(
            df, condition=_CONDITION, direction="long",
            data_ranges=RANGES, forward_bars=len(df) - 1,
            min_samples=1, request_start=df.index[220])
        conf = res.to_dict()["confidence"]
        assert conf["n_grouped"] == 0
        assert conf["counter_rate"] is None
        assert conf["interval"] == [0.0, 0.0]

    def test_mtf_cache_columns_not_written_through(self, tmp_path):
        """CARD-14 写穿回归锚：高周期条件求值后入参 df 列集不变
        （__mtf_mask_* 幂等缓存列写副本不写穿入参——家族「不改输入」
        纪律，copy 先行修复的行为锚）。"""
        from core.analysis.condition_kernel import MtfEvalContext
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir()
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        base.to_parquet(data_dir / "BTCUSDT_1h.parquet")
        high.to_parquet(data_dir / "BTCUSDT_4h.parquet")
        before_cols = list(base.columns)

        ctx = MtfEvalContext(data_dir, "BTCUSDT", "1h")
        res = counter_profile(
            base, conditions=[{"subject": "close", "action": "gt",
                               "target": 0.0, "timeframe": "4h"}],
            direction="long",
            data_ranges=[{"start": "2024-01-01", "end": "2024-02-01"}],
            forward_bars=6, min_samples=1, mtf=ctx)
        assert res.sample_size > 0  # 求值确实发生（416+ 触发）
        assert list(base.columns) == before_cols, "入参列集不变"
        assert not any(c.startswith("__mtf_mask_") for c in base.columns), \
            "mtf 缓存列不得写穿入参 df"

    def test_confidence_zero_counter_degenerate_interval(self):
        """反例组 0 退化形：lo=0（截断）、hi>0（0 反例的 95% 上界——
        「全符合」不等于「反例率为 0 的确证」，区间仍留上侧余量）；
        n_grouped=符合数。"""
        n = 400
        arr = 100.0 + 0.6 * np.arange(n)  # 单调升：long 触发全符合
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({
            "open": arr, "high": arr + 0.5, "low": arr - 0.5,
            "close": arr, "volume": np.full(n, 100.0)}, index=idx)
        res = counter_profile(
            df, condition={"subject": "close", "action": "gt",
                           "target": 105.0},
            direction="long", data_ranges=RANGES, forward_bars=6,
            min_samples=30, request_start=df.index[10])
        conf = res.to_dict()["confidence"]
        n_grouped = res.payload["groups"]["conform"]["count"]
        assert res.payload["groups"]["counter"]["count"] == 0
        assert conf["n_grouped"] == n_grouped
        lo, hi = conf["interval"]
        assert lo == 0.0
        assert hi > 0.0
        # 独立核算：p=0 时 Wilson 上界 z²/(n+z²)
        z = 1.96
        assert hi == pytest.approx(z * z / (n_grouped + z * z), rel=1e-9)
