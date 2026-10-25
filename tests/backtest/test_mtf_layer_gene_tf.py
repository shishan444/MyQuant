"""R1 求值合流锚族（fix-mtf-execution-consistency 组 1）。

锁定 evaluate_layer 基因求值合流单点的契约面（design 卡 R1）：

- R1-E1 轴缺失拒绝：层内标注轴不在装载集 → RESEARCH_DATA_INSUFFICIENT
  透传，与单周期路径同错误源产出同款错误面（合流的错误面一致性）；
- R1-E2 ValueError 转译：层内基因求值 ValueError → RESEARCH_CONDITION_
  INVALID detail 四键（静默 continue 退役——基因信号不产生即红）；
- R1-S1 两步投影语义：层内 3d 标注基因经 3d→层轴 4h→请求轴 30m 的
  可见时刻=已收盘投影逐段——相对一步投影（3d→30m）延后 ∈ [0, 1 个
  层周期]（区间断言形态：锁可见时刻上界+不早于+不丢信号，语义锁
  「投影先于层内组合顺序」）；
- R1-S2 第三参兼容：缺省 None 两参调用现行为不变；带标注基因不传
  装载集 → 结构化拒绝（fail-loud 不静默）；
- R1-M1 mtf_engine 传导：evaluate_layer_with_context 透传装载集，
  共振引擎多层个体带标注基因可求值（extract_context 保持层轴提取
  不扩——负向由修复前后 ctx 形制不变承载，此处锁跑通+信号生效）。
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.errors import ResearchError  # noqa: E402
from core.strategy.dna import (  # noqa: E402
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)
from core.strategy.executor import dna_to_signal_set, evaluate_layer  # noqa: E402
from tests.helpers.data_factory import make_ohlcv  # noqa: E402

BEAR_FIELD = "pattern_bearish_engulfing"


# ── 合成多轴数据 ──────────────────────────────────────────────

def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = pd.DataFrame({
        "open": df["open"].resample(rule).first(),
        "high": df["high"].resample(rule).max(),
        "low": df["low"].resample(rule).min(),
        "close": df["close"].resample(rule).last(),
        "volume": df["volume"].resample(rule).sum(),
    }).dropna()
    return out[(out["high"] >= out["low"]) & (out["volume"] > 0)]


def _multi_axis_env(n_30m: int = 4320, seed: int = 11):
    """30m 基准 + 4h/3d 轴合成环境（4320 根 30m = 90 天 ≈ 30 根 3d）。"""
    base = make_ohlcv(n=n_30m, freq="30min", start="2024-01-01", seed=seed)
    return base, _resample_ohlcv(base, "4h"), _resample_ohlcv(base, "3D")


def _implant_bearish_engulfing(df_3d: pd.DataFrame, at: int) -> pd.DataFrame:
    """在 3d 轴第 at 根植入确定性看跌吞没（前根阳线+当根大阴线）。"""
    df = df_3d.copy()
    prev, cur = df.index[at - 1], df.index[at]
    df.loc[prev, "open"] = 100.0
    df.loc[prev, "close"] = 110.0  # 阳线
    df.loc[cur, "open"] = 111.0    # 高开超过前根收盘
    df.loc[cur, "close"] = 99.0    # 收盘低于前根开盘——吞没成立
    df.loc[prev, "high"] = max(112.0, df.loc[prev, "high"])
    df.loc[prev, "low"] = min(99.0, df.loc[prev, "low"])
    df.loc[cur, "high"] = max(112.0, df.loc[cur, "high"])
    df.loc[cur, "low"] = min(98.0, df.loc[cur, "low"])
    return df


def _annotated_gene(tf: str | None = None) -> SignalGene:
    return SignalGene(
        "BearishEngulfing", {}, SignalRole.ENTRY_TRIGGER, BEAR_FIELD,
        {"type": "gt", "threshold": 0.0}, timeframe=tf)


def _layer_dna(gene: SignalGene, layer_tf: str = "4h") -> StrategyDNA:
    """显式单层 DNA（层 4h 内含指定基因；构造态=真显式层）。"""
    layer = TimeframeLayer(
        timeframe=layer_tf, signal_genes=[gene],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"))
    return StrategyDNA(
        strategy_id="st_r1_layer_test",
        signal_genes=[gene],
        execution_genes=ExecutionGenes(timeframe="30m", symbol="BTCUSDT"),
        logic_genes=LogicGenes(),
        risk_genes=RiskGenes(direction="short"),
        layers=[layer],
        signal_sources=[{"type": "rule"}])


def _flat_dna(gene: SignalGene) -> StrategyDNA:
    """等价无层单周期 DNA（同一基因挂顶层 signal_genes）。"""
    return StrategyDNA(
        strategy_id="st_r1_flat_test",
        signal_genes=[gene],
        execution_genes=ExecutionGenes(timeframe="30m", symbol="BTCUSDT"),
        logic_genes=LogicGenes(),
        risk_genes=RiskGenes(direction="short"),
        signal_sources=[{"type": "rule"}])


# ── R1-E1 轴缺失拒绝 ─────────────────────────────────────────

class TestR1E1AxisMissing:
    def test_layer_annotated_gene_missing_axis_structured_reject(self):
        """层内 @3d 基因+装载集缺 3d → 两路径同款 RESEARCH_DATA_INSUFFICIENT。"""
        base, df_4h, _ = _multi_axis_env()
        gene = _annotated_gene("3d")
        dfs_no_3d = {"30m": base, "4h": df_4h}

        with pytest.raises(ResearchError) as layer_err:
            dna_to_signal_set(_layer_dna(gene), base, dfs_no_3d)
        with pytest.raises(ResearchError) as flat_err:
            dna_to_signal_set(_flat_dna(gene), base, dfs_no_3d)

        assert layer_err.value.code == "RESEARCH_DATA_INSUFFICIENT"
        # 合流的错误面一致性：两路径同 code 同 detail（含 indicator 补注）
        assert layer_err.value.code == flat_err.value.code
        assert layer_err.value.detail == flat_err.value.detail
        assert layer_err.value.detail["timeframe"] == "3d"
        assert "3d" not in layer_err.value.detail["available"]
        assert layer_err.value.detail["indicator"] == "BearishEngulfing"


# ── R1-E2 ValueError 转译 ─────────────────────────────────────

class TestR1E2ValueErrorTranslation:
    def test_layer_gene_value_error_translated(self):
        """层内基因求值 ValueError → RESEARCH_CONDITION_INVALID 四键。"""
        base, df_4h, _ = _multi_axis_env()
        # 基础列基因（hour_utc 由装载层注入——轴 df 无该列 → ValueError）
        bad = SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER,
                         None, {"type": "gt", "threshold": 5.0})
        with pytest.raises(ResearchError) as exc:
            dna_to_signal_set(
                _layer_dna(bad), base, {"30m": base, "4h": df_4h})
        assert exc.value.code == "RESEARCH_CONDITION_INVALID"
        for key in ("indicator", "params", "field", "cause"):
            assert key in exc.value.detail, f"detail 缺 {key}"
        assert exc.value.detail["indicator"] == "hour_utc"

    def test_silent_continue_retired_no_residual_signal(self):
        """静默 continue 退役：坏基因让整次求值结构化拒绝（无残缺产出）。"""
        base, df_4h, _ = _multi_axis_env()
        bad = SignalGene("hour_utc", {}, SignalRole.ENTRY_TRIGGER,
                         None, {"type": "gt", "threshold": 5.0})
        good = SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                          "rsi", {"type": "lt", "threshold": 30})
        layer = TimeframeLayer(
            timeframe="4h", signal_genes=[good, bad],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"))
        dna = StrategyDNA(
            strategy_id="st_r1_mixed_test",
            signal_genes=[good, bad],
            execution_genes=ExecutionGenes(timeframe="30m", symbol="BTCUSDT"),
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="short"),
            layers=[layer], signal_sources=[{"type": "rule"}])
        with pytest.raises(ResearchError):
            dna_to_signal_set(dna, base, {"30m": base, "4h": df_4h})


# ── R1-S1 两步投影语义 ───────────────────────────────────────

class TestR1S1TwoStepProjection:
    def test_layer_annotated_projection_within_one_layer_period(self):
        """3d→4h→30m 两步投影 vs 3d→30m 一步投影：延后 ∈ [0, 8] 根 30m。"""
        base, df_4h, df_3d = _multi_axis_env()
        df_3d = _implant_bearish_engulfing(df_3d, at=8)
        gene = _annotated_gene("3d")
        dfs = {"30m": base, "4h": df_4h, "3d": df_3d}

        sig_layer = dna_to_signal_set(_layer_dna(gene), base, dfs)
        sig_flat = dna_to_signal_set(_flat_dna(gene), base, dfs)

        layer_pos = np.flatnonzero(sig_layer.entries.fillna(False).to_numpy())
        flat_pos = np.flatnonzero(sig_flat.entries.fillna(False).to_numpy())
        assert len(flat_pos) > 0, "对照一步投影应非空（吞没形态已植入）"
        assert len(layer_pos) > 0, "层路径两步投影应非空"
        # 语义锁：层路径首触发不早于一步投影（投影先于层内组合），
        # 且延后不超过 1 个 4h 层周期（8 根 30m）
        assert layer_pos[0] >= flat_pos[0]
        assert layer_pos[0] - flat_pos[0] <= 8, (
            f"两步投影延后 {layer_pos[0] - flat_pos[0]} 根 30m 超一个层周期")
        # 不丢信号：层路径触发集合 ⊆ 一步投影触发集合的 [0, +8] 邻域
        flat_set = set(flat_pos.tolist())
        for p in layer_pos.tolist():
            assert any(q in flat_set for q in range(p - 8, p + 1)), (
                f"层路径触发 @{p} 无一步投影对应源（信号丢失）")


# ── R1-S2 第三参兼容 ─────────────────────────────────────────

class TestR1S2SignatureCompat:
    def test_two_arg_call_unchanged_for_unannotated_genes(self):
        """无标注基因：两参调用与三参 None 信号逐位相等（现行为不变）。"""
        base, df_4h, _ = _multi_axis_env()
        gene = SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                          "rsi", {"type": "lt", "threshold": 35})
        layer = TimeframeLayer(
            timeframe="4h", signal_genes=[gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"))
        sig2 = evaluate_layer(layer, df_4h)
        sig3 = evaluate_layer(layer, df_4h, None)
        pd.testing.assert_series_equal(
            sig2.entries, sig3.entries, check_names=False)
        pd.testing.assert_series_equal(
            sig2.exits, sig3.exits, check_names=False)
        assert int(sig2.entries.sum()) > 0  # 数据量下 RSI<35 应有触发

    def test_annotated_gene_without_load_set_rejects(self):
        """带标注基因不传装载集 → 结构化拒绝（fail-loud 不静默）。"""
        _, df_4h, _ = _multi_axis_env()
        gene = _annotated_gene("3d")
        layer = TimeframeLayer(
            timeframe="4h", signal_genes=[gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"))
        with pytest.raises(ResearchError) as exc:
            evaluate_layer(layer, df_4h)  # 两参调用（无装载集）
        assert exc.value.code == "RESEARCH_DATA_INSUFFICIENT"


# ── R1-M1 mtf_engine 传导 ────────────────────────────────────

class TestR1M1MtfEnginePropagation:
    def test_score_engine_annotated_gene_evaluates(self):
        """共振引擎多层个体带标注基因：透传装载集后可求值且信号生效。"""
        base, df_4h, df_3d = _multi_axis_env()
        df_3d = _implant_bearish_engulfing(df_3d, at=8)
        dfs = {"30m": base, "4h": df_4h, "3d": df_3d}
        gene = _annotated_gene("3d")
        exec_layer = TimeframeLayer(
            timeframe="30m",
            signal_genes=[gene,
                          SignalGene("RSI", {"period": 14},
                                     SignalRole.ENTRY_TRIGGER, "rsi",
                                     {"type": "gt", "threshold": 20})],
            logic_genes=LogicGenes(entry_logic="OR", exit_logic="AND"))
        trend_layer = TimeframeLayer(
            timeframe="1d",
            signal_genes=[SignalGene("EMA", {"period": 20},
                                     SignalRole.ENTRY_TRIGGER, "ema",
                                     {"type": "price_below"})],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            role="trend")
        df_1d = _resample_ohlcv(base, "1D")
        dfs["1d"] = df_1d
        dna = StrategyDNA(
            strategy_id="st_r1_mtf_engine_test",
            signal_genes=[gene],
            execution_genes=ExecutionGenes(timeframe="30m", symbol="BTCUSDT"),
            logic_genes=LogicGenes(),
            risk_genes=RiskGenes(direction="short"),
            layers=[trend_layer, exec_layer],
            mtf_mode="score",
            signal_sources=[{"type": "rule"}])
        sig = dna_to_signal_set(dna, base, dfs)  # mtf_mode → run_mtf_engine
        assert sig.entries is not None
        assert len(sig.entries) == len(base)
