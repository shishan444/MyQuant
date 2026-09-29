"""column_ref 单元测试（0.2.13 unify-column-references T1）。

锚清单（design 8.2）：
- resolve 正例/拒绝（混合形制、field 规则、编码字符串入口关闭）
- decode 12 naming 模式覆盖（含 bb 整数化 std、kc 小数 mult 文法）
- 往返一致性锁：resolve_indicator_column × _compute_indicator 全注册表
  采样对照（评估轮漂移 0 基线固化）；decode→encode 恒等
- canonical_ref（数值同形/键序/field 缺省归一）

VWAP/OBV/AD/CVD 为 0 参 default 指标（列名无后缀，_compute_indicator
无实现或不产编码列）——往返锁声明跳过并注释（N1 无关发现，不本次修）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.analysis.column_ref import (
    BASE_SUBJECT_COLUMNS,
    ColumnRefError,
    canonical_ref,
    decode_column_ref,
    encode_column_ref,
    resolve_column_ref,
)
from core.features.indicators import _compute_indicator
from core.features.registry import INDICATOR_REGISTRY

# 0 参 default 指标：无编码列产出（VWAP 空产出为历史现状——N1 记录不改）
_SKIP_NO_ENCODE = {"VWAP", "OBV", "AD", "CVD"}


# ---------------------------------------------------------------- resolve

@pytest.mark.parametrize(("ref", "kind", "field", "column"), [
    pytest.param({"indicator": "RSI", "params": {"period": 14}},
                 "indicator", "rsi", "rsi_14", id="单参数指标-缺省field"),
    pytest.param({"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
                  "field": "signal"}, "indicator", "signal",
                 "macd_signal_12_26_9", id="多参数指标-显式field"),
    pytest.param({"indicator": "BearishEngulfing", "params": {}},
                 "indicator", "pattern_bearish_engulfing",
                 "pattern_bearish_engulfing", id="无参pattern类"),
    pytest.param("close", "base", None, "close", id="基础列字符串"),
    pytest.param("funding_rate", "base", None, "funding_rate", id="衍生品基础列"),
])
def test_resolve_column_ref_accepts_mixed_forms(ref, kind, field, column):
    d = resolve_column_ref(ref)
    assert d.kind == kind
    assert d.field == field
    assert d.encoded_name() == column


def test_resolve_column_ref_accepts_plugin_column(db_path):
    # 插件列判定需要注册表（研究表自愈 + 生产 repository 写入）
    from core.analysis.executor import ensure_research_tables
    from core.persistence.repository import research as rr
    ensure_research_tables(db_path)
    rr.register_existing_plugin_signal(
        db_path, source="formula", plugin_id="plg_1",
        name="alpha", version=1, column_name="fx_alpha", extra={})
    d = resolve_column_ref("fx_alpha", db_path)
    assert d.kind == "plugin"


@pytest.mark.parametrize(("ref", "code"), [
    pytest.param("rsi_14", "ENCODED_STRING_REJECTED", id="编码字符串-入口关闭"),
    pytest.param("macd_12_26_9", "ENCODED_STRING_REJECTED", id="多段编码字符串"),
    pytest.param("nope_col", "NOT_FOUND", id="未知字符串"),
    pytest.param({"indicator": "NOPE", "params": {}}, "PARAM_INVALID", id="未知指标"),
    pytest.param({"indicator": "RSI", "params": {"period": 14, "x": 1}},
                 "PARAM_INVALID", id="多余参数键"),
    pytest.param({"indicator": "RSI", "params": {}},
                 "PARAM_INVALID", id="缺参数键"),
    pytest.param({"indicator": "RSI", "params": {"period": 99999}},
                 "PARAM_INVALID", id="参数越界"),
    pytest.param({"indicator": "RSI", "params": {"period": "14"}},
                 "PARAM_INVALID", id="参数非数值"),
    pytest.param({"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}},
                 "FIELD_REQUIRED", id="多输出缺field"),
    pytest.param({"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
                  "field": "nope"}, "FIELD_UNKNOWN", id="field越界"),
    pytest.param(42, "PARAM_INVALID", id="非对象非字符串"),
])
def test_resolve_column_ref_rejects_invalid_refs(ref, code):
    with pytest.raises(ColumnRefError) as ei:
        resolve_column_ref(ref)
    assert ei.value.code == code


def test_reject_detail_carries_hint():
    with pytest.raises(ColumnRefError) as ei:
        resolve_column_ref("rsi_14")
    assert "signals" in ei.value.detail["hint"]


# ---------------------------------------------------------------- decode

@pytest.mark.parametrize(("column", "indicator", "params", "field_name"), [
    pytest.param("rsi_14", "RSI", {"period": 14}, "rsi", id="default单参"),
    pytest.param("adx_14", "ADX", {"period": 14}, "adx", id="default单参2"),
    pytest.param("macd_12_26_9", "MACD",
                 {"fast": 12, "slow": 26, "signal": 9}, "macd", id="macd主列"),
    pytest.param("macd_signal_12_26_9", "MACD",
                 {"fast": 12, "slow": 26, "signal": 9}, "signal", id="macd信号线"),
    pytest.param("macd_histogram_12_26_9", "MACD",
                 {"fast": 12, "slow": 26, "signal": 9}, "histogram", id="macd柱"),
    pytest.param("bb_upper_20_2", "BB",
                 {"period": 20, "std": 2.0}, "upper", id="bb整数化std文法"),
    pytest.param("stoch_k_14_3", "Stochastic",
                 {"k_period": 14, "d_period": 3}, "k", id="stoch"),
    pytest.param("kc_20_14_2.0_upper", "Keltner",
                 {"ema_period": 20, "atr_period": 14, "multiplier": 2.0},
                 "upper", id="kc小数mult文法"),
    pytest.param("dc_upper_20", "Donchian", {"period": 20}, "upper", id="dc"),
    pytest.param("aroon_up_14", "Aroon", {"period": 14}, "aroon_up", id="aroon"),
    pytest.param("mfe_score_10_100", "FractalEntropy",
                 {"bins": 10, "lookback": 100}, "mfe_score", id="mfe"),
    pytest.param("mf_osc_20", "MultifactorOsc",
                 {"lookback": 20}, "mf_osc", id="mf_osc"),
    pytest.param("psar", "PSAR",
                 {"step": 0.02, "max_step": 0.2}, "psar",
                 id="psar固定名-参数补default"),
    pytest.param("pattern_bearish_engulfing", "BearishEngulfing",
                 {}, "pattern_bearish_engulfing", id="pattern固定名反查"),
])
def test_decode_column_ref_covers_12_naming_modes(column, indicator, params, field_name):
    got = decode_column_ref(column)
    assert got == {"indicator": indicator, "params": params, "field": field_name}
    # decode→encode 恒等（往返一致）
    assert encode_column_ref(indicator, params, field_name) == column


@pytest.mark.parametrize("column", [
    pytest.param("close", id="基础列非编码"),
    pytest.param("fx_alpha_v2", id="插件列非编码"),
    pytest.param("rsi_xx", id="非数字参数段"),
    pytest.param("rsi_14_26", id="段数不匹配"),
])
def test_decode_returns_none_for_non_encoded(column):
    assert decode_column_ref(column) is None


# ---------------------------------------------------------------- 往返一致性锁

def _sample_df(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({
        "open": close + rng.normal(0, .5, n),
        "high": close + abs(rng.normal(0, 1, n)),
        "low": close - abs(rng.normal(0, 1, n)),
        "close": close,
        "volume": abs(rng.normal(1000, 100, n)),
        "open_interest": abs(rng.normal(50000, 5000, n)),
        "funding_rate": rng.normal(0.0001, 0.0004, n),
    }, index=pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"))


@pytest.mark.parametrize("sample", ["default", "min"])
def test_roundtrip_lock_encode_matches_compute_output(sample):
    """评估轮漂移 0 基线固化：_compute_indicator 实际产出列名 ×
    resolve_indicator_column（encode 权威）全注册表采样对照。"""
    df = _sample_df()
    drift, checked = [], 0
    for name, defn in INDICATOR_REGISTRY.items():
        if name in _SKIP_NO_ENCODE:
            continue  # N1：VWAP 等空产出（历史现状，非本次引入）
        params = {k: (p.default if sample == "default" else p.min)
                  for k, p in defn.params.items()}
        cols = _compute_indicator(df, name, dict(params))
        if not cols:
            continue  # 合成数据下个别指标不可计算（如衍生品缺列）——跳过
        produced = set()
        for f in defn.output_fields:
            try:
                produced.add(encode_column_ref(name, dict(params), f))
            except Exception:
                pass
        missing = [c for c in cols if c not in produced]
        checked += len(cols)
        if missing:
            drift.append((name, sample, sorted(cols), missing))
    assert not drift, f"encode 与实际产出漂移: {drift}"
    assert checked > 0


# ---------------------------------------------------------------- canonical

def test_canonical_number_normalization():
    assert (canonical_ref({"indicator": "RSI", "params": {"period": 14}})
            == canonical_ref({"indicator": "RSI", "params": {"period": 14.0}}))


def test_canonical_field_default_equivalence():
    """缺省 field 与显式 field 的同语义引用同 canonical（幂等不断裂）。"""
    assert (canonical_ref({"indicator": "RSI", "params": {"period": 14}})
            == canonical_ref({"indicator": "RSI", "params": {"period": 14},
                              "field": "rsi"}))


def test_canonical_string_form_keeps_legacy_continuity():
    """字符串 ref 归一 strip 裸名——与现状指纹 subject 段逐字节连续（存量连续锚）。"""
    assert canonical_ref("close") == "close"
    assert canonical_ref("fx_alpha_v2") == "fx_alpha_v2"
    assert canonical_ref("  rsi_14 ") == "rsi_14"


def test_canonical_param_key_order_independent():
    a = {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9},
         "field": "signal"}
    b = {"indicator": "MACD", "params": {"signal": 9, "slow": 26, "fast": 12},
         "field": "signal"}
    assert canonical_ref(a) == canonical_ref(b)


def test_base_columns_registry_intact():
    """基础列清单自 hypothesis_service 移入后内容不变（迁移完整性）。"""
    assert {"open", "high", "low", "close", "volume", "trades",
            "open_interest", "funding_rate"} <= BASE_SUBJECT_COLUMNS


# ---------------------------------------------------------------- 五域集成锚

class TestFiveDomainIntegration:
    """T3 五域关键锚（登记→翻译→取列 N4 全链）。"""

    def test_macd_three_param_register_translate_and_column(self, db_path):
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.db_ext import init_db_ext
        init_db_ext(db_path)
        ensure_research_tables(db_path)
        rng = [{"symbol": "BTCUSDT", "timeframe": "4h",
                "start": "2024-01-01", "end": "2024-02-01"}]
        hid = register_hypothesis(db_path, conditions={
            "when_conditions": [{"subject": {"indicator": "MACD",
                                             "params": {"fast": 12, "slow": 26,
                                                        "signal": 9},
                                             "field": "signal"},
                                 "action": "lt", "target": 0}],
            "then_conditions": [{"subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                 "action": "gt", "target": 50}]},
            statement="macd", data_ranges=rng)["hypothesis_id"]
        res = translate_hypothesis(db_path, hypothesis_id=hid)
        assert res["genes"][0] == "MACD(signal)"
        # N4 取列锚：field 语义名 → executor resolve 精确取 macd_signal 列
        from core.persistence.db_ext import get_strategy
        import json as _json
        strat = get_strategy(db_path, res["strategy_id"])
        dna = _json.loads(strat["dna_json"] if "dna_json" in strat.keys()
                          else strat["dna"])
        gene = dna["signal_genes"][0]
        assert gene["field"] == "signal"
        assert gene["params"] == {"fast": 12, "slow": 26, "signal": 9}
        assert encode_column_ref("MACD", gene["params"], gene["field"]) == \
            "macd_signal_12_26_9"

    def test_encoded_string_rejected_at_registration(self, db_path):
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import ResearchError, register_hypothesis
        ensure_research_tables(db_path)
        rng = [{"symbol": "BTCUSDT", "timeframe": "4h",
                "start": "2024-01-01", "end": "2024-02-01"}]
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(db_path, conditions={
                "when_conditions": [{"subject": "rsi_14",
                                     "action": "lt", "target": 30}],
                "then_conditions": [{"subject": "close",
                                     "action": "gt", "target": 0}]},
                statement="s", data_ranges=rng)
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"
