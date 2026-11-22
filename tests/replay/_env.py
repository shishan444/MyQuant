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


def make_add_loop_dna(
    *, direction: str = "long", position_size: float = 0.1,
    leverage: int = 2,
) -> StrategyDNA:
    """入场+加仓双恒触发策略（fix-replay-task-lifecycle G1-3 构造：
    RSI>0 恒真）。

    加仓信号每根触发——持仓沿保证金上限累积（资金不足即拒，两侧
    同拒仍是对账面）；exit 永假（RSI>200）——退出不干扰 add 路径。
    direction 可传 short（开/加×多/空矩阵补角）。
    """
    gene_entry = make_signal_gene(
        indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt", condition_value=0)
    gene_exit = make_signal_gene(  # 永假（RSI<200 恒真）——add 路径
        indicator="RSI", role=SignalRole.EXIT_TRIGGER,   # 不被 exit 打断
        condition_type="gt", condition_value=200)
    gene_add = make_signal_gene(
        indicator="RSI", role=SignalRole.ADD_TRIGGER,
        condition_type="gt", condition_value=0)
    return StrategyDNA(
        signal_genes=[gene_entry, gene_exit, gene_add],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.2,
            position_size=position_size, leverage=leverage,
            direction=direction),
    )


def make_add_reduce_loop_dna(
    *, direction: str = "short", position_size: float = 0.1,
    leverage: int = 2, exit_fraction: float = 0.5,
) -> StrategyDNA:
    """入场+加仓+减仓策略（fix-replay-task-lifecycle G1-3 构造）：
    意图决策序每 bar 单意图且 reduce 先于 add（kernel/intents.py
    决策序——同 bar 并存时 add 永不产出），故触发面错开：add 恒真
    （RSI>0）+reduce 偶发（RSI>60），对账轴覆盖 add 与 reduce 两
    动作路径。

    exit 永假——退出不干扰加减路径纯度。
    """
    gene_entry = make_signal_gene(
        indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt", condition_value=0)
    gene_exit = make_signal_gene(
        indicator="RSI", role=SignalRole.EXIT_TRIGGER,
        condition_type="gt", condition_value=200)
    gene_add = make_signal_gene(
        indicator="RSI", role=SignalRole.ADD_TRIGGER,
        condition_type="gt", condition_value=0)
    gene_reduce = make_signal_gene(
        indicator="RSI", role=SignalRole.REDUCE_TRIGGER,
        condition_type="gt", condition_value=60)
    return StrategyDNA(
        signal_genes=[gene_entry, gene_exit, gene_add, gene_reduce],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.2,
            position_size=position_size, leverage=leverage,
            direction=direction, exit_fraction=exit_fraction),
    )


def make_mtf_dna_main4h() -> StrategyDNA:
    """主 4h 辅 1d 的 MTF dna（两层 RSI lt/gt——沿 data_factory
    make_mtf_dna 形制，时间框定制为主 4h+辅 1d）。"""
    from tests.helpers.data_factory import make_mtf_dna

    return make_mtf_dna(timeframes=("1d", "4h"))


