"""箱体两件指标契约测试（add-box-capability 任务 1.1-1.3/2.1-2.3）。

BoxRegime（识别件，极值宽族）/BoxChannel（沿通道件，分位沿族）三列/
五列契约、条件链五面（query_indicator 归约/condition_stats/假设登记/
翻译/回测求值）、参数域与 cross_params 拒绝、warmup 数值锚、计数与
naming 三层同步、两族口径分界样例（X-A/X-B）。

期望全部人工核算或 demo 公式独立重演构造（T1 铁律——不经被测内核
生成）；真实数据用例循 demo 复算先例（不在库即 skip）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.features.indicators import _compute_indicator
from core.features.registry import INDICATOR_REGISTRY, resolve_indicator_column
from core.analysis.column_ref import (
    ColumnRefError, decode_column_ref, encode_column_ref,
    _resolve_indicator_ref)

from core.data.fapi.rest import default_data_dir

MARKET = default_data_dir()


def _mk_df(closes, highs=None, lows=None, start="2024-01-01", freq="1h"):
    """closes → OHLCV df（highs/lows 缺省=close 对称 ±spread 由调用方给）。"""
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC",
                        name="timestamp")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({
        "open": c,
        "high": c if highs is None else pd.Series(highs, index=idx,
                                                  dtype=float),
        "low": c if lows is None else pd.Series(lows, index=idx,
                                                dtype=float),
        "close": c, "volume": 1.0,
    }, index=idx)


def _mk_spread_df(spreads, close=100.0):
    """恒定 close、对称高低差构造器（box_inventory 测试同款）：width=
    (hi−lo)/mid 由窗内 spread 分布决定。"""
    idx = pd.date_range("2024-01-01", periods=len(spreads), freq="1h",
                        tz="UTC", name="timestamp")
    s = pd.Series(spreads, index=idx, dtype=float)
    return pd.DataFrame({
        "open": close, "high": close + s, "low": close - s,
        "close": float(close), "volume": 1.0,
    }, index=idx)


# ----------------------------------------------------------- 1.1/2.1/4.1 注册与命名

class TestRegistrationAndNaming:
    """注册可见性、列名格式样例锚、candidates、白名单扩容、三层同步。"""

    def test_registry_visible_category_count(self):
        for name in ("BoxRegime", "BoxChannel"):
            d = INDICATOR_REGISTRY[name]
            assert d.category == "structure"
        assert len(INDICATOR_REGISTRY) == 55  # champion 五件入库后（2026-11-09）
        assert len(INDICATOR_REGISTRY["BoxRegime"].output_fields) == 3
        assert len(INDICATOR_REGISTRY["BoxChannel"].output_fields) == 5

    def test_indicator_metadata_carries_both(self):
        from core.analysis.registry_meta import indicator_metadata
        names = {m["name"] for m in indicator_metadata()}
        assert {"BoxRegime", "BoxChannel"} <= names

    def test_column_name_format_samples(self):
        """列名格式＋具体样例锚（float 编码 str 形制固化：
        str(0.08)="0.08"、str(0.10)="0.1"）。"""
        assert resolve_indicator_column(
            "BoxRegime",
            {"window": 120, "width_thr": 0.08, "min_len": 48}) == \
            "boxreg_120_0.08_48_box_state"
        assert resolve_indicator_column(
            "BoxChannel",
            {"edge_window": 240, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}, "upper") == "boxch_240_0.1_0.9_0.25_upper"
        # 全 field 遍历格式（前缀_参数链_field）
        for f in INDICATOR_REGISTRY["BoxRegime"].output_fields:
            assert resolve_indicator_column(
                "BoxRegime",
                {"window": 120, "width_thr": 0.08, "min_len": 48},
                f) == f"boxreg_120_0.08_48_{f}"
        for f in INDICATOR_REGISTRY["BoxChannel"].output_fields:
            assert resolve_indicator_column(
                "BoxChannel",
                {"edge_window": 240, "q_low": 0.10, "q_high": 0.90,
                 "buffer": 0.25}, f) == f"boxch_240_0.1_0.9_0.25_{f}"

    def test_naming_three_layers_identical(self):
        """C2 naming 三层同步：resolve 正向解析/产列自拼/column_ref 逆
        解码按两式列名逐项相等（encode/decode 往返）。"""
        reg_params = {"window": 120, "width_thr": 0.08, "min_len": 48}
        ch_params = {"edge_window": 240, "q_low": 0.10, "q_high": 0.90,
                     "buffer": 0.25}
        df = _mk_df([100.0] * 12, [101.0] * 12, [99.0] * 12)
        for name, params in (("BoxRegime", reg_params),
                             ("BoxChannel", ch_params)):
            produced = set(_compute_indicator(df, name, params))
            for f in INDICATOR_REGISTRY[name].output_fields:
                forward = resolve_indicator_column(name, params, f)
                encoded = encode_column_ref(name, params, f)
                assert forward == encoded, (name, f)
                assert forward in produced, (name, f, forward)
                decoded = decode_column_ref(forward)
                assert decoded["indicator"] == name
                assert decoded["field"] == f
                assert decoded["params"] == params

    def test_candidates_registered(self):
        rp = INDICATOR_REGISTRY["BoxRegime"].params
        assert rp["window"].candidates == [120, 240]
        assert rp["min_len"].candidates == [48, 96]
        cp = INDICATOR_REGISTRY["BoxChannel"].params
        assert cp["edge_window"].candidates == [240, 480]
        # 默认取 1h 冻结值（timeframe 感知 registry 形制）
        assert rp["window"].default == 120 and rp["min_len"].default == 48
        assert cp["edge_window"].default == 240

    def test_period_like_expansion_zero_regression(self):
        """白名单扩容自带零回归扫描：既有件参数名与新增两键零命中
        （排除集随 champion 五件入库扩至七件——五件自带 window/
        rank_window 参数属合法消费非冲突）＋既有件 warmup 推导不因
        扩容改变。"""
        from core.analysis.registry_meta import (
            _PERIOD_LIKE_PARAMS, derive_warmup)
        assert {"window", "edge_window"} <= _PERIOD_LIKE_PARAMS
        assert "min_len" not in _PERIOD_LIKE_PARAMS
        newkeys = {"window", "edge_window"}
        owners = {"BoxRegime", "BoxChannel", "ShannonEntropy",
                  "PermutationEntropy", "EfficiencyRatio", "CUSUM",
                  "GARCHFilter"}
        hits = [(n, k) for n, d in INDICATOR_REGISTRY.items()
                if n not in owners
                for k in d.params if k in newkeys]
        assert hits == [], f"扩容撞既有参数名: {hits}"
        # 既有件推导锚（扩容前后同值——left/right 先例不受影响）
        assert derive_warmup(["Pivot"]) == 600
        assert derive_warmup(["EMA"]) == 600

    def test_supported_conditions_registry(self):
        assert set(INDICATOR_REGISTRY["BoxRegime"].supported_conditions) == \
            {"gt", "lt", "eq", "within_bars_after"}
        assert set(INDICATOR_REGISTRY["BoxChannel"].supported_conditions) == \
            {"price_above", "price_below", "cross_above_series",
             "cross_below_series", "touch_bounce", "role_reversal",
             "gt", "lt", "within_bars_after"}


# ----------------------------------------------------------- 1.2 BoxRegime 三列契约

class TestBoxRegimeContract:
    """BR1 三法确定样例（期望不经被测内核生成）/BR2 防前视双不变量/
    与 r9_d1_ruler.py 同参数同数据逐位同源算例。"""

    def test_br1_constant_price_equality_inclusive(self):
        """恒定价格（spread=4、close=100、W=3）：width=8/100=0.08 恰=
        thr 按含等号成箱；bar0-1 NaN 期 False。"""
        df = _mk_spread_df([4.0] * 6)
        out = _compute_indicator(
            df, "BoxRegime", {"window": 3, "width_thr": 0.08, "min_len": 2})
        state = out["boxreg_3_0.08_2_box_state"]
        assert list(state) == [0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
        width = out["boxreg_3_0.08_2_box_width"]
        assert np.isnan(width.iloc[0]) and np.isnan(width.iloc[1])
        assert width.iloc[2] == pytest.approx(0.08)
        # box_width 与 state 自洽：state=1 ⇔ width ≤ thr（NaN 期 False）
        assert ((width <= 0.08).fillna(False).astype(float) == state).all()

    def test_br1_linear_increase_manual(self):
        """线性递增（close=100..、±1 对称、W=3）：t≥2 处 hi=c[t]+1、
        lo=c[t−2]−1、mid=三根均值——手算锚（thr 宽松全成箱）。"""
        closes = [100.0 + i for i in range(8)]
        df = _mk_df(closes, [c + 1 for c in closes],
                    [c - 1 for c in closes])
        out = _compute_indicator(
            df, "BoxRegime", {"window": 3, "width_thr": 0.08, "min_len": 2})
        width = out["boxreg_3_0.08_2_box_width"]
        for t in range(2, 8):
            hi, lo = closes[t] + 1, closes[t - 2] - 1
            mid = sum(closes[t - 2:t + 1]) / 3
            assert width.iloc[t] == pytest.approx((hi - lo) / mid), t
        # 线性递增段宽度≈4/mid<0.04 → 全箱体态（t≥2）
        assert (out["boxreg_3_0.08_2_box_state"].iloc[2:] == 1.0).all()

    def test_br1_small_sample_manual(self):
        """手算小样本（W=1 逐 bar 独立、spread=[5,4,3,3,3,5]）：
        width=[.10,.08,.06,.06,.06,.10]、thr=.08 → state=[0,1,1,1,1,0]；
        established（min_len=2）=[0,0,1,1,1,0]。"""
        df = _mk_spread_df([5.0, 4.0, 3.0, 3.0, 3.0, 5.0])
        out = _compute_indicator(
            df, "BoxRegime", {"window": 1, "width_thr": 0.08, "min_len": 2})
        state = out["boxreg_1_0.08_2_box_state"]
        assert list(state) == [0.0, 1.0, 1.0, 1.0, 1.0, 0.0]
        est = out["boxreg_1_0.08_2_box_established"]
        assert list(est) == [0.0, 0.0, 1.0, 1.0, 1.0, 0.0]

    def test_established_min_len_boundaries(self):
        """恰 min_len 成真/min_len−1 不成真/min_len=1 退化为 state。"""
        df = _mk_spread_df([5.0, 4.0, 4.0, 4.0, 4.0, 4.0, 5.0])  # state=[0,1,1,1,1,0,0]
        base = {"window": 1, "width_thr": 0.08}
        out3 = _compute_indicator(df, "BoxRegime",
                                  {**base, "min_len": 3})
        est3 = out3["boxreg_1_0.08_3_box_established"]
        # t2=连续 2 根（min_len−1）不成真；t3=恰 3 根成真
        assert est3.iloc[2] == 0.0 and est3.iloc[3] == 1.0
        out1 = _compute_indicator(df, "BoxRegime", {**base, "min_len": 1})
        est1 = out1["boxreg_1_0.08_1_box_established"]
        state1 = out1["boxreg_1_0.08_1_box_state"]
        pd.testing.assert_series_equal(est1, state1, check_names=False)

    def test_br2_truncation_and_future_invariants(self):
        """BR2 双不变量：截断不变量（前 k 根与全序列前 k 根逐位一致）
        ＋未来不变量（修改 t+1 起数据不改变 t 及之前输出）。"""
        rng = np.random.default_rng(7)
        n = 40
        close = pd.Series(100 + rng.normal(0, 1, n).cumsum())
        spread = pd.Series(rng.uniform(1, 6, n))
        df = pd.DataFrame({
            "open": close, "high": close + spread, "low": close - spread,
            "close": close, "volume": 1.0,
        }, index=pd.date_range("2024-01-01", periods=n, freq="1h",
                               tz="UTC", name="timestamp"))
        params = {"window": 5, "width_thr": 0.08, "min_len": 4}
        full = _compute_indicator(df, "BoxRegime", params)
        # 截断不变量（k 取中段）
        k = 20
        prefix = _compute_indicator(df.iloc[:k], "BoxRegime", params)
        for col in full:
            pd.testing.assert_series_equal(
                prefix[col], full[col].iloc[:k], check_names=False)
        # 未来不变量：改 t+1（t=10）起的 close/high/low
        df2 = df.copy()
        df2.iloc[11:, df2.columns.get_loc("close")] = 500.0
        df2.iloc[11:, df2.columns.get_loc("high")] = 510.0
        df2.iloc[11:, df2.columns.get_loc("low")] = 490.0
        after = _compute_indicator(df2, "BoxRegime", params)
        for col in full:
            pd.testing.assert_series_equal(
                full[col].iloc[:11], after[col].iloc[:11],
                check_names=False)

    def test_ruler_d1_same_source_bitwise(self):
        """与 r9_d1_ruler.py:65-67 同参数同数据逐位同源：demo 三行公式
        （hi/lo/mid/width/state）独立重演，期望序列不经被测内核。"""
        rng = np.random.default_rng(42)
        n = 300
        close = pd.Series(100 + rng.normal(0, 2, n).cumsum())
        spread = pd.Series(rng.uniform(0.5, 8, n))
        df = pd.DataFrame({
            "open": close, "high": close + spread, "low": close - spread,
            "close": close, "volume": 1.0,
        }, index=pd.date_range("2024-01-01", periods=n, freq="1h",
                               tz="UTC", name="timestamp"))
        w, thr = 120, 0.08
        out = _compute_indicator(
            df, "BoxRegime", {"window": w, "width_thr": thr, "min_len": 48})
        # demo 独立重演（r9_d1_ruler.py:61-67 原式）
        hi = df["high"].rolling(w).max()
        lo = df["low"].rolling(w).min()
        mid = df["close"].rolling(w).mean()
        width = (hi - lo) / mid
        state = (width <= thr).fillna(False)
        col_w = out[f"boxreg_{w}_{thr}_48_box_width"]
        col_s = out[f"boxreg_{w}_{thr}_48_box_state"]
        pd.testing.assert_series_equal(col_w, width, check_names=False)
        # 布尔列数值化（True=1.0）与 demo 布尔逐位同源
        assert (col_s.astype(bool) == state).all()
        assert set(col_s.dropna().unique()) <= {0.0, 1.0}


# ----------------------------------------------------------- 1.3 BoxRegime 条件链五面

class TestBoxRegimeConditions:
    """五面全通＋布尔条件行为锚＋负向锚＋warmup 720。"""

    @staticmethod
    def _box_df(n=120, seed=3):
        """恒定 close＋分段 spread（前半 3.0→width=0.06 箱体态、后半
        5.0→width=0.10 非箱——窗 20 过渡带外两态确定，混合形态保证
        检出力）。close 用 ndarray 构造（Series 带 RangeIndex 进
        DatetimeIndex DataFrame 会索引对齐全 NaN——假绿坑，检出断言
        在各用例前置）。"""
        idx = pd.date_range("2024-01-01", periods=n, freq="1h",
                            tz="UTC", name="timestamp")
        spread = np.concatenate([np.full(n // 2, 3.0),
                                 np.full(n - n // 2, 5.0)])
        close = np.full(n, 100.0)
        return pd.DataFrame({
            "open": close, "high": close + spread, "low": close - spread,
            "close": close, "volume": 1.0,
        }, index=idx)

    PARAMS = {"window": 20, "width_thr": 0.08, "min_len": 10}

    def _state_series(self, df):
        out = _compute_indicator(df, "BoxRegime", self.PARAMS)
        return out["boxreg_20_0.08_10_box_state"]

    def test_face_query_indicator_reductions(self):
        """面一 query_indicator：latest/quantiles/trend_slope 三归约可算
        （真实数据；不在库 skip——demo 复算先例）。"""
        if not (MARKET / "BTCUSDT_1h.parquet").exists():
            pytest.skip("真实数据不在库（data/market_fapi）")
        from core.analysis.indicator_query import query_indicator
        out = query_indicator(
            MARKET, symbol="BTCUSDT", timeframe="1h", indicator="BoxRegime",
            params={"window": 120, "width_thr": 0.08, "min_len": 48},
            start="2026-09-01", end="2026-11-01",
            reductions=["latest", "quantiles", "trend_slope"])
        assert out["indicator"] == "BoxRegime"
        assert out["warmup_bars"] == 720
        reds = out["reductions"]["boxreg_120_0.08_48_box_state"]
        assert set(reds) == {"latest", "quantiles", "trend_slope"}
        assert reds["latest"]["value"] in (0.0, 1.0)

    def test_face_condition_stats(self):
        """面二 condition_stats：结构化 subject（field=box_state）经条件
        统计——信号计数（含尾部丢弃）与手算一致。"""
        from core.analysis.templates.condition_stats import condition_stats
        df = self._box_df()
        state = self._state_series(df)
        out = condition_stats(
            df, indicator="BoxRegime", params=self.PARAMS,
            condition={
                "subject": {"indicator": "BoxRegime",
                            "params": self.PARAMS, "field": "box_state"},
                "action": "gt", "target": 0.5},
            data_ranges=[{"symbol": "T", "timeframe": "1h",
                          "start": "2024-01-01", "end": "2024-01-06"}],
            min_samples=1).to_dict()
        p = out["payload"]
        total = int((state > 0.5).sum())
        assert p["hit_count"] == total, "hit_count=统计段总命中（dropped 为其中尾部子集）"
        assert p["hit_count"] > 0

    def test_face_register_and_translate(self, tmp_path):
        """面三/四 假设登记＋翻译：BoxRegime eq 1 条件登记入册并翻译为
        SignalGene（indicator/params 原样传递）。"""
        from core.analysis.hypothesis_to_strategy import build_signal_genes
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables

        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        cond = {"subject": {"indicator": "BoxRegime", "field": "box_state",
                            "params": {"window": 120, "width_thr": 0.08,
                                       "min_len": 48}},
                "action": "eq", "target": 1}
        then = {"subject": {"indicator": "RSI", "params": {"period": 14}},
                "action": "gt", "target": 50}
        genes, _sources = build_signal_genes(
            [cond], [then], db, strategy_timeframe="1h")
        g = genes[0]
        assert g.indicator == "BoxRegime" and g.field_name == "box_state"
        assert g.params == {"window": 120, "width_thr": 0.08,
                            "min_len": 48}

    def test_face_evaluate_condition(self):
        """面五回测求值：eq 1/gt 0.5/within_bars_after 三动作行为锚。"""
        from core.strategy.executor import evaluate_condition
        df = self._box_df()
        state = self._state_series(df)
        eq1 = evaluate_condition(state, df["close"], {"type": "eq"})
        gt05 = evaluate_condition(state, df["close"],
                                  {"type": "gt", "threshold": 0.5})
        assert (eq1 == (state == 1.0)).all()
        assert (gt05 == (state > 0.5)).all()

    def test_gt1_always_false_negative(self):
        """负向锚：box_state gt 1 恒 False（值域 {0.0,1.0}——docstring
        警示的行为面）；样例含混合形态保证检出力。"""
        from core.strategy.executor import evaluate_condition
        df = self._box_df()
        state = self._state_series(df)
        assert df["close"].notna().all(), "构造器索引对齐假绿防线"
        assert (state == 1.0).any() and (state == 0.0).any(), \
            "样例须混合箱体/非箱体（检出力）"
        gt1 = evaluate_condition(state, df["close"],
                                 {"type": "gt", "threshold": 1})
        assert not gt1.any()
        # spec R1 布尔条件语义 THEN：「gt 1 恒 False」警示入 docstring
        from core.features import structure_indicators as si
        doc = si.compute_box_regime.__doc__ or ""
        assert "gt 1" in doc and "恒 False" in doc, \
            "docstring 须携带 gt 1 恒 False 勿踩警示（披露义务）"

    def test_within_bars_after_bool_subject(self):
        """within_bars_after 布尔 subject（1/0）时序锚：inner=eq 1、
        window=3——state 脉冲 [0,1,0,0,0,1] 在脉冲后 3 根内为真（不含
        当根）。"""
        from core.strategy.executor import evaluate_condition
        idx = pd.date_range("2024-01-01", periods=8, freq="1h", tz="UTC",
                            name="timestamp")
        state = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
                          index=idx)
        close = pd.Series(100.0, index=idx)
        out = evaluate_condition(
            state, close,
            {"type": "within_bars_after", "window": 3,
             "inner": {"type": "eq", "threshold": 1}})
        # 脉冲 t=1 → t=2,3,4 真（近 3 根内触发过，不含当根）
        # 脉冲 t=5 → t=6,7 真（t=8 不存在）
        expect = [False, False, True, True, True, False, True, True]
        assert list(out) == expect

    def test_warmup_720_and_expansion(self):
        """warmup 数值锚 720（=max(200, window max 240×3)）＋白名单
        存在性断言先行（漏扩先红直达根因）。"""
        from core.analysis.registry_meta import (
            _PERIOD_LIKE_PARAMS, derive_warmup)
        assert {"window"} <= _PERIOD_LIKE_PARAMS
        assert derive_warmup(["BoxRegime"]) == 720

    def test_insufficient_warmup_error_carries_actual(self):
        """装载不足报错 detail 携带实算 warmup_bars=720（真实数据）。"""
        if not (MARKET / "BTCUSDT_1h.parquet").exists():
            pytest.skip("真实数据不在库（data/market_fapi）")
        from core.analysis.indicator_query import query_indicator
        from core.analysis.errors import ResearchError
        head = pd.read_parquet(MARKET / "BTCUSDT_1h.parquet").index[100]
        with pytest.raises(ResearchError) as ei:
            query_indicator(
                MARKET, symbol="BTCUSDT", timeframe="1h",
                indicator="BoxRegime",
                params={"window": 120, "width_thr": 0.08, "min_len": 48},
                start=str(head))
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"
        assert ei.value.detail["warmup_bars"] == 720


# ----------------------------------------------------------- 2.2 BoxChannel 五列契约

class TestBoxChannelContract:
    """BC1 分位沿 shift(1)/BC2 防自引用严一档不变量/demo 同构与
    break_low 数学等价/buffer=0 退化。"""

    PARAMS = {"edge_window": 240, "q_low": 0.10, "q_high": 0.90,
              "buffer": 0.25}

    @staticmethod
    def _chan_df(n=60, seed=5):
        """close 用 ndarray 构造（Series 带 RangeIndex 进 DatetimeIndex
        DataFrame 会索引对齐全 NaN——NaN 对 NaN 的 assert_series_equal
        假绿坑，各重演用例带非空检出断言）。"""
        rng = np.random.default_rng(seed)
        close = 100 + rng.normal(0, 3, n).cumsum()
        idx = pd.date_range("2024-01-01", periods=n, freq="1h",
                            tz="UTC", name="timestamp")
        return pd.DataFrame({
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1.0,
        }, index=idx)

    def _cols(self, df, params=None):
        return _compute_indicator(df, "BoxChannel", params or self.PARAMS)

    def test_bc1_quantile_shift_manual(self):
        """(90,110) 振荡、W=2 手算锚：quantile(0.9,[90,110])=108（linear
        插值）、quantile(0.1)=92；shift(1) 首 2 根 NaN。"""
        closes = [90.0, 110.0] * 6
        df = _mk_df(closes)
        p = {"edge_window": 2, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        out = self._cols(df, p)
        upper = out["boxch_2_0.1_0.9_0.25_upper"]
        lower = out["boxch_2_0.1_0.9_0.25_lower"]
        assert np.isnan(upper.iloc[0]) and np.isnan(upper.iloc[1]), \
            "shift(1) 首根 NaN"
        assert upper.iloc[2] == pytest.approx(108.0)
        assert lower.iloc[2] == pytest.approx(92.0)
        assert upper.iloc[-1] == pytest.approx(108.0)

    def test_bc1_independent_replay_five_columns(self):
        """五列独立重演（demo r9_d3_box.py 公式原式，期望不经被测内核）：
        q 分位 shift(1)、channel_width shift(1) 分母、break±buffer×span。"""
        df = self._chan_df(200)
        p = {"edge_window": 50, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        out = self._cols(df, p)
        assert df["close"].notna().all(), "构造器索引对齐假绿防线"
        assert out["boxch_50_0.1_0.9_0.25_upper"].notna().sum() > 100
        q10 = df["close"].rolling(50).quantile(0.10).shift(1)
        q90 = df["close"].rolling(50).quantile(0.90).shift(1)
        span = q90 - q10
        expect = {
            "upper": q90, "lower": q10,
            "channel_width": (q90 - q10) / df["close"].shift(1),
            "break_high": q90 + 0.25 * span,
            "break_low": q10 - 0.25 * span,
        }
        for f, exp in expect.items():
            pd.testing.assert_series_equal(
                out[f"boxch_50_0.1_0.9_0.25_{f}"], exp, check_names=False)

    def test_bc2_strict_no_self_reference(self):
        """BC2 严一档不变量：修改当根 t 的收盘不影响 t 根沿/宽/破坏列
        值（shift(1) 前一根取值——t+1 根可变，t 根不变）。"""
        df = self._chan_df(80)
        p = {"edge_window": 10, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        a = self._cols(df, p)
        assert a["boxch_10_0.1_0.9_0.25_upper"].notna().sum() > 60, \
            "构造器索引对齐假绿防线"
        t = 40
        df2 = df.copy()
        df2.iloc[t, df2.columns.get_loc("close")] = 999.0
        b = self._cols(df2, p)
        for col in a:
            assert b[col].iloc[t] == a[col].iloc[t], col
        # t+1 根允许变（rolling 窗含 t）——对照组（证明数据确实改了）
        diff = [c for c in a if b[c].iloc[t + 1] != a[c].iloc[t + 1]]
        assert diff, "改 t 根收盘应影响 t+1 根（shift 语义对照）"

    def test_channel_width_shift_denominator_anchor(self):
        """channel_width 分母 shift(1) 差异标注锚：(90,110) 振荡下
        t=3 处分母=close[2]=90（当根分母形制（demo box_ok 口径）会取
        close[3]=110——列值=16/90 证 shift 分母）。"""
        closes = [90.0, 110.0] * 5
        df = _mk_df(closes)
        p = {"edge_window": 2, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        out = self._cols(df, p)
        cw = out["boxch_2_0.1_0.9_0.25_channel_width"]
        assert cw.iloc[3] == pytest.approx(16.0 / 90.0)

    def test_break_lines_and_buffer_zero_degenerate(self):
        """break=沿±buffer×(upper−lower) 逐 bar 断言＋buffer=0 退化
        （break_low=lower、break_high=upper 逐位）。"""
        df = self._chan_df(120)
        p = {"edge_window": 20, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        out = self._cols(df, p)
        upper = out["boxch_20_0.1_0.9_0.25_upper"]
        lower = out["boxch_20_0.1_0.9_0.25_lower"]
        bh = out["boxch_20_0.1_0.9_0.25_break_high"]
        bl = out["boxch_20_0.1_0.9_0.25_break_low"]
        span = upper - lower
        pd.testing.assert_series_equal(bh, upper + 0.25 * span,
                                       check_names=False)
        pd.testing.assert_series_equal(bl, lower - 0.25 * span,
                                       check_names=False)
        p0 = {**p, "buffer": 0.0}
        out0 = self._cols(df, p0)
        pd.testing.assert_series_equal(
            out0["boxch_20_0.1_0.9_0.0_break_low"],
            out0["boxch_20_0.1_0.9_0.0_lower"], check_names=False)
        pd.testing.assert_series_equal(
            out0["boxch_20_0.1_0.9_0.0_break_high"],
            out0["boxch_20_0.1_0.9_0.0_upper"], check_names=False)

    def test_demo_sl_line_isomorphic_and_equivalence(self):
        """demo 同构断言：q 0.10/0.90/buffer 0.25 下 break_low 逐位=
        demo sl_line 公式值（q10−0.25×(q90−q10) 独立重演）；break_low
        [e−1] 与 demo 入场冻结线数学等价算例（ruler_compare e−1 冻结
        场景：close=[100,200,150…]、W=1 → break_low[1]=100=sl_line）。"""
        df = self._chan_df(150)
        p = {"edge_window": 30, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        out = self._cols(df, p)
        assert out["boxch_30_0.1_0.9_0.25_break_low"].notna().sum() > 100, \
            "构造器索引对齐假绿防线"
        q10 = df["close"].rolling(30).quantile(0.10).shift(1)
        q90 = df["close"].rolling(30).quantile(0.90).shift(1)
        sl_line = q10 - 0.25 * (q90 - q10)  # demo r9_d3_box.py 原式
        pd.testing.assert_series_equal(
            out["boxch_30_0.1_0.9_0.25_break_low"], sl_line,
            check_names=False)
        # e−1 等价算例（W=1：沿=前一根收盘）
        df2 = _mk_df([100.0, 200.0] + [150.0] * 5)
        out2 = self._cols(df2, {"edge_window": 1, "q_low": 0.10,
                                "q_high": 0.90, "buffer": 0.25})
        bl = out2["boxch_1_0.1_0.9_0.25_break_low"]
        # 入场 e=2 → e−1=1：break_low[1]=100−0.25×(100−100)=100
        assert bl.iloc[1] == pytest.approx(100.0)


# ----------------------------------------------------------- 2.3 BoxChannel 参数与八动作

class TestBoxChannelParams:
    """cross_params/q 值域/buffer 域拒绝＋八动作族五面＋warmup 1440。"""

    PARAMS = {"edge_window": 240, "q_low": 0.10, "q_high": 0.90,
              "buffer": 0.25}

    def test_cross_params_hook_and_integration_reject(self):
        """q_low≥q_high 拒绝双面：cross_params 钩子单元（返回拒绝消息，
        SessionRange 先例形制）＋集成面 422（分域值域 (0.01,0.49)/
        (0.51,0.99) 使域校验先拦——钩子在登记链上为防御性冗余，钩子
        行为由单元断言独立锁定）。"""
        hook = INDICATOR_REGISTRY["BoxChannel"].cross_params
        assert hook is not None
        msg = hook({"q_low": 0.4, "q_high": 0.3, "edge_window": 240,
                    "buffer": 0.25})
        assert msg is not None and "q_low" in msg
        assert hook({"q_low": 0.1, "q_high": 0.9, "edge_window": 240,
                     "buffer": 0.25}) is None
        # 集成面：q_low≥q_high 的传参（如 0.9/0.5）落 422 结构化拒绝
        with pytest.raises(ColumnRefError) as ei:
            _resolve_indicator_ref({
                "indicator": "BoxChannel",
                "params": {"edge_window": 240, "q_low": 0.9,
                           "q_high": 0.5, "buffer": 0.25},
                "field": "upper"})
        assert ei.value.code == "PARAM_INVALID"

    @pytest.mark.parametrize(
        ("params", "key"),
        [pytest.param({"q_low": 0.005}, "q_low", id="q_low 下界外"),
         pytest.param({"q_low": 0.50}, "q_low", id="q_low 上界外"),
         pytest.param({"q_high": 0.50}, "q_high", id="q_high 下界外"),
         pytest.param({"q_high": 0.995}, "q_high", id="q_high 上界外"),
         pytest.param({"buffer": -0.1}, "buffer", id="buffer 负值"),
         pytest.param({"buffer": 1.1}, "buffer", id="buffer 上界外"),
         pytest.param({"edge_window": 19}, "edge_window",
                      id="edge_window 下界外")])
    def test_param_domain_rejected(self, params, key):
        base = dict(self.PARAMS)
        base.update(params)
        with pytest.raises(ColumnRefError) as ei:
            _resolve_indicator_ref({"indicator": "BoxChannel",
                                    "params": base, "field": "upper"})
        assert ei.value.code == "PARAM_INVALID"
        assert key in ei.value.detail.get("key", "")

    @staticmethod
    def _eval_df():
        """八动作求值构造：平台期后下跌回穿的确定性序列（平台期供
        edge_window=20 窗收敛）。"""
        closes = [100.0] * 25 + [95.0, 90.0, 88.0, 90.0, 95.0, 100.0,
                                 102.0] + [103.0] * 28
        return _mk_df(closes)

    def test_eight_actions_evaluate_family(self):
        """八动作族求值面全通（gt/lt/price_above/price_below/
        cross_above_series/cross_below_series/touch_bounce/
        role_reversal）。"""
        from core.strategy.executor import evaluate_condition
        df = self._eval_df()
        p = {"edge_window": 5, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        out = _compute_indicator(df, "BoxChannel", p)
        upper = out["boxch_5_0.1_0.9_0.25_upper"]
        lower = out["boxch_5_0.1_0.9_0.25_lower"]
        bh = out["boxch_5_0.1_0.9_0.25_break_high"]
        bl = out["boxch_5_0.1_0.9_0.25_break_low"]
        close = df["close"]
        # gt/lt：阈值数值比较
        assert (evaluate_condition(upper, close, {"type": "gt",
                                                  "threshold": 0.0})
                == (upper > 0.0)).all()
        assert (evaluate_condition(lower, close, {"type": "lt",
                                                  "threshold": 1e9})
                == (lower < 1e9)).all()
        # price_above/price_below：close 与沿比较
        assert (evaluate_condition(upper, close, {"type": "price_above"})
                == (close > upper)).all()
        assert (evaluate_condition(lower, close, {"type": "price_below"})
                == (close < lower)).all()
        # cross_above_series/cross_below_series：close 对沿的穿越
        ca = evaluate_condition(
            close, close, {"type": "cross_above_series",
                           "target_indicator": "BoxChannel",
                           "target_params": p, "target_field": "lower"},
            df=df)
        expect_ca = ((close.shift(1) < lower.shift(1))
                     & (close >= lower)).fillna(False)
        assert list(ca) == list(expect_ca)
        assert ca.any(), "回穿序列须有至少一次对 lower 上穿"
        cb = evaluate_condition(
            close, close, {"type": "cross_below_series",
                           "target_indicator": "BoxChannel",
                           "target_params": p, "target_field": "upper"},
            df=df)
        expect_cb = ((close.shift(1) > upper.shift(1))
                     & (close <= upper)).fillna(False)
        assert list(cb) == list(expect_cb)
        # touch_bounce/role_reversal（支撑/阻力沿语义——df 提供高低价）
        tb = evaluate_condition(lower, close, {"type": "touch_bounce",
                                               "direction": "support"},
                                df=df)
        assert isinstance(tb, pd.Series) and len(tb) == len(df)
        rr = evaluate_condition(lower, close, {"type": "role_reversal"},
                                df=df)
        assert isinstance(rr, pd.Series) and len(rr) == len(df)

    def test_bc3_break_judgment_three_cases(self):
        """BC3 破坏判定三算例：①close price_below break_low 收盘判定
        破线；②buffer 线恰触（=）不破/恰未触不破/越过即破两侧；
        ③close 对 lower 的 cross_above_series 回穿确认时序锚（target
        现算经域校验——用域内 edge_window=20，期望独立重演）。"""
        from core.strategy.executor import evaluate_condition
        # ①+②：W=1 单元素窗 → upper=lower=close[t−1]（box_h=0、
        # buffer 项=0）→ break_low[t]=close[t−1]（逐 bar 滚动语义——
        # 与冻结线分界：冻结语义在消费侧取 e−1 根列值，见 ruler_compare）。
        # close=[100,200,95,100,101,90]：bl=[NaN,100,200,95,100,101]；
        # close<bl 逐 bar → below=[F,F,T,F,F,T]（bar2 95<200 破、
        # bar5 90<101 破；bar3 100>95、bar4 101>100 不破）
        df = _mk_df([100.0, 200.0, 95.0, 100.0, 101.0, 90.0])
        p1 = {"edge_window": 1, "q_low": 0.10, "q_high": 0.90,
              "buffer": 0.25}
        out = _compute_indicator(df, "BoxChannel", p1)
        bl = out["boxch_1_0.1_0.9_0.25_break_low"]
        assert bl.iloc[2] == pytest.approx(200.0)
        below = evaluate_condition(bl, df["close"],
                                   {"type": "price_below"})
        assert list(below) == [False, False, True, False, False, True]
        # 恰触（close==break_low 不破——严格 <）/恰未触（线上方）/
        # 破线三侧：bl=[NaN,100,100,101]、close=[100,100,101,99]
        df_t = _mk_df([100.0, 100.0, 101.0, 99.0])
        bl_t = _compute_indicator(df_t, "BoxChannel", p1)[
            "boxch_1_0.1_0.9_0.25_break_low"]
        below_t = evaluate_condition(bl_t, df_t["close"],
                                     {"type": "price_below"})
        assert list(below_t) == [False, False, False, True], \
            "恰触（=）不破、恰未触（线上方）不破、低于线破"
        # ③回穿确认时序：前根收在 lower 下、当根收回线上 → 当根 True
        p20 = {"edge_window": 20, "q_low": 0.10, "q_high": 0.90,
               "buffer": 0.25}
        df2 = self._eval_df()
        out2 = _compute_indicator(df2, "BoxChannel", p20)
        lower2 = out2["boxch_20_0.1_0.9_0.25_lower"]
        assert lower2.notna().sum() > 5, "回穿锚检出力（列非全 NaN）"
        ca = evaluate_condition(
            df2["close"], df2["close"],
            {"type": "cross_above_series", "target_indicator": "BoxChannel",
             "target_params": p20, "target_field": "lower"}, df=df2)
        expect = ((df2["close"].shift(1) < lower2.shift(1))
                  & (df2["close"] >= lower2)).fillna(False)
        assert list(ca) == list(expect)
        assert ca.any(), "回穿序列须有至少一次对 lower 上穿"

    def test_warmup_1440_and_expansion(self):
        """warmup 数值锚 1440（=max(200, edge_window max 480×3)）＋
        白名单存在性断言（漏扩先红直达根因）。"""
        from core.analysis.registry_meta import (
            _PERIOD_LIKE_PARAMS, derive_warmup)
        assert {"window", "edge_window"} <= _PERIOD_LIKE_PARAMS
        assert derive_warmup(["BoxChannel"]) == 1440

    def test_face_condition_stats(self):
        """condition_stats 面八动作族样本（price_above 经结构化 subject，
        域内参数）。"""
        from core.analysis.templates.condition_stats import condition_stats
        p = {"edge_window": 20, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        rng = np.random.default_rng(9)
        n = 120
        close = 100 + rng.normal(0, 3, n).cumsum()
        df = _mk_df(close.tolist())
        out = _compute_indicator(df, "BoxChannel", p)
        upper = out["boxch_20_0.1_0.9_0.25_upper"]
        res = condition_stats(
            df, indicator="BoxChannel", params=p,
            condition={"subject": {"indicator": "BoxChannel",
                                   "params": p, "field": "upper"},
                       "action": "price_above", "target": 0},
            data_ranges=[{"symbol": "T", "timeframe": "1h",
                          "start": "2024-01-01", "end": "2024-01-06"}],
            min_samples=1).to_dict()
        pl = res["payload"]
        assert pl["hit_count"] == int((df["close"] > upper).sum()), \
            "hit_count=统计段总命中（dropped 为其中尾部子集）"


# ----------------------------------------------------------- 5.1 两族分界（X-A/X-B）

class TestCaliberBoundary:
    """逐列口径分界总则断言：X-A 当根收盘突变确定样例＋X-B 不可互换锚。"""

    def test_xa_current_bar_shock_divergence(self):
        """X-A：同一数据当根收盘突变——结构列（box_state/box_width）
        可变、基准列（upper/lower）不变（两族信息集差异的可观察证明）。"""
        closes = [100.0 + i * 0.5 for i in range(30)]
        df = _mk_df(closes)
        reg_p = {"window": 5, "width_thr": 0.08, "min_len": 4}
        ch_p = {"edge_window": 5, "q_low": 0.10, "q_high": 0.90,
                "buffer": 0.25}
        t = len(df) - 1
        df2 = df.copy()
        df2.iloc[t, df2.columns.get_loc("close")] = 500.0  # 当根收盘突变
        ra = _compute_indicator(df, "BoxRegime", reg_p)
        rb = _compute_indicator(df2, "BoxRegime", reg_p)
        ca = _compute_indicator(df, "BoxChannel", ch_p)
        cb = _compute_indicator(df2, "BoxChannel", ch_p)
        # 结构列可变（rolling 含当根——mid/width 随当根收盘变）
        assert (rb["boxreg_5_0.08_4_box_width"].iloc[t]
                != ra["boxreg_5_0.08_4_box_width"].iloc[t])
        # 基准列不变（shift(1) 前一根取值）
        for col in ca:
            assert cb[col].iloc[t] == ca[col].iloc[t], col

    def test_xb_not_interchangeable(self):
        """X-B 不可互换锚：两件输出结构差异断言＋docstring 语义分诊
        互指披露（识别件 vs 沿通道件）。"""
        from core.features import structure_indicators as si
        reg_doc = si.compute_box_regime.__doc__ or ""
        ch_doc = si.compute_box_channel.__doc__ or ""
        # 互指分诊：识别件 docstring 指沿通道件、反之亦然
        assert "BoxChannel" in reg_doc and "不可互换" in reg_doc
        assert "BoxRegime" in ch_doc and "不可互换" in ch_doc
        # 输出结构差异：三列（布尔+数值）vs 五列（价格基准）
        assert INDICATOR_REGISTRY["BoxRegime"].output_fields == \
            ["box_state", "box_width", "box_established"]
        assert INDICATOR_REGISTRY["BoxChannel"].output_fields == \
            ["upper", "lower", "channel_width", "break_high", "break_low"]
        assert set(INDICATOR_REGISTRY["BoxRegime"].output_fields) \
            .isdisjoint(INDICATOR_REGISTRY["BoxChannel"].output_fields)


# --------------------------------------------- 补网：BoxRegime 参数域（值域归设计卡）

class TestBoxRegimeParamDomain:
    """BoxRegime 参数域拒绝（spec R1「值域与默认归设计卡」：
    window 20-240/width_thr 0.01-0.30/min_len 10-200——域外经登记链
    422 结构化拒绝；与 BoxChannel test_param_domain_rejected 同形制）。"""

    BASE = {"window": 120, "width_thr": 0.08, "min_len": 48}

    @pytest.mark.parametrize(
        ("params", "key"),
        [pytest.param({"window": 19}, "window", id="window 下界外"),
         pytest.param({"window": 241}, "window", id="window 上界外"),
         pytest.param({"width_thr": 0.005}, "width_thr",
                      id="width_thr 下界外"),
         pytest.param({"width_thr": 0.31}, "width_thr",
                      id="width_thr 上界外"),
         pytest.param({"min_len": 9}, "min_len", id="min_len 下界外"),
         pytest.param({"min_len": 201}, "min_len", id="min_len 上界外")])
    def test_param_domain_rejected(self, params, key):
        base = dict(self.BASE)
        base.update(params)
        with pytest.raises(ColumnRefError) as ei:
            _resolve_indicator_ref({"indicator": "BoxRegime",
                                    "params": base, "field": "box_state"})
        assert ei.value.code == "PARAM_INVALID"
        assert key in ei.value.detail.get("key", "")

    def test_param_keyset_exactly_contract_three(self):
        """参数键集恰为契约三键（值域面之外的键层面锁——多余参数
        不得静默混入）。"""
        assert set(INDICATOR_REGISTRY["BoxRegime"].params) == \
            {"window", "width_thr", "min_len"}


# --------------------------------------------- 补网：BoxChannel 五面补齐（缺三面）

class TestBoxChannelRemainingFaces:
    """BoxChannel 五面补齐：query_indicator 归约/假设登记＋翻译
    （condition_stats 与回测求值两面由 TestBoxChannelParams 承载——
    行动指令「五面全通」的两件对等要求）。"""

    def test_face_query_indicator_reductions(self):
        """面一 query_indicator：latest 归约五列全算＋warmup_bars=1440
        数值锚（真实数据；不在库 skip——demo 复算先例。三归约全带超
        归约摘要字节上限 2048——RESEARCH_PARAM_INVALID 既有约束，
        五列件取轻档；quantiles/slope 归约机制为全指标通用面，由
        BoxRegime 版用例承载）。"""
        if not (MARKET / "BTCUSDT_1h.parquet").exists():
            pytest.skip("真实数据不在库（data/market_fapi）")
        from core.analysis.indicator_query import query_indicator
        p = {"edge_window": 240, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        out = query_indicator(
            MARKET, symbol="BTCUSDT", timeframe="1h",
            indicator="BoxChannel", params=p,
            start="2026-09-01", end="2026-11-01",
            reductions=["latest"])
        assert out["indicator"] == "BoxChannel"
        assert out["warmup_bars"] == 1440
        reds = out["reductions"]["boxch_240_0.1_0.9_0.25_channel_width"]
        assert set(reds) == {"latest"}

    def test_face_register_and_translate(self, tmp_path):
        """面三/四 假设登记＋翻译：BoxChannel price_below break_low
        条件（D2 破箱认错语义）登记入册并翻译为 SignalGene。"""
        from core.analysis.hypothesis_to_strategy import build_signal_genes
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables

        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        p = {"edge_window": 240, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        cond = {"subject": {"indicator": "BoxChannel", "field": "break_low",
                            "params": p},
                "action": "price_below", "target": 0}
        then = {"subject": {"indicator": "RSI", "params": {"period": 14}},
                "action": "gt", "target": 50}
        genes, _sources = build_signal_genes(
            [cond], [then], db, strategy_timeframe="1h")
        g = genes[0]
        assert g.indicator == "BoxChannel" and g.field_name == "break_low"
        assert g.params == p


# --------------------------------------------- 补网：资格宽经 threshold 承载（R2）

class TestQualificationWidthViaThreshold:
    """资格宽分界（spec R2 条件族 scenario THEN）：资格宽阈值不进
    指标参数——由策略条件 threshold 承载（channel_width lt 阈值）。"""

    def test_param_keyset_excludes_qualification_width(self):
        """BoxChannel 参数键集恰四键——资格宽阈值不在指标参数
        （不进参数的直接断言，非仅间接锁定）。"""
        assert set(INDICATOR_REGISTRY["BoxChannel"].params) == \
            {"edge_window", "q_low", "q_high", "buffer"}

    def test_channel_width_lt_via_strategy_threshold(self):
        """channel_width lt 阈值经 evaluate_condition threshold 承载：
        求值结果与独立重演 (channel_width < thr) 逐位一致——样例保证
        两侧（收窄/未收窄）都命中（检出力）。"""
        from core.strategy.executor import evaluate_condition
        rng = np.random.default_rng(13)
        n = 160
        close = 100 + rng.normal(0, 3, n).cumsum()
        df = _mk_df(close.tolist())
        p = {"edge_window": 20, "q_low": 0.10, "q_high": 0.90,
             "buffer": 0.25}
        out = _compute_indicator(df, "BoxChannel", p)
        cw = out["boxch_20_0.1_0.9_0.25_channel_width"]
        assert cw.notna().sum() > 100, "构造器索引对齐假绿防线"
        thr = float(cw.dropna().median())
        got = evaluate_condition(cw, df["close"], {"type": "lt",
                                                   "threshold": thr})
        expect = (cw < thr).fillna(False)
        assert list(got.fillna(False)) == list(expect)
        assert expect.any() and not expect.all(), \
            "样例须两侧都命中（资格宽收窄/未收窄——检出力）"


# ----------------------------------------------------------- 属性补强（闸门步骤4）

class TestPropertyReinforcement:
    """纯计算面不变量（TESTING_V3「不变量与属性」法——随机采样
    参数/数据域，一类输入应保持的关系；采样确定性 seed 固定）。"""

    @staticmethod
    def _sample_params(rng, n=12):
        """域内确定性采样（float 取两位小数保证 str 编码往返精确）。"""
        reg = [{"window": int(rng.integers(20, 241)),
                "width_thr": round(float(rng.uniform(0.01, 0.30)), 2),
                "min_len": int(rng.integers(10, 201))}
               for _ in range(n)]
        ch = [{"edge_window": int(rng.integers(20, 481)),
               "q_low": round(float(rng.uniform(0.01, 0.49)), 2),
               "q_high": round(float(rng.uniform(0.51, 0.99)), 2),
               "buffer": round(float(rng.uniform(0.0, 1.0)), 2)}
              for _ in range(n)]
        return reg, ch

    def test_naming_roundtrip_property(self):
        """命名三层往返性质：域内随机参数 × 全输出 field——resolve
        正向 == encode 编码 ∈ 产列自拼集合，decode 逆解码还原
        {indicator, field, params}（比默认单组的既有锚域更宽）。"""
        rng = np.random.default_rng(21)
        reg_params, ch_params = self._sample_params(rng)
        df = _mk_df([100.0] * 12, [101.0] * 12, [99.0] * 12)
        for name, samples in (("BoxRegime", reg_params),
                              ("BoxChannel", ch_params)):
            for params in samples:
                produced = set(_compute_indicator(df, name, params))
                for f in INDICATOR_REGISTRY[name].output_fields:
                    forward = resolve_indicator_column(name, params, f)
                    assert forward == encode_column_ref(name, params, f)
                    assert forward in produced
                    decoded = decode_column_ref(forward)
                    assert decoded["indicator"] == name
                    assert decoded["field"] == f
                    assert decoded["params"] == params

    def test_cross_params_hook_uniform_judgment_property(self):
        """cross_params 钩子归一面性质：域内随机 q_low<q_high 全通过、
        镜像倒挂全拒绝（含等值）——判据与采样无关的一致性。"""
        rng = np.random.default_rng(22)
        _, ch_params = self._sample_params(rng, n=20)
        hook = INDICATOR_REGISTRY["BoxChannel"].cross_params
        for p in ch_params:
            assert p["q_low"] < p["q_high"]
            assert hook(p) is None, p
            inverted = dict(p, q_low=p["q_high"], q_high=p["q_low"])
            assert hook(inverted) is not None, inverted
            assert hook({**p, "q_low": p["q_high"]}) is not None  # 等值

    def test_box_regime_channel_ordering_property(self):
        """BoxRegime 自洽不变量（任意随机数据 width≤thr ⇔ state=1；
        established=1 ⇒ state=1）＋BoxChannel 分位单调与破坏线包络
        （upper ≥ lower、break_high ≥ upper ≥ lower ≥ break_low——
        buffer≥0 下恒成立）。"""
        reg_p = {"window": 8, "width_thr": 0.08, "min_len": 4}
        ch_p = {"edge_window": 8, "q_low": 0.10, "q_high": 0.90,
                "buffer": 0.25}
        for seed in (31, 32, 33):
            rng = np.random.default_rng(seed)
            n = 90
            close = 100 + rng.normal(0, 2, n).cumsum()
            spread = rng.uniform(0.5, 9, n)
            df = pd.DataFrame({
                "open": close, "high": close + spread, "low": close - spread,
                "close": close, "volume": 1.0,
            }, index=pd.date_range("2024-01-01", periods=n, freq="1h",
                                   tz="UTC", name="timestamp"))
            r = _compute_indicator(df, "BoxRegime", reg_p)
            state = r["boxreg_8_0.08_4_box_state"]
            width = r["boxreg_8_0.08_4_box_width"]
            est = r["boxreg_8_0.08_4_box_established"]
            assert state.isin([0.0, 1.0]).all()
            assert ((width <= 0.08).fillna(False).astype(float)
                    == state).all(), "width≤thr ⇔ state=1 自洽"
            assert not ((est == 1.0) & (state == 0.0)).any(), \
                "established ⇒ state（成熟态蕴含当根箱体态）"
            c = _compute_indicator(df, "BoxChannel", ch_p)
            upper = c["boxch_8_0.1_0.9_0.25_upper"]
            lower = c["boxch_8_0.1_0.9_0.25_lower"]
            bh = c["boxch_8_0.1_0.9_0.25_break_high"]
            bl = c["boxch_8_0.1_0.9_0.25_break_low"]
            valid = upper.notna() & lower.notna()
            assert (upper[valid] >= lower[valid]).all(), \
                "分位单调：q_high 分位 ≥ q_low 分位"
            assert ((bh[valid] >= upper[valid]) & (lower[valid] >= bl[valid])
                    ).all(), "buffer≥0 破坏线包络：break_high≥upper、lower≥break_low"
