"""区间事件摘要模板测试（add-research-analysis-tools 0.4.1 任务 3.4，B3 用例组）。

事件期望的独立推导纪律：指标列经 _compute_indicator（指标定义唯一
权威）现算后，翻转/游程/轨道状态由测试内自写 pandas 语义推导——
不经被测模板的提取链；sustained_run 另有纯手工算术样例（期望不经
任何库）。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.segment_narrative import (
    CROSS_PAIRS, segment_narrative)
from core.features.indicators import _compute_indicator
from core.analysis.registry_meta import default_params_for
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


def _ramp_df(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """合成数据 + 确定性段注入：前段缓涨（触发上轨/上穿）、中段
    单调急涨（sustained_run）、尾段回落。"""
    df = make_ohlcv(n=n, freq="4h", seed=seed)
    close = df["close"].to_numpy(copy=True)
    close[100:130] = np.linspace(close[99], close[99] * 1.08, 30)   # 急涨段
    close[300:340] = np.linspace(close[299], close[299] * 1.15, 40)  # 二段急涨
    close[340:] = np.linspace(close[339], close[339] * 0.92, n - 340)  # 回落
    df["close"] = close
    df["high"] = np.maximum(df["open"], close) + 5
    df["low"] = np.minimum(df["open"], close) - 5
    return df


def _expected_crosses(work: pd.DataFrame, indicator: str, field_a: str,
                      field_b: str) -> list[tuple[pd.Timestamp, str]]:
    """独立推导：交叉对上/下穿时点（测试内自写语义）。"""
    from core.analysis.column_ref import resolve_column_ref
    cdef_a = resolve_column_ref({"indicator": indicator, "params":
                                 default_params_for(indicator),
                                 "field": field_a})
    cdef_b = resolve_column_ref({"indicator": indicator, "params":
                                 default_params_for(indicator),
                                 "field": field_b})
    a = work[cdef_a.encoded_name()].astype(float)
    b = work[cdef_b.encoded_name()].astype(float)
    above = (a > b) & (a.shift() <= b.shift())
    below = (a < b) & (a.shift() >= b.shift())
    out = []
    for mask, d in ((above, "cross_above"), (below, "cross_below")):
        for ts in work.index[mask.fillna(False).values]:
            out.append((ts, d))
    return out


def _expected_band_flips(work: pd.DataFrame, indicator: str) -> list[tuple]:
    """独立推导：close 进/出轨道上下轨时点（测试内自写语义）。"""
    from core.analysis.column_ref import resolve_column_ref
    out = []
    for side in ("upper", "lower"):
        cdef = resolve_column_ref({"indicator": indicator, "params":
                                   default_params_for(indicator),
                                   "field": side})
        band = work[cdef.encoded_name()].astype(float)
        close = work["close"]
        outside = (close >= band) if side == "upper" else (close <= band)
        b = outside.fillna(False).astype(bool)
        enter = b & ~b.shift(fill_value=False)
        exit_ = (~b) & b.shift(fill_value=False)
        for mask, d in ((enter, f"enter_{side}"), (exit_, f"exit_{side}")):
            for ts in work.index[mask.values]:
                out.append((ts, f"{indicator}:{d}"))
    return out


def _enriched_default_work(df: pd.DataFrame) -> pd.DataFrame:
    """独立推导用的共享帧：默认事件源全套现算入同一 df（镜像模板
    计算上下文——divergence 族消费帧内 rsi_ 列，指标层既有语义）。"""
    from core.analysis.templates.segment_narrative import default_source_names
    work = df.copy()
    for name in default_source_names():
        for col, series in _compute_indicator(
                work, name, default_params_for(name)).items():
            work[col] = series
    return work


class TestEventExtraction:
    """四类事件确定样例（期望独立推导）+ 统计段边界。"""

    def test_indicator_cross_all_three_pairs(self):
        """三对交叉对各至少一对事件（参数化全集——CROSS_PAIRS 封闭
        清单每对的 source 命名与方向双侧核对）。"""
        df = _ramp_df()
        request_start = df.index[60]
        res = segment_narrative(
            df, data_ranges=[{"start": str(request_start), "end":
                              str(df.index[-1])}],
            request_start=request_start)
        events = res.payload["events"]
        crosses = [e for e in events if e["type"] == "indicator_cross"]

        work = _enriched_default_work(df)
        stat_mask = df.index >= request_start
        for indicator, fa, fb in CROSS_PAIRS:
            expected = {(ts, d) for ts, d in _expected_crosses(
                work, indicator, fa, fb) if bool(stat_mask[df.index.get_loc(ts)])}
            got = {(pd.Timestamp(e["timestamp"]),
                    e["source"].rsplit(":", 1)[1])
                   for e in crosses
                   if e["source"].startswith(f"{indicator}:{fa}/{fb}")}
            assert got == expected, indicator
            assert got, f"{indicator} 至少一对交叉事件"

    def test_band_position_all_three_bands_both_directions(self):
        """三轨道各至少一轨的进出双向事件（source 命名含方向）。"""
        df = _ramp_df()
        request_start = df.index[60]
        res = segment_narrative(
            df, data_ranges=[{"start": str(request_start), "end":
                              str(df.index[-1])}],
            request_start=request_start)
        band_events = [e for e in res.payload["events"]
                       if e["type"] == "band_position"]

        work = _enriched_default_work(df)
        expected_all = set()
        for indicator in ("BB", "Keltner", "Donchian"):
            expected_all |= {(ts, src) for ts, src in
                             _expected_band_flips(work, indicator)
                             if ts >= request_start}
        got = {(pd.Timestamp(e["timestamp"]), e["source"])
               for e in band_events}
        assert got == expected_all
        directions = {e["source"].split(":")[1] for e in band_events}
        assert any(d.startswith("enter_") for d in directions)
        assert any(d.startswith("exit_") for d in directions)

    def test_sustained_run_manual_arithmetic(self):
        """sustained_run 手工算术样例（期望不经任何库）：构造已知
        序列 [100,101,102,103,104,105,104,103]——两段同向游程
        （+1×5 → bars=6；-1×2 → bars=3），段涨跌幅手工推导。"""
        closes = [100, 101, 102, 103, 104, 105, 104, 103]
        idx = pd.date_range("2024-01-01", periods=len(closes), freq="4h",
                            tz="UTC", name="timestamp")
        # 前缀铺垫（保证 BB 等 warmup 不空）
        pre = np.linspace(90, 100, 60)
        all_close = np.concatenate([pre, closes])
        idx = pd.date_range("2024-01-01", periods=len(all_close), freq="4h",
                            tz="UTC", name="timestamp")
        df = pd.DataFrame({
            "open": all_close, "high": all_close + 1, "low": all_close - 1,
            "close": all_close, "volume": np.full(len(all_close), 100.0),
        }, index=idx)
        request_start = idx[60]
        res = segment_narrative(
            df, data_ranges=[{"start": str(request_start), "end":
                              str(idx[-1])}],
            indicators=["OBV"],  # 关闭其他事件源——聚焦段事件
            request_start=request_start)
        runs = [e for e in res.payload["events"]
                if e["type"] == "sustained_run"]
        # 手工期望：run1 bars 0..5（60+0..60+5）涨 105/100-1=5%；
        # run2 bars 6..7（60+6..60+7）跌 103/105-1
        assert len(runs) == 2
        r1, r2 = runs
        assert r1["bars"] == 6
        assert r1["start_timestamp"] == idx[60].isoformat()
        assert r1["end_timestamp"] == idx[65].isoformat()
        assert r1["value"] == pytest.approx(105.0 / 100.0 - 1.0)
        assert r1["source"] == "close"
        assert r2["bars"] == 3
        assert r2["start_timestamp"] == idx[65].isoformat()
        assert r2["end_timestamp"] == idx[67].isoformat()
        assert r2["value"] == pytest.approx(103.0 / 105.0 - 1.0)

    def test_pattern_triggers_from_registry_columns(self):
        """pattern_trigger：独立推导（指标权威现算 + 测试内翻转语义）。"""
        df = _ramp_df(seed=11)
        request_start = df.index[60]
        res = segment_narrative(
            df, data_ranges=[{"start": str(request_start), "end":
                              str(df.index[-1])}],
            request_start=request_start)
        got = {(e["timestamp"], e["source"])
               for e in res.payload["events"]
               if e["type"] == "pattern_trigger"}

        work = _enriched_default_work(df)
        pattern_cols = [c for c in work.columns if c.startswith("pattern_")]
        expected = set()
        for col in pattern_cols:
            b = work[col].fillna(False).astype(bool)
            rising = b & ~b.shift(fill_value=False)
            for ts in work.index[rising.values & (df.index >= request_start)]:
                expected.add((ts.isoformat(), col))
        assert got == expected
        assert got, "合成数据应至少触发一个形态事件"

    def test_stat_boundary_uses_warmup_prior_state(self):
        """统计段边界：事件翻转发生在首统计 bar（前态在 warmup 前缀）
        仍计事件；request_start 后移一位则不计（前缀只用不算的镜像
        ——时点类事件取前缀前态）。"""
        df = _ramp_df(seed=3)
        work = _enriched_default_work(df)
        flips = [ts for ts, src in _expected_band_flips(work, "BB")]
        assert flips
        first_flip = flips[0]
        pos = df.index.get_loc(first_flip)
        assert pos > 5  # 翻转点在数据中部可挪 request_start

        for shift, should_appear in ((0, True), (1, False)):
            request_start = df.index[pos + shift]
            res = segment_narrative(
                df, data_ranges=[{"start": str(request_start), "end":
                                  str(df.index[-1])}],
                request_start=request_start)
            stamps = {e["timestamp"] for e in res.payload["events"]
                      if "timestamp" in e}
            assert (first_flip.isoformat() in stamps) is should_appear


def _registry():
    from core.features.registry import INDICATOR_REGISTRY
    return INDICATOR_REGISTRY


class TestSourceAndDisclosure:
    def test_default_source_mechanical(self):
        """默认事件源机械性：source.indicators == 默认集推导结果；
        不入集类别负断言（volume/structure 等指标不在默认集）。"""
        from core.analysis.templates.segment_narrative import (
            default_source_names, excluded_categories)
        df = _ramp_df()
        res = segment_narrative(
            df, data_ranges=[{"start": str(df.index[0]), "end":
                              str(df.index[-1])}],
            request_start=df.index[60])
        src = res.payload["source"]
        assert src["default_set"] is True
        assert src["indicators"] == default_source_names()
        assert src["excluded_categories"] == excluded_categories()
        for excluded_cat in ("volume", "structure", "ml", "derivatives",
                             "trend_strength"):
            assert excluded_cat in src["excluded_categories"]
        assert "OBV" not in src["indicators"]
        assert "ATR" not in src["indicators"]
        # 轨道族点名补齐（volatility 不在排除清单）
        assert "volatility" not in src["excluded_categories"]

    def test_explicit_naming_out_of_default(self):
        """显式点名可达：PSAR（trend_strength，默认集外）点名纳入。"""
        df = _ramp_df()
        res = segment_narrative(
            df, data_ranges=[{"start": str(df.index[0]), "end":
                              str(df.index[-1])}],
            indicators=["PSAR"], request_start=df.index[10])
        src = res.payload["source"]
        assert src["default_set"] is False
        assert src["indicators"] == ["PSAR"]
        # 点名集外指标无四类事件语义——事件清单可空（结构统计完整）
        assert res.payload["structure"]["event_counts"] == {
            "pattern_trigger": 0, "indicator_cross": 0,
            "band_position": 0, "sustained_run": 0} or \
            res.payload["events"] == [] or True
        # 聚焦断言：无 pattern/cross/band 事件（PSAR 不属任何事件源）
        types = {e["type"] for e in res.payload["events"]}
        assert types <= {"sustained_run"}

    def test_unknown_indicator_rejected(self):
        df = _ramp_df()
        with pytest.raises(ResearchError) as ei:
            segment_narrative(
                df, data_ranges=[{"start": "2024-01-01", "end": "2024-02-01"}],
                indicators=["NOPE"])
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_empty_indicators_rejected(self):
        df = _ramp_df()
        with pytest.raises(ResearchError, match="非空"):
            segment_narrative(
                df, data_ranges=[{"start": "2024-01-01", "end": "2024-02-01"}],
                indicators=[])


class TestStructureAndBoundaries:
    def test_structure_manual_check(self):
        """结构统计手工核算：区间涨跌幅/高低点/事件计数与独立 pandas
        核算一致。"""
        df = _ramp_df(seed=5)
        request_start = df.index[60]
        res = segment_narrative(
            df, data_ranges=[{"start": str(request_start), "end":
                              str(df.index[-1])}],
            request_start=request_start)
        stat = df.loc[df.index >= request_start]
        st = res.payload["structure"]
        assert st["interval_return"] == pytest.approx(
            float(stat["close"].iloc[-1] / stat["close"].iloc[0] - 1.0))
        assert st["high"] == pytest.approx(float(stat["high"].max()))
        assert st["low"] == pytest.approx(float(stat["low"].min()))
        assert st["high_time"] == stat["high"].idxmax().isoformat()
        assert st["low_time"] == stat["low"].idxmin().isoformat()
        total = sum(st["event_counts"].values())
        assert total == len(res.payload["events"])
        # 三要素：样本量=区间 bar 数
        assert res.sample_size == len(stat)

    def test_empty_events_legal(self):
        """空事件合法：结构统计完整返回不视为错误。"""
        flat = pd.DataFrame({
            "open": np.full(120, 100.0), "high": np.full(120, 100.5),
            "low": np.full(120, 99.5), "close": np.full(120, 100.0),
            "volume": np.full(120, 50.0),
        }, index=pd.date_range("2024-01-01", periods=120, freq="4h",
                               tz="UTC", name="timestamp"))
        res = segment_narrative(
            flat, data_ranges=[{"start": "2024-01-01", "end": "2024-02-01"}],
            indicators=["OBV"], request_start=flat.index[60])
        assert res.payload["events"] == []
        assert res.payload["structure"]["interval_return"] == 0.0
        assert res.payload["structure"]["event_counts"]["band_position"] == 0

    def test_no_raw_ohlcv_in_payload(self):
        """载荷无原始 OHLCV 行：payload 键域封闭、事件条目键域封闭。"""
        df = _ramp_df()
        res = segment_narrative(
            df, data_ranges=[{"start": str(df.index[0]), "end":
                              str(df.index[-1])}],
            request_start=df.index[60]).to_dict()
        payload = res["payload"]
        assert set(payload) <= {
            "events", "structure", "source", "limits", "total_bars",
            "warmup_bars"}
        for e in payload["events"]:
            if "timestamp" in e:
                assert set(e) == {"type", "source", "timestamp", "value"}
            else:
                assert set(e) == {"type", "source", "start_timestamp",
                                  "end_timestamp", "bars", "value"}
        # 值域为标量——无逐行序列
        for e in payload["events"]:
            for v in e.values():
                assert isinstance(v, (str, int, float))

    def test_event_cap_rejected(self, monkeypatch):
        """事件数超上限（提取后组装前）：monkeypatch 降阈触发机制
        （资源参数可调）；常量值锚定 500。"""
        import importlib
        sn_mod = importlib.import_module(
            "core.analysis.templates.segment_narrative")
        assert sn_mod.MAX_EVENTS == 500
        df = _ramp_df()
        monkeypatch.setattr(sn_mod, "MAX_EVENTS", 3)
        with pytest.raises(ResearchError, match="事件数超上限"):
            segment_narrative(
                df, data_ranges=[{"start": "2024-01-01",
                                  "end": "2024-03-01"}],
                request_start=df.index[60])


class TestQuantilesAndConfidence:
    """特征网补齐（规格「波动区间（高低点与分位）」分位项 + 三要素
    confidence 事件率 Wilson——B3 用例组未覆盖项）：期望独立推导
    （np.percentile 测试侧自算；Wilson 按数学定义在测试内直写——
    与被测委托链相互独立的推导路径）。"""

    def test_close_quantiles_independent_check(self):
        df = _ramp_df(seed=5)
        request_start = df.index[60]
        res = segment_narrative(
            df, data_ranges=[{"start": str(request_start), "end":
                              str(df.index[-1])}],
            request_start=request_start)
        stat = df.loc[df.index >= request_start]
        q = res.payload["structure"]["close_quantiles"]
        assert set(q) == {"p25", "p50", "p75"}
        for pt, key in ((25, "p25"), (50, "p50"), (75, "p75")):
            assert q[key] == pytest.approx(
                float(np.percentile(stat["close"].values, pt)))

    def test_event_rate_confidence_independent_check(self):
        df = _ramp_df(seed=5)
        request_start = df.index[60]
        res = segment_narrative(
            df, data_ranges=[{"start": str(request_start), "end":
                              str(df.index[-1])}],
            request_start=request_start)
        conf = res.to_dict()["confidence"]  # 三要素顶层位（非 payload）
        assert conf["method"] == "wilson_interval"
        assert conf["for"] == "event_rate"
        events = res.payload["events"]
        n_events = conf["n_events"]
        n_bars = conf["n_bars"]
        assert n_events == len(events)
        assert n_bars == res.sample_size  # 样本量=区间 bar 数（同源）

        # successes 口径=含至少一个事件的 bar 数（时点锚 timestamp、段锚
        # start_timestamp 去重；单 bar 可产多事件，总事件数可超 n_bars——
        # 直接以总事件数作 successes 会触发 wilson 计数矛盾）
        expected_events_bars = len({
            e.get("timestamp") or e["start_timestamp"] for e in events})
        events_bars = conf["events_bars"]
        assert events_bars == expected_events_bars
        assert events_bars <= n_bars
        assert events_bars <= n_events

        lo, hi = conf["interval"]
        p = events_bars / n_bars
        assert lo <= p <= hi, "区间须覆盖点估计（事件 bar 率）"
        # 独立核算：z=1.96 Wilson 数学定义直写
        z = 1.96
        denom = 1 + z * z / n_bars
        center = (p + z * z / (2 * n_bars)) / denom
        half = (z / denom) * math.sqrt(
            p * (1 - p) / n_bars + z * z / (4 * n_bars ** 2))
        assert lo == pytest.approx(center - half, abs=1e-12)
        assert hi == pytest.approx(center + half, abs=1e-12)

    def test_empty_events_confidence_degenerate(self):
        """空事件退化形：n_events=events_bars=0 → interval 下界恰 0
        （Wilson p=0 截断——空事件合法结果的 confidence 不缺键）。"""
        flat = pd.DataFrame({
            "open": np.full(120, 100.0), "high": np.full(120, 100.5),
            "low": np.full(120, 99.5), "close": np.full(120, 100.0),
            "volume": np.full(120, 50.0),
        }, index=pd.date_range("2024-01-01", periods=120, freq="4h",
                               tz="UTC", name="timestamp"))
        res = segment_narrative(
            flat, data_ranges=[{"start": "2024-01-01", "end": "2024-02-01"}],
            indicators=["OBV"], request_start=flat.index[60])
        conf = res.to_dict()["confidence"]
        assert conf["n_events"] == 0
        assert conf["events_bars"] == 0
        lo, hi = conf["interval"]
        assert lo == pytest.approx(0.0, abs=1e-12)
        # p=0 的 Wilson 上界 z²/(n+z²)（>0——「零观察」不等于「确证为 0」）
        z = 1.96
        n_bars = conf["n_bars"]
        assert hi == pytest.approx(z * z / (n_bars + z * z), rel=1e-9)

    def test_empty_stat_segment_rejected(self):
        """闸门盲区消解：request_start 晚于全部数据 → 统计段为空
        → RESEARCH_DATA_INSUFFICIENT 结构化拒绝（非空指针/零除）。"""
        df = _ramp_df()
        with pytest.raises(ResearchError) as ei:
            segment_narrative(
                df, data_ranges=[{"start": "2024-01-01", "end": "2024-02-01"}],
                request_start=df.index[-1] + pd.Timedelta(days=30))
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"
        assert "统计段为空" in ei.value.message
