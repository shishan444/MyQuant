"""指标读数服务测试（add-query-indicator 任务 3.1）。

覆盖五区：归约四类（pandas_ta / numpy 独立直算对照——T1 铁律：禁止
调被测内核生成期望）、参数与校验链（多余键拒绝防内核静默忽略）、
求值域与装载（warmup 段不入归约的敏感构造——T2：断言差非零）、
多输出列与 null 语义（B 裁决：列级判空，monkeypatch 构造——标准
装载下 warmup 200 保护使冷启动差不会自然出现，null 是防御性契约）、
体积自检。

fixture 沿用 0.2.8 形制（合成数据 + seed 固定 + tmp 目录）；零 db
（零留痕通道签名无 db_path）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_ta as ta
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.indicator_query import query_indicator
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"
WARMUP_RSI = 200  # derive_warmup 下限保底（RSI period<67 时恒 200）


@pytest.fixture()
def env(tmp_path: Path):
    """800 bar 1d 数据（seed=13——四态 regime 天然出现）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=800, freq="1d", start="2023-06-01", seed=13)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    return {"data_dir": data_dir, "df": df}


def _call(env, **kwargs):
    base = {"symbol": SYMBOL, "timeframe": "1d", "indicator": "RSI"}
    base.update(kwargs)
    return query_indicator(env["data_dir"], **base)


def _ta_rsi(env, period: int) -> pd.Series:
    """独立推导（T1）：pandas_ta 直算，不经过被测内核。"""
    return ta.rsi(env["df"]["close"], length=period)


def _domain_slice(env, period: int) -> pd.Series:
    """求值域内的独立 RSI 序列（缺省装载：全量 - warmup 200 段）。"""
    full = _ta_rsi(env, period)
    return full.iloc[WARMUP_RSI:].dropna()


class TestReductions:
    def test_latest_matches_pandas_ta_tail(self, env):
        out = _call(env, params={"period": 14}, reductions=["latest"])
        got = out["reductions"]["rsi_14"]["latest"]
        expect = _domain_slice(env, 14)
        assert got["value"] == pytest.approx(expect.iloc[-1], rel=1e-6)
        assert got["timestamp"] == expect.index[-1].isoformat()

    def test_quantiles_match_independent_np(self, env):
        out = _call(env, params={"period": 14}, reductions=["quantiles"])
        got = out["reductions"]["rsi_14"]["quantiles"]
        vals = _domain_slice(env, 14).values
        for pt, key in zip((1, 5, 25, 50, 75, 95, 99),
                           ("p01", "p05", "p25", "p50", "p75", "p95", "p99")):
            assert got[key] == pytest.approx(
                float(np.percentile(vals, pt)), rel=1e-6), key

    def test_quantiles_exclude_warmup_sensitive(self, env):
        """T2 敏感构造：含冷启动段的归约必然不同（断言差非零），
        服务层结果 = 排除后口径。"""
        out = _call(env, params={"period": 14}, reductions=["quantiles"])
        got = out["reductions"]["rsi_14"]["quantiles"]["p50"]
        full = _ta_rsi(env, 14).dropna().values  # 含 warmup 段（若误入）
        with_warmup = float(np.percentile(full, 50))
        assert got != pytest.approx(with_warmup, rel=1e-9), \
            "归约疑似含 warmup 段（构造落在不敏感区）"
        assert got == pytest.approx(
            float(np.percentile(_domain_slice(env, 14).values, 50)), rel=1e-6)

    def test_regime_means_four_states_with_counts(self, env):
        out = _call(env, reductions=["regime_means"])
        got = out["reductions"]["rsi_14"]["regime_means"]
        # 四态齐备（T4：fixture 数据保证；分组键跟随 classify_series
        # 权威输出——S2 锚，非硬编码两态）
        assert set(got) == {"趋势", "过渡", "震荡", "高波动"}
        total = sum(g["count"] for g in got.values())
        assert 0 < total <= out["range"]["bars"]  # NaN 态落组外如实缩小
        for g in got.values():
            assert g["count"] > 0 and 0.0 <= g["mean"] <= 100.0

    def test_trend_slope_matches_polyfit(self, env):
        out = _call(env, reductions=["trend_slope"])
        got = out["reductions"]["rsi_14"]["trend_slope"]
        vals = _domain_slice(env, 14).values.astype(float)
        expect = float(np.polyfit(np.arange(len(vals)), vals, 1)[0])
        assert got["slope_per_bar"] == pytest.approx(expect, rel=1e-6)
        assert got["n_used"] == len(vals)

    def test_param_sensitivity_38_vs_14(self, env):
        """规划验收锚：RSI(38) 与 RSI(14) 同区间结果不同。"""
        r38 = _call(env, params={"period": 38})
        r14 = _call(env, params={"period": 14})
        assert r38["reductions"]["rsi_38"]["quantiles"]["p50"] != \
            pytest.approx(r14["reductions"]["rsi_14"]["quantiles"]["p50"],
                          rel=1e-9)
        # 且各自与独立计算一致（p50 抽查）
        v38 = _ta_rsi(env, 38).iloc[WARMUP_RSI:].dropna().values
        assert r38["reductions"]["rsi_38"]["quantiles"]["p50"] == \
            pytest.approx(float(np.percentile(v38, 50)), rel=1e-6)


