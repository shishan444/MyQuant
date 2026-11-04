"""vr-signal-trace golden 样本矩阵生成器（组 0 任务 0.1）。

使命：为引擎留痕通道（add-vr-signal-trace）提供改动前行为锚定的
固定样本集——每个样本=(合成数据段+DNA+引擎参数) 的确定性组合，同
种子同数据（沿 tests/reference/fixture_gen.py 纯函数合成先例）。
矩阵覆盖设计卡 §3①：SL 四档×出场族六值×三类拒绝点×求值层两分支×
多列（batch）×MTF 门控×资金费大额段×杠杆清算边界。

样本清单（11 个——事件覆盖面与黄金锚的双重使命）：
- pct_long        pct 档多头 SL/TP+entry/exit_signal（码 10/13/20/21）
- atr_short       atr 档空头（码 20/21 ATR 分派+10/13）
- trail_lock      trail 档峰值回撤（码 20+trail 逐 bar 线）
- series_line     series 档线值列（码 20 series 分支）
- exit_family     出场族 max_hold/time_exit/max_loss/exit_signal（码 23/24/25/13）
- exposure_add    敞口上限拦截（码 30）+减仓（码 12）
- risk_budget_cap risk_budget 数量仓截断（capped 推导面）
- swallow_conflict 求值层两分支：同 bar 互斥（33）+加仓抑制（34）
- mtf_gate        MTF 决策门控（mtf_gated_bars 计数面）
- liq_small_cash  杠杆清算（22）+清算后再入场资金不足（31）
- funding_batch   资金费大额段+batch_run 多列（两 DNA）

生成器纯函数无 IO；hash 清单与环境锚由 golden 断言测试的生成模式
（VR_TRACE_GOLDEN=1）随 golden 文件同批产出（tests/fixtures/
vr_trace_golden/manifest.json）。
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from tests.reference.fixture_gen import gen_ohlcv


def _gene(indicator: str, params: dict, role: str, field: str | None,
          condition: dict) -> dict:
    g: Dict[str, Any] = {
        "indicator": indicator, "params": params, "role": role,
        "condition": condition,
    }
    if field is not None:
        g["field"] = field
    return g


def _logic(entry="AND", exit_="AND", add="AND", reduce_="AND") -> dict:
    return {"entry_logic": entry, "exit_logic": exit_,
            "add_logic": add, "reduce_logic": reduce_}


def _risk(**kw) -> dict:
    base = {
        "stop_loss": 0.05, "take_profit": None, "position_size": 0.3,
        "leverage": 1, "direction": "long", "sl_mode": "pct",
        "atr_period": 14, "exit_fraction": 0.5, "add_fraction": 0.1,
    }
    base.update(kw)
    return base


# ── 样本定义（spec：数据段+DNA+引擎参数；gen 键直接喂 gen_ohlcv）──

SAMPLES: Dict[str, Dict[str, Any]] = {
    "pct_long": {
        "gen": {"scenario": "volatile", "seed": 1301, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                _gene("BB", {"period": 20, "std": 2}, "entry_trigger",
                      "upper", {"type": "price_above"}),
                _gene("RSI", {"period": 14}, "entry_guard", "rsi",
                      {"type": "lt", "threshold": 95}),
                _gene("BB", {"period": 20, "std": 2}, "exit_trigger",
                      "middle", {"type": "price_below"}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(stop_loss=0.04, take_profit=0.06,
                                direction="long"),
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "atr_short": {
        "gen": {"scenario": "volatile", "seed": 202, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                _gene("BB", {"period": 20, "std": 2}, "entry_trigger",
                      "lower", {"type": "price_below"}),
                _gene("RSI", {"period": 14}, "exit_trigger", "rsi",
                      {"type": "gt", "threshold": 55}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(stop_loss=2.0, take_profit=3.0,
                                direction="short", sl_mode="atr"),
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "trail_lock": {
        "gen": {"scenario": "trend", "seed": 303, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                _gene("EMA", {"period": 21}, "entry_trigger", "ema",
                      {"type": "price_above"}),
                _gene("EMA", {"period": 21}, "exit_trigger", "ema",
                      {"type": "price_below"}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(stop_loss=0.03, direction="long",
                                sl_mode="trail"),
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "series_line": {
        "gen": {"scenario": "normal", "seed": 404, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                _gene("EMA", {"period": 21}, "entry_trigger", "ema",
                      {"type": "price_above"}),
                _gene("RSI", {"period": 14}, "exit_trigger", "rsi",
                      {"type": "gt", "threshold": 70}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(
                direction="long", sl_mode="series", stop_loss=0.05,
                sl_series_source={"indicator": "BB", "params": {"period": 20, "std": 2},
                                  "field": "lower"}),
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "exit_family": {
        "gen": {"scenario": "normal", "seed": 505, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                _gene("RSI", {"period": 14}, "entry_trigger", "rsi",
                      {"type": "lt", "threshold": 45}),
                _gene("RSI", {"period": 14}, "reduce_trigger", "rsi",
                      {"type": "gt", "threshold": 58}),
                _gene("RSI", {"period": 14}, "exit_trigger", "rsi",
                      {"type": "gt", "threshold": 62}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(
                direction="long", stop_loss=0.06, position_size=0.6,
                max_hold=6, time_exit_utc=600, max_position_loss=1500.0),
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "exposure_add": {
        "gen": {"scenario": "trend", "seed": 606, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                _gene("EMA", {"period": 21}, "entry_trigger", "ema",
                      {"type": "price_above"}),
                _gene("EMA", {"period": 7}, "add_trigger", "ema",
                      {"type": "price_above"}),
                _gene("EMA", {"period": 7}, "reduce_trigger", "ema",
                      {"type": "price_below"}),
                _gene("EMA", {"period": 21}, "exit_trigger", "ema",
                      {"type": "price_below"}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(
                direction="long", position_size=0.1, stop_loss=0.05,
                max_exposure=0.25, add_fraction=0.1),
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "risk_budget_cap": {
        "gen": {"scenario": "volatile", "seed": 707, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                _gene("EMA", {"period": 21}, "entry_trigger", "ema",
                      {"type": "price_above"}),
                _gene("EMA", {"period": 21}, "exit_trigger", "ema",
                      {"type": "price_below"}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(
                direction="long", sl_mode="pct", stop_loss=0.08,
                position_mode="risk_budget", risk_fraction=0.1,
                leverage=1, max_exposure=1.0),
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "swallow_conflict": {
        "gen": {"scenario": "normal", "seed": 808, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                # 同条件进出场：RSI>50 的 bar 上 entries∧exits 同真——
                # 互斥清洗吞 entry（swallowed·entry_exit_conflict 面）
                _gene("RSI", {"period": 14}, "entry_trigger", "rsi",
                      {"type": "gt", "threshold": 50}),
                _gene("RSI", {"period": 14}, "exit_trigger", "rsi",
                      {"type": "gt", "threshold": 50}),
                # 同条件加仓+抑制：触发 bar 全被抑制（swallowed·
                # add_inhibited 面）
                _gene("RSI", {"period": 14}, "add_trigger", "rsi",
                      {"type": "gt", "threshold": 50}),
                _gene("RSI", {"period": 14}, "add_inhibit", "rsi",
                      {"type": "gt", "threshold": 50}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(direction="long"),
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "mtf_gate": {
        "gen": {"scenario": "normal", "seed": 909, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        # 趋势层 1d：EMA 多头排列为方向/共振源；执行层 4h RSI 触发
        "extra_timeframes": {
            "1d": {"scenario": "trend", "seed": 910, "n": 120,
                   "base_price": 40000.0, "freq": "1d"},
        },
        "dna": {
            "layers": [
                {
                    "timeframe": "1d", "role": "trend",
                    "signal_genes": [
                        _gene("EMA", {"period": 20}, "entry_trigger", "ema",
                              {"type": "price_above"}),
                        _gene("EMA", {"period": 20}, "exit_trigger", "ema",
                              {"type": "price_below"}),
                    ],
                    "logic_genes": _logic(),
                },
                {
                    "timeframe": "4h",
                    "signal_genes": [
                        _gene("RSI", {"period": 14}, "entry_trigger", "rsi",
                              {"type": "lt", "threshold": 40}),
                        _gene("RSI", {"period": 14}, "exit_trigger", "rsi",
                              {"type": "gt", "threshold": 65}),
                    ],
                    "logic_genes": _logic(),
                },
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(direction="long", stop_loss=0.05),
            "mtf_mode": "direction+confluence",
            "confluence_threshold": 0.3,
            "signal_sources": [{"type": "rule"}],
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "liq_small_cash": {
        "gen": {"scenario": "liquidation_edge", "seed": 1010, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        "dna": {
            "signal_genes": [
                # 近恒真入场（RSI>20）：清算后再入场路径可达，资金
                # 不足拒绝面（31 码）与清算强平面（22 码）同批覆盖
                _gene("RSI", {"period": 14}, "entry_trigger", "rsi",
                      {"type": "gt", "threshold": 20}),
                _gene("RSI", {"period": 14}, "exit_trigger", "rsi",
                      {"type": "gt", "threshold": 85}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(direction="long", stop_loss=0.05,
                                leverage=8, position_size=0.9),
        },
        "engine": {"init_cash": 10000.0, "fee": 0.001, "slippage": 0.0005},
    },
    "funding_batch": {
        "gen": {"scenario": "normal", "seed": 1111, "n": 400,
                "base_price": 40000.0, "freq": "4h"},
        # 资金费大额段：逐 bar 0.001（8h 结算连续折算口径下的大额
        # 费率——权益曲线含费）
        "extra_cols": {"funding_rate": 0.001},
        "dna": {
            "signal_genes": [
                _gene("EMA", {"period": 21}, "entry_trigger", "ema",
                      {"type": "price_above"}),
                _gene("EMA", {"period": 21}, "exit_trigger", "ema",
                      {"type": "price_below"}),
            ],
            "logic_genes": _logic(),
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": _risk(direction="long", stop_loss=0.04),
        },
        "batch_variant": {
            "risk_overrides": {"stop_loss": 0.06, "take_profit": 0.08},
        },
        "engine": {"init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005},
    },
}


def build_sample(name: str) -> Dict[str, Any]:
    """样本名 → 可执行组合（df/dna/引擎参数；纯函数每调用重建）。

    Returns:
        {"df": 执行轴 OHLCV DataFrame, "dfs_by_timeframe": dict|None,
         "dna": StrategyDNA, "init_cash/fee/slippage": float}
        batch 样本另含 "batch_dnas": [StrategyDNA, ...]（第二 DNA=
        risk_overrides 变体——多列对齐断言面）。
    """
    from core.strategy.dna import StrategyDNA

    spec = SAMPLES[name]
    gen = dict(spec["gen"])
    df = gen_ohlcv(**gen)
    for col, val in (spec.get("extra_cols") or {}).items():
        df[col] = float(val)

    dfs_by_timeframe = None
    if spec.get("extra_timeframes"):
        dfs_by_timeframe = {gen["freq"]: df}
        for tf, tf_gen in spec["extra_timeframes"].items():
            dfs_by_timeframe[tf] = gen_ohlcv(**tf_gen)

    dna = StrategyDNA.from_dict(spec["dna"])
    out = {
        "df": df, "dfs_by_timeframe": dfs_by_timeframe, "dna": dna,
        "init_cash": spec["engine"]["init_cash"],
        "fee": spec["engine"]["fee"],
        "slippage": spec["engine"]["slippage"],
    }
    if "batch_variant" in spec:
        import copy
        d2 = copy.deepcopy(spec["dna"])
        d2["risk_genes"].update(spec["batch_variant"]["risk_overrides"])
        out["batch_dnas"] = [dna, StrategyDNA.from_dict(d2)]
    return out


def sample_names() -> list[str]:
    return list(SAMPLES.keys())
