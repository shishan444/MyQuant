"""T① 三形态双链对拍锚族（fix-mtf-execution-consistency 组 2，T2.6）。

同一确定性合成数据喂两条独立实现，断言信号序列逐位相等（「分析
所见=执行所得」——信号层口径；交易级口径归 C1 活体锚）：

- 分析面：condition_kernel.mask_for_all（含高周期掩码投影——全案
  唯一已正确的多周期实现，对拍对照侧）；
- 执行面：dna_to_signal_set 经**落库链加载态**（save→get→from_json）
  +传装载集（执行轴+全部标注轴）。

控制变量声明：测试 DNA 无 entry_trigger_policy；出场基因独立断言
（exits 对拍 then 掩码）——防 policy 变换/同 bar 互斥污染对拍口径。

三形态：
- A 趋势类×高周期：EMA@1d price_above + BB upper price_above（4h
  基准）——防回归；
- B 形态类×高周期：BearishEngulfing@3d gt 0 + RVOL gt 1.2（30m
  基准）——**判别力在存量形制 dna_json 加载态**（R1b 前伪层字节，
  手工注入构造；用内存构造态或新产物形制会使判别力失效——锚文件
  头防误构声明）；
- C 纯低周期基线（4h 基准）。

⚠ 防误构声明（B 形态）：本锚的执行面 DNA 必须经「伪层形制 dna_json」
加载（_legacy_autowrap_json 注入）——R1b 后新产物 dna_json 不含伪层
字节，加载态与内存态同路由使本锚退化为恒等对拍。改用新产物形制须
重造判别力（构造 to_dict 写出显式层并保持 is_mtf=True 的对照路径）。
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.condition_kernel import MtfEvalContext, mask_for_all
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import get_strategy, save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)
from core.strategy.executor import dna_to_signal_set


def _synth(n: int, freq: str, seed: int, start: str = "2024-01-01",
           base: float = 60000.0, sigma: float = 0.008) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    rets = rng.normal(0.0002, sigma, n)
    close = base * np.cumprod(1.0 + rets)
    open_ = np.empty(n)
    open_[0] = base
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, sigma / 2, n)) * close
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": open_,
                         "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread,
                         "close": close,
                         "volume": np.abs(rng.normal(500, 150, n)) + 1.0},
                        index=idx)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = pd.DataFrame({
        "open": df["open"].resample(rule).first(),
        "high": df["high"].resample(rule).max(),
        "low": df["low"].resample(rule).min(),
        "close": df["close"].resample(rule).last(),
        "volume": df["volume"].resample(rule).sum(),
    }).dropna()
    return out[(out["high"] >= out["low"]) & (out["volume"] > 0)]


def _implant_bearish(df_hi: pd.DataFrame, at: int) -> pd.DataFrame:
    df = df_hi.copy()
    prev, cur = df.index[at - 1], df.index[at]
    df.loc[prev, "open"] = 100.0
    df.loc[prev, "close"] = 110.0
    df.loc[cur, "open"] = 111.0
    df.loc[cur, "close"] = 99.0
    df.loc[prev, "high"] = max(112.0, df.loc[prev, "high"])
    df.loc[prev, "low"] = min(99.0, df.loc[prev, "low"])
    df.loc[cur, "high"] = max(112.0, df.loc[cur, "high"])
    df.loc[cur, "low"] = min(98.0, df.loc[cur, "low"])
    return df


def _assert_parity(analysis_mask, exec_series, label):
    """逐位相等断言（fillna(False) 归一）。"""
    a = analysis_mask.fillna(False).astype(bool)
    e = exec_series.fillna(False).astype(bool)
    pd.testing.assert_series_equal(a, e, check_names=False,
                                   check_freq=False,
                                   obj=f"{label} 对拍（分析面 vs 执行面）")


class TestT1ParityFormA:
    """A 形态：趋势类×高周期（EMA@1d price_above+BB upper，4h 基准）。"""

    def test_form_a_trend_high_tf_parity(self, tmp_path):
        base = _synth(3000, "4h", 41)
        hi = _resample(base, "1D")
        work = base.iloc[500:]  # 请求区间（头部留收敛前缀语义）

        when = [
            {"subject": {"indicator": "EMA", "params": {"period": 10},
                         "field": "ema"},
             "action": "price_above", "target": 0.0, "timeframe": "1d"},
            {"subject": {"indicator": "BB", "params": {"period": 20,
                                                        "std": 2},
                         "field": "upper"},
             "action": "price_above", "target": 0.0},
        ]
        then = [
            {"subject": {"indicator": "BB", "params": {"period": 20,
                                                       "std": 2},
                         "field": "middle"},
             "action": "price_below", "target": 0.0},
        ]
        # 分析面
        ctx = MtfEvalContext(data_dir=None, symbol="BTCUSDT",
                             base_tf="4h")
        ctx._raw["1d"] = hi  # 直注 raw（免 parquet 落盘——同数据同源）
        mask_when = mask_for_all(work, when, mtf=ctx)
        mask_then = mask_for_all(work, then, mtf=ctx)

        # 执行面（落库链加载态+装载集）
        dna = StrategyDNA(
            signal_genes=[
                SignalGene("EMA", {"period": 10}, SignalRole.ENTRY_TRIGGER,
                           "ema", {"type": "price_above"}, timeframe="1d"),
                SignalGene("BB", {"period": 20, "std": 2},
                           SignalRole.ENTRY_TRIGGER, "upper",
                           {"type": "price_above"}),
                SignalGene("BB", {"period": 20, "std": 2},
                           SignalRole.EXIT_TRIGGER, "middle",
                           {"type": "price_below"}),
            ],
            execution_genes=ExecutionGenes(timeframe="4h",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(),
            signal_sources=[{"type": "rule"}])
        db = tmp_path / "q.db"
        init_db_ext(db)
        save_strategy(db, strategy_id="st_t1_a", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      gene_signature="sig_t1_a")
        loaded = StrategyDNA.from_json(
            get_strategy(db, "st_t1_a")["dna_json"])
        sig = dna_to_signal_set(
            loaded, work, {"4h": work, "1d": hi})

        assert int(mask_when.sum()) > 0, "A 形态对照侧应有触发"
        _assert_parity(mask_when, sig.entries, "A.entries")
        _assert_parity(mask_then, sig.exits, "A.exits")


class TestT1ParityFormB:
    """B 形态：形态类×高周期（BearishEngulfing@3d+RVOL，30m 基准，
    存量伪层形制加载态——判别力核心）。"""

    def test_form_b_pattern_high_tf_parity(self, tmp_path):
        base = _synth(6000, "30min", 43)
        hi = _implant_bearish(_resample(base, "3D"), at=25)
        work = base.iloc[3000:]  # 前 62 天≈20 根 3d（分析面 warmup 前扩达标）

        when = [
            {"subject": {"indicator": "BearishEngulfing", "params": {},
                         "field": "pattern_bearish_engulfing"},
             "action": "gt", "target": 0.0, "timeframe": "3d"},
            {"subject": {"indicator": "RVOL", "params": {"period": 20},
                         "field": "rvol"},
             "action": "gt", "target": 1.2},
        ]
        ctx = MtfEvalContext(data_dir=None, symbol="BTCUSDT",
                             base_tf="30m")
        ctx._raw["3d"] = hi
        mask_when = mask_for_all(work, when, mtf=ctx)

        # 执行面：内存构造 → to_json → 手工注入伪层（R1b 前存量形制）
        # → 落库 → 加载（愈合后单周期路径+基因级 3d 标注生效）
        # 不带 exit 基因：BE/RVOL 与 BB middle 出场无互斥安全关系，
        # 同 bar 互斥清洗会污染 entries 对拍（T① 控制变量声明——
        # A/C 的 exit 与 entry 物理互斥不受影响，B 的对拍焦点在 when）
        dna_mem = StrategyDNA(
            signal_genes=[
                SignalGene("BearishEngulfing", {},
                           SignalRole.ENTRY_TRIGGER,
                           "pattern_bearish_engulfing",
                           {"type": "gt", "threshold": 0.0},
                           timeframe="3d"),
                SignalGene("RVOL", {"period": 20},
                           SignalRole.ENTRY_TRIGGER, "rvol",
                           {"type": "gt", "threshold": 1.2}),
            ],
            execution_genes=ExecutionGenes(timeframe="30m",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(direction="short"),
            signal_sources=[{"type": "rule"}])
        payload = json.loads(dna_mem.to_json())
        ghost = TimeframeLayer(
            timeframe="30m", signal_genes=list(dna_mem.signal_genes),
            logic_genes=LogicGenes())
        payload["layers"] = [ghost.to_dict()]  # 存量伪层字节
        legacy_json = json.dumps(payload, ensure_ascii=False, indent=2)

        db = tmp_path / "q.db"
        init_db_ext(db)
        save_strategy(db, strategy_id="st_t1_b", dna_json=legacy_json,
                      symbol="BTCUSDT", timeframe="30m",
                      gene_signature="sig_t1_b")
        loaded = StrategyDNA.from_json(
            get_strategy(db, "st_t1_b")["dna_json"])
        assert loaded.is_mtf is False  # 愈合生效（防误构自检）
        sig = dna_to_signal_set(
            loaded, work, {"30m": work, "3d": hi})

        assert int(mask_when.sum()) > 0, "B 形态对照侧应有触发"
        _assert_parity(mask_when, sig.entries, "B.entries")


class TestT1ParityFormC:
    """C 形态：纯低周期基线（4h 基准，无任何周期标注）。"""

    def test_form_c_pure_low_tf_parity(self, tmp_path):
        work = _synth(1500, "4h", 47)
        when = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"},
             "action": "lt", "target": 55},
            {"subject": {"indicator": "BB", "params": {"period": 20,
                                                        "std": 2},
                         "field": "upper"},
             "action": "price_above", "target": 0.0},
        ]
        then = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"},
             "action": "gt", "target": 65},
        ]
        ctx = MtfEvalContext(data_dir=None, symbol="BTCUSDT", base_tf="4h")
        mask_when = mask_for_all(work, when, mtf=ctx)
        mask_then = mask_for_all(work, then, mtf=ctx)

        dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi", {"type": "lt", "threshold": 55}),
                SignalGene("BB", {"period": 20, "std": 2},
                           SignalRole.ENTRY_TRIGGER, "upper",
                           {"type": "price_above"}),
                SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                           "rsi", {"type": "gt", "threshold": 65}),
            ],
            execution_genes=ExecutionGenes(timeframe="4h",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(),
            signal_sources=[{"type": "rule"}])
        db = tmp_path / "q.db"
        init_db_ext(db)
        save_strategy(db, strategy_id="st_t1_c", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      gene_signature="sig_t1_c")
        loaded = StrategyDNA.from_json(
            get_strategy(db, "st_t1_c")["dna_json"])
        sig = dna_to_signal_set(loaded, work, {"4h": work})

        assert int(mask_when.sum()) > 0, "C 形态对照侧应有触发"
        _assert_parity(mask_when, sig.entries, "C.entries")
        _assert_parity(mask_then, sig.exits, "C.exits")