class TestValidation:
    def test_indicator_unknown(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, indicator="NOPE")
        assert ei.value.code == "RESEARCH_INDICATOR_UNKNOWN"

    def test_param_out_of_range(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, params={"period": 99})  # RSI 注册表上限 50
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_param_extra_key_rejected(self, env):
        """S2 锚：内核静默忽略未知键，服务层必须拦截（假象成功防线）。"""
        with pytest.raises(ResearchError) as ei:
            _call(env, params={"period": 14, "bogus": 1})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "bogus" in str(ei.value.detail.get("illegal_keys"))

    def test_param_unparsable(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, params={"period": "abc"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_reduction_unknown(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, reductions=["latest", "nope"])
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert sorted(ei.value.detail["legal_values"]) == \
            ["latest", "quantiles", "regime_means", "trend_slope"]

    def test_reductions_default(self, env):
        out = _call(env)
        assert set(out["reductions"]["rsi_14"]) == {"latest", "quantiles"}

    def test_params_default_echo(self, env):
        """Q1 裁决：缺省拼注册表 default，echo 披露实际生效值。"""
        out = _call(env)
        assert out["params"] == {"period": 14}

    def test_validation_order_indicator_first(self, env):
        """固定校验顺序（D7）：在册先于参数——同错时报在册。"""
        with pytest.raises(ResearchError) as ei:
            _call(env, indicator="NOPE", params={"bogus": 1})
        assert ei.value.code == "RESEARCH_INDICATOR_UNKNOWN"

    def test_symbol_not_found(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, symbol="XXXUSDT")
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"


class TestLoading:
    def test_explicit_start_effective(self, env):
        start = "2024-03-01T00:00:00Z"  # 距数据起点 > 200 bar
        out = _call(env, start=start, reductions=["latest"])
        assert out["range"]["effective_start"] == \
            pd.Timestamp(start).isoformat()
        assert out["range"]["bars"] == 526  # 独立日历推算：start 为第
        # 274 根（2023-06-01 → 2024-03-01 间隔 274 天），求值域 800-274

    def test_default_start_warmup_excluded(self, env):
        out = _call(env, reductions=["latest"])
        df = env["df"]
        assert out["range"]["effective_start"] == \
            df.index[WARMUP_RSI].isoformat()
        assert out["warmup_bars"] == WARMUP_RSI

    def test_warmup_insufficient_rejected(self, env):
        with pytest.raises(ResearchError) as ei:
            _call(env, start="2023-08-01T00:00:00Z")  # 前缀 61 bar < 200
        assert ei.value.code == "RESEARCH_WARMUP_INSUFFICIENT"

    def test_end_default_covers_to_tail(self, env):
        out = _call(env, reductions=["latest"])
        assert out["range"]["effective_end"] == \
            env["df"].index[-1].isoformat()
        assert out["reductions"]["rsi_14"]["latest"]["timestamp"] == \
            env["df"].index[-1].isoformat()

    def test_end_explicit_upper_bound(self, env):
        end = "2024-06-01T00:00:00Z"
        out = _call(env, end=end, reductions=["latest"])
        assert out["range"]["effective_end"] == pd.Timestamp(end).isoformat()
        assert out["reductions"]["rsi_14"]["latest"]["timestamp"] <= \
            pd.Timestamp(end).isoformat()


class TestMultiColumn:
    def test_macd_columns_independent(self, env):
        from core.analysis.registry_meta import derive_warmup

        warmup = derive_warmup(["MACD"])  # 外部权威（非被测编排）
        out = _call(env, indicator="MACD", reductions=["quantiles"])
        cols = set(out["reductions"])
        assert cols == {"macd_12_26_9", "macd_signal_12_26_9",
                        "macd_histogram_12_26_9"}
        assert out["warmup_bars"] == warmup
        m = ta.macd(env["df"]["close"], fast=12, slow=26, signal=9)
        macd_col = m["MACD_12_26_9"].iloc[warmup:].dropna().values
        assert out["reductions"]["macd_12_26_9"]["quantiles"]["p50"] == \
            pytest.approx(float(np.percentile(macd_col, 50)), rel=1e-6)

    def test_bb_columns_independent(self, env):
        out = _call(env, indicator="BB", reductions=["latest"])
        assert set(out["reductions"]) == \
            {"bb_lower_20_2", "bb_middle_20_2", "bb_upper_20_2"}

    def test_partial_nan_column_null_semantics(self, env, monkeypatch):
        """B 裁决：个别列全 NaN → 该列各归约 null 键保留，整体通过。

        标准装载下 warmup 200 保护使冷启动差不会自然出现（derive_warmup
        max 聚合覆盖最慢列），null 属防御性契约——以注入构造列验证
        列级判空逻辑（mock 的是数据源，不是被测归约编排）。
        """
        import core.features.indicators as ind_mod

        rng = np.random.default_rng(5)
        fast_vals = pd.Series(
            50.0 + rng.normal(0, 5, len(env["df"])), index=env["df"].index)
        fast_vals.iloc[:50] = np.nan  # 模拟较快冷启动

        def fake_compute(df, name, params):
            return {"col_fast": fast_vals,
                    "col_slow": pd.Series(np.nan, index=df.index)}

        monkeypatch.setattr(ind_mod, "_compute_indicator", fake_compute)
        out = _call(env, indicator="MACD")  # 指标名仅走校验，列已注入
        assert out["reductions"]["col_slow"]["latest"] is None
        assert out["reductions"]["col_slow"]["quantiles"] is None
        expect = fast_vals.iloc[out["warmup_bars"]:].dropna().iloc[-1]
        assert out["reductions"]["col_fast"]["latest"]["value"] == \
            pytest.approx(expect, rel=1e-6)

    def test_all_columns_nan_rejected(self, env, monkeypatch):
        import core.features.indicators as ind_mod

        monkeypatch.setattr(
            ind_mod, "_compute_indicator",
            lambda df, name, params: {
                "col_a": pd.Series(np.nan, index=df.index)})
        with pytest.raises(ResearchError) as ei:
            _call(env, indicator="RSI")
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"


class TestVolume:
    def test_full_reductions_under_2048(self, env):
        out = _call(env, reductions=["latest", "quantiles",
                                     "regime_means", "trend_slope"])
        size = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        assert size <= 2048

    def test_oversize_rejected(self, env, monkeypatch):
        import core.analysis.indicator_query as iq

        monkeypatch.setattr(iq, "_MAX_SERIALIZED_BYTES", 16)
        with pytest.raises(ResearchError) as ei:
            _call(env)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["limit"] == 16


class TestGapClosureQuery3:
    """第三批：装载与求值域边界。"""

    # 不可达判定（第三批，装载层责任）：①load 返回 None 分支——
    # load_parquet 对损坏/空文件抛底层 ArrowInvalid 而非返回 None，
    # df is None 无触发路径；②求值域空分支——end 早于数据时
    # load_and_prepare_df 装载检查前置拒（区间数据不足）。

    def test_数据不足有效域_拒(self, tmp_path, db_path):
        import numpy as np
        import pandas as pd
        data_dir = tmp_path / "m"
        data_dir.mkdir()
        idx = pd.date_range("2024-01-01", periods=150, tz="UTC",
                            name="timestamp")
        close = np.full(150, 100.0)
        pd.DataFrame({"open": close, "high": close, "low": close,
                      "close": close,
                      "volume": np.full(150, 10.0)},
                     index=idx).to_parquet(data_dir / "BTCUSDT_1d.parquet")
        with pytest.raises(ResearchError, match="有效域"):
            query_indicator(data_dir, symbol="BTCUSDT", timeframe="1d",
                            indicator="RSI", params={"period": 14})
