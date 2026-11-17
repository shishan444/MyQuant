"""回放测试共享装配（tests/replay 专用）。

合成 parquet 数据目录+临时库+策略行三件装配（与 tests/paper_trading/
_Harness 同源不同装配：本域无 FeedRegistry——回放驱动不经 registry
订阅，供数全走 ReplayFeeder）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.data.storage import save_parquet
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalRole,
    StrategyDNA,
)
from tests.helpers.data_factory import make_dna, make_ohlcv, make_signal_gene


class ReplayEnv:
    """合成数据+临时库+策略行装配（每个用例独立实例）。"""

    def __init__(self, tmp_path: Path, *, dna=None, n_bars=600,
                 freq="4h", seed=7, base_price=100.0):
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_ohlcv(n=n_bars, freq=freq, seed=seed,
                             base_price=base_price)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        self.dna = dna or make_dna()
        self.strategy_id = "st_replay_test"
        save_strategy(
            self.db, strategy_id=self.strategy_id,
            dna_json=self.dna.to_json(), symbol="BTCUSDT", timeframe="4h")

    def add_mtf_aux(self, freq="1d", seed=11, n=320):
        """补辅周期 parquet（MTF 用例——主 4h 辅 1d；1d 轴 warmup
        200 floor+区间覆盖故独立长度，与主帧时间轴自然交叠）。"""
        aux = make_ohlcv(n=n, freq=freq, seed=seed, base_price=100.0)
        save_parquet(aux, self.data_dir / "BTCUSDT_1d.parquet")
        return aux

    def range(self, head_bars: int = 300) -> tuple[str, str]:
        """回放区间（起点=倒数 head_bars 根，终点=末根）。"""
        idx = self.df.index
        return idx[-head_bars].isoformat(), idx[-1].isoformat()


def make_reduce_loop_dna(
    *, exit_fraction: float = 0.5, position_size: float = 0.1,
    leverage: int = 10,
) -> StrategyDNA:
    """入场+减仓双恒触发策略（锚②构造：RSI>0 恒真）。

    减仓信号每根触发——衰减序列在终止条款下有限步归零离场。
    """
    gene_entry = make_signal_gene(
        indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt", condition_value=0)
    gene_exit = make_signal_gene(  # 永假（RSI<200 恒真）——衰减路径
        indicator="RSI", role=SignalRole.EXIT_TRIGGER,   # 不被 exit 打断
        condition_type="gt", condition_value=200)
    gene_reduce = make_signal_gene(
        indicator="RSI", role=SignalRole.REDUCE_TRIGGER,
        condition_type="gt", condition_value=0)
    return StrategyDNA(
        signal_genes=[gene_entry, gene_exit, gene_reduce],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.2,
            position_size=position_size, leverage=leverage,
            direction="long", exit_fraction=exit_fraction),
    )


def make_mtf_dna_main4h() -> StrategyDNA:
    """主 4h 辅 1d 的 MTF dna（两层 RSI lt/gt——沿 data_factory
    make_mtf_dna 形制，时间框定制为主 4h+辅 1d）。"""
    from tests.helpers.data_factory import make_mtf_dna

    return make_mtf_dna(timeframes=("1d", "4h"))