def make_mtf_loop_dna_main4h() -> StrategyDNA:
    """恒触发 MTF dna（主 4h 辅 1d 两层 entry RSI>0 恒真+exit 永假）
    ——保证两轴都有成交，等价性对账有实质面（CARD-01 回归构造：
    阈值型两层 AND 在合成数据下难同时触发，成交 0 的对账是弱证据）。"""
    from core.strategy.dna import ExecutionGenes, TimeframeLayer
    from tests.helpers.data_factory import make_signal_gene

    layers = []
    for tf in ("1d", "4h"):
        entry = make_signal_gene(
            indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
            condition_type="gt", condition_value=0)
        exit_gene = make_signal_gene(
            indicator="RSI", role=SignalRole.EXIT_TRIGGER,
            condition_type="gt", condition_value=200)
        layers.append(TimeframeLayer(
            timeframe=tf,
            signal_genes=[entry, exit_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND")))
    return StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.2, position_size=0.1,
            leverage=2, direction="long"),
        layers=layers)


def make_mtf_threshold_dna_main4h(
    *, main_threshold: float = 55.0, aux_threshold: float = 57.0,
) -> StrategyDNA:
    """阈值型 MTF dna（fix-replay-task-lifecycle G1-1 判别力构造）：
    主 4h 层 entry RSI<55 × 辅 1d 层 entry RSI<57 两层 AND——阈值取
    两轴 RSI 分布中位附近（主 p50≈48/辅 p50≈57），信号面非零且对
    辅帧末端可见集敏感（末端根阈值侧翻转即信号翻转——恒真构造
    对可见集差一不敏感，无判别力）。exit 永假（纯 entry 对账面）。"""
    from core.strategy.dna import ExecutionGenes, TimeframeLayer
    from tests.helpers.data_factory import make_signal_gene

    layers = []
    for tf, threshold in (("1d", aux_threshold), ("4h", main_threshold)):
        entry = make_signal_gene(
            indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
            condition_type="lt", condition_value=threshold)
        exit_gene = make_signal_gene(
            indicator="RSI", role=SignalRole.EXIT_TRIGGER,
            condition_type="gt", condition_value=200)
        layers.append(TimeframeLayer(
            timeframe=tf,
            signal_genes=[entry, exit_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND")))
    return StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe="4h", symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.2, position_size=0.1,
            leverage=2, direction="long"),
        layers=layers)


def make_gene_tf_dna(
    *, exec_tf: str = "1h", gene_tf: str = "4h",
    position_size: float = 0.1, leverage: int = 2,
) -> StrategyDNA:
    """基因级标注形制 dna（fix-replay-gap-alignment T1 夹具——缺陷 A
    生产形制：无 layers、is_mtf=False、执行 1h+entry 基因 timeframe=
    "4h"，dna.timeframes 退化为 [执行周期] 单元素）。

    entry 双基因 AND：4h 轴 RSI>0 恒真（掩码投影状态窗）×基准轴
    RSI>0 恒真；exit 永假（RSI>200 恒假）——单次进场后持有到区间
    末，T1-a 投影时点断言与 T1-c 装载等价对账的稳定面。"""
    entry_hi = make_signal_gene(
        indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt", condition_value=0, timeframe=gene_tf)
    entry_base = make_signal_gene(
        indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt", condition_value=0)
    gene_exit = make_signal_gene(
        indicator="RSI", role=SignalRole.EXIT_TRIGGER,
        condition_type="gt", condition_value=200)
    return StrategyDNA(
        signal_genes=[entry_hi, entry_base, gene_exit],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=exec_tf, symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.2, position_size=position_size,
            leverage=leverage, direction="long"))


def make_layer_equiv_dna(
    *, exec_tf: str = "1h", hi_tf: str = "4h",
    position_size: float = 0.1, leverage: int = 2,
) -> StrategyDNA:
    """层级形制等价 dna（T1-c 对照轴）：与 make_gene_tf_dna 同语义
    条件的层级表达——基准层 entry RSI>0 恒真×高周期层 entry RSI>0
    恒真（层间 cross_layer_logic 缺省 AND），双侧 exit 永假。

    等价性边界（T1-c 注释同款）：4h 侧投影两路语义不同（层路=
    pulse 脉冲单 bar/基因路=掩码状态窗），恒真条件+对齐网格下两路
    首触发同 bar（pulse 落位 bar=状态窗起始 bar）——逐位一致断言
    仅在该条件形制下成立。"""
    from core.strategy.dna import ExecutionGenes, TimeframeLayer
    from tests.helpers.data_factory import make_signal_gene

    layers = []
    for tf in (exec_tf, hi_tf):
        entry = make_signal_gene(
            indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
            condition_type="gt", condition_value=0)
        exit_gene = make_signal_gene(
            indicator="RSI", role=SignalRole.EXIT_TRIGGER,
            condition_type="gt", condition_value=200)
        layers.append(TimeframeLayer(
            timeframe=tf,
            signal_genes=[entry, exit_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND")))
    return StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=exec_tf, symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.2, position_size=position_size,
            leverage=leverage, direction="long"),
        layers=layers)


def make_single_layer_dna(*, exec_tf: str = "4h") -> StrategyDNA:
    """单层层级形制 dna（T1-h 回归锚）：layers=[执行周期单层]，
    is_mtf=True 而 required_aux_timeframes=空集——或语义扩门的回归
    陷阱形制（纯判空换源会使它改道单周期分支路由，L917「等同」是
    语义宣称非逐字节保证）。

    现状基准（扩门前实测）：_assemble_mtf_frames 返回 {执行周期:
    main_df} 非 None（MTF 分支路由）；扩门后同形制同结果。"""
    from core.strategy.dna import ExecutionGenes, TimeframeLayer
    from tests.helpers.data_factory import make_signal_gene

    entry = make_signal_gene(
        indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
        condition_type="gt", condition_value=0)
    exit_gene = make_signal_gene(
        indicator="RSI", role=SignalRole.EXIT_TRIGGER,
        condition_type="gt", condition_value=200)
    return StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=exec_tf, symbol="BTCUSDT"),
        risk_genes=RiskGenes(
            stop_loss=0.05, take_profit=0.2, position_size=0.1,
            leverage=2, direction="long"),
        layers=[TimeframeLayer(
            timeframe=exec_tf,
            signal_genes=[entry, exit_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"))])
