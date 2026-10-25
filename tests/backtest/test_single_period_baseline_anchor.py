"""T③ 单周期回归基线锚（fix-mtf-execution-consistency 组 0 固化）。

修复前（组 0）在无本 change 提交的工作区采集：固定 seed 合成数据 +
代表性无标注 DNA（timeframe 全 None——纯单周期路径）经 dna_to_signal_set
的 entries/exits/adds/reduces 计数与序列 sha256 固化入资产文件。

组 1（R1+R1b）落地后本锚必须逐位仍绿——纯单周期策略信号逐字节不变
（proposal 1.2 回归基线第一条）。资产不匹配即行为回归，红锚归因程序：
- 先核对合成数据参数（seed/n_bars）与 DNA 定义未变；
- 再查 executor 单周期路径（非 MTF 分支）与指标计算是否被改动；
- 本锚只锁行为不定原因，修复引入的预期变化不应触碰无标注 DNA 路径
  （若触碰即违反「单周期零变化」边界，属实施缺陷而非资产过期）。

资产重生成（仅基线语义变化时，需 proposal 边界明示）：
    UPDATE_BASELINE=1 ./venv/bin/python -m pytest \
        tests/backtest/test_single_period_baseline_anchor.py -o addopts=""
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.strategy.dna import ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA
from core.strategy.executor import dna_to_signal_set

ASSET = Path(__file__).parent / "assets" / "single_period_baseline.json"
SEED = 20261024
N_BARS = 3600


def _synth_df(seed: int = SEED, n: int = N_BARS) -> pd.DataFrame:
    """确定性合成 30m OHLCV（随机游走价格×独立量能流）。"""
    rng = np.random.RandomState(seed)
    rets = rng.normal(0.0002, 0.008, n)
    close = 60000.0 * np.cumprod(1.0 + rets)
    open_ = np.empty(n)
    open_[0] = 60000.0
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, 0.004, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = np.abs(rng.normal(500, 150, n)) + 1.0
    idx = pd.date_range("2024-01-01", periods=n, freq="30min", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": volume}, index=idx)


def _baseline_dna() -> StrategyDNA:
    """代表性无标注 DNA：四 role×主要条件族（gt/lt/price_above/price_below）。

    基因 timeframe 全 None——单周期路径专属锚（带轴基因归 T①/T② 族）。
    """
    genes = [
        SignalGene("BB", {"period": 20, "std": 2}, SignalRole.ENTRY_TRIGGER,
                   "upper", {"type": "price_above"}),
        SignalGene("RVOL", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                   "rvol", {"type": "gt", "threshold": 1.2}),
        SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_GUARD,
                   "rsi", {"type": "lt", "threshold": 70}),
        SignalGene("BB", {"period": 20, "std": 2}, SignalRole.EXIT_TRIGGER,
                   "middle", {"type": "price_below"}),
        SignalGene("RSI", {"period": 14}, SignalRole.EXIT_GUARD,
                   "rsi", {"type": "gt", "threshold": 25}),
        SignalGene("RVOL", {"period": 20}, SignalRole.ADD_TRIGGER,
                   "rvol", {"type": "gt", "threshold": 2.0}),
        SignalGene("EMA", {"period": 21}, SignalRole.REDUCE_TRIGGER,
                   "ema", {"type": "price_below"}),
        SignalGene("EMA", {"period": 7}, SignalRole.EXIT_TRIGGER,
                   "ema", {"type": "price_above"}),
    ]
    return StrategyDNA(
        strategy_id="st_baseline_anchor_t3",
        signal_genes=genes,
        execution_genes=ExecutionGenes(timeframe="30m", symbol="BTCUSDT"),
        logic_genes=LogicGenes(),
        risk_genes=RiskGenes(direction="short", exit_fraction=0.5,
                             add_fraction=0.1),
        signal_sources=[{"type": "rule"}],
    )


def _digest(series: pd.Series) -> str:
    vals = series.fillna(False).astype(bool).to_numpy()
    return hashlib.sha256(vals.tobytes()).hexdigest()


def _compute() -> dict:
    df = _synth_df()
    dna = _baseline_dna()
    sig = dna_to_signal_set(dna, df, dfs_by_timeframe=None)
    return {
        "seed": SEED,
        "n_bars": N_BARS,
        "dna_json": dna.to_json(),
        "counts": {
            "entries": int(sig.entries.fillna(False).sum()),
            "exits": int(sig.exits.fillna(False).sum()),
            "adds": int(sig.adds.fillna(False).sum()),
            "reduces": int(sig.reduces.fillna(False).sum()),
        },
        "sha256": {
            "entries": _digest(sig.entries),
            "exits": _digest(sig.exits),
            "adds": _digest(sig.adds),
            "reduces": _digest(sig.reduces),
        },
    }


def test_single_period_baseline_anchor():
    """纯单周期信号逐字节不变（组 0 固化 → 组 1 验证同资产全绿）。"""
    if __import__("os").environ.get("UPDATE_BASELINE") == "1":
        ASSET.parent.mkdir(parents=True, exist_ok=True)
        payload = _compute()
        ASSET.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        pytest.skip("基线资产已重生成——请以正常模式复跑验证")
    assert ASSET.exists(), (
        "基线资产缺失：先 UPDATE_BASELINE=1 运行本文件固化（组 0 步骤）")
    expected = json.loads(ASSET.read_text())
    actual = _compute()
    assert actual["seed"] == expected["seed"]
    assert actual["n_bars"] == expected["n_bars"]
    assert actual["dna_json"] == expected["dna_json"], (
        "DNA 定义漂移——锚资产与本文件构造不一致（红锚归因见模块 docstring）")
    assert actual["counts"] == expected["counts"], (
        f"信号计数漂移: 期望 {expected['counts']} 实得 {actual['counts']}")
    assert actual["sha256"] == expected["sha256"], (
        "信号序列逐位漂移（计数同而序列变）——单周期路径行为回归")
