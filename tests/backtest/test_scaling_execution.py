"""分批执行面测试（0.4.2 R3，scaled-position-actions 规格，B4+B5 用例组）。

覆盖：executor 三入口 ADD_INHIBIT NOT 组合一致性（同一 DNA 三入口 adds
逐位一致+NOT 三边界）；引擎独立比例（串用修正锚 50% 非 30%/加仓手工
核算/敞口超限跳过/缺省完备 0.5-0.1-1.0）；零触达锚；同 bar 优先序。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)
from core.strategy.executor import (
    SignalSet,
    batch_signal_sets,
    dna_to_signal_set,
    evaluate_layer,
)
from core.strategy.dna import TimeframeLayer


def mk_df(n=60, seed=5):
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, n))
    idx = pd.DatetimeIndex(
        pd.date_range("2024-01-01", periods=n, freq="4h"), name="timestamp")
    return pd.DataFrame({
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": rng.uniform(100, 900, n),
    }, index=idx)


def mk_gene(role, threshold=50.0):
    return SignalGene("RSI", {"period": 14}, role,
                      condition={"type": "lt", "threshold": threshold})


def mk_scaling_dna(inhibit_threshold=None, **risk):
    """ADD_TRIGGER + ADD_GUARD + （可选）ADD_INHIBIT 的分批 DNA。"""
    genes = [
        mk_gene(SignalRole.ENTRY_TRIGGER),
        mk_gene(SignalRole.EXIT_TRIGGER, threshold=80.0),
        mk_gene(SignalRole.ADD_TRIGGER, threshold=60.0),
        mk_gene(SignalRole.ADD_GUARD, threshold=70.0),
    ]
    if inhibit_threshold is not None:
        genes.append(mk_gene(SignalRole.ADD_INHIBIT,
                             threshold=inhibit_threshold))
    return StrategyDNA(
        signal_genes=genes, logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(),
        risk_genes=RiskGenes(**risk))


def _with_rsi(df, period=14):
    """附 RSI 列（指标求值链消费）。"""
    from core.features.indicators import _compute_indicator
    for col, series in _compute_indicator(df, "RSI", {"period": period}).items():
        df[col] = series
    return df


class TestInhibitorThreeEntries:
    """executor 三入口 NOT 组合一致性（B4-21 批量路径守护不拆任务）。"""

    @pytest.mark.parametrize("inhibit_threshold,case", [
        (99.0, "全抑制（RSI<99 几乎恒成立 → 抑制常开 → adds 恒 False）"),
        (55.0, "部分抑制（RSI 跨阈值 → 部分被抑制）"),
        (None, "无抑制（对照组——无 ADD_INHIBIT 基因）"),
    ])
    def test_three_entries_adds_consistent(self, inhibit_threshold, case):
        df = _with_rsi(mk_df())
        dna = mk_scaling_dna(inhibit_threshold)
        # 入口 1：dna_to_signal_set
        ss1 = dna_to_signal_set(dna, df)
        # 入口 2：batch_signal_sets（批量组装路径）
        ss2 = batch_signal_sets([dna], df)[0]
        # 入口 3：evaluate_layer（层求值——单周期 auto-wrap 同构）
        layer = TimeframeLayer(timeframe="4h", signal_genes=dna.signal_genes,
                               logic_genes=LogicGenes())
        ss3 = evaluate_layer(layer, df)
        for a, b, c in zip(ss1.adds, ss2.adds, ss3.adds):
            assert bool(a) == bool(b) == bool(c), case
        # NOT 语义：抑制条件（lt threshold → RSI<threshold 成立）为真处
        # adds 必 False——任一抑制成立即不加仓
        if inhibit_threshold is not None:
            rsi = df["rsi_14"]
            inhibited = rsi < inhibit_threshold  # lt 条件成立=抑制生效
            assert not (ss1.adds & inhibited).any(), \
                "抑制成立的 bar 不得产生加仓信号"

    def test_not_is_boolean_not_event_inversion(self):
        """cross 类锚点（B4-19）：抑制=布尔 NOT（掩码取反），非事件取反。
        构造抑制条件为 cross_below 型——「发生下穿的 bar」被抑制不加仓、
        「持续位于下方」的 bar 不受抑制（事件语义取反必错此例）。"""
        df = mk_df()
        # 覆写 rsi_14 为构造序列（dna_to_signal_set 只消费列）：bar0-19
        # 高位 60 → bar20 下穿到 40 → bar21-39 持续 40 → bar40 回 60
        rsi_seq = [60.0] * 20 + [40.0] * 20 + [60.0] * 20
        df["rsi_14"] = rsi_seq
        genes = [
            mk_gene(SignalRole.ENTRY_TRIGGER),
            mk_gene(SignalRole.EXIT_TRIGGER, threshold=80.0),
            mk_gene(SignalRole.ADD_TRIGGER, threshold=45.0),  # 低位段成立
            SignalGene("RSI", {"period": 14}, SignalRole.ADD_INHIBIT,
                       condition={"type": "cross_below", "threshold": 50.0}),
        ]
        dna = StrategyDNA(signal_genes=genes, logic_genes=LogicGenes(),
                          execution_genes=ExecutionGenes())
        ss = dna_to_signal_set(dna, df)
        rsi = df["rsi_14"]  # 覆写后的列（DatetimeIndex——与 adds 对齐）
        cross_bars = (rsi < 50.0) & (rsi.shift(1) >= 50.0)  # bar20
        below_bars = (rsi < 50.0) & ~cross_bars              # bar21-39
        assert cross_bars.sum() == 1 and bool(cross_bars.iloc[20])  # 仅 bar20 下穿
        # 事件语义锚：下穿发生的 bar 不加仓（抑制生效）
        assert not (ss.adds & cross_bars).any(), \
            "发生下穿的 bar 不得产生加仓信号"
        # 持续位于下方的 bar 不受抑制（触发+门控成立即加仓）——若实现
        # 误按「位于下方」抑制（布尔取反 cross_below→below），这 19 个
        # bar 全被错杀；若误取反映射成 ADD_GUARD 门控，则门控恒关同样
        # 全错杀——两个错误方向都被本锚识别
        assert (ss.adds & below_bars).sum() == 19, \
            f"持续下方 bar 应照常加仓（19 个），实际 {(ss.adds & below_bars).sum()}"
        # 判定力锚：取反映射（把抑制当 ADD_GUARD）adds 分布必须不同
        guard_genes = [
            mk_gene(SignalRole.ENTRY_TRIGGER),
            mk_gene(SignalRole.EXIT_TRIGGER, threshold=80.0),
            mk_gene(SignalRole.ADD_TRIGGER, threshold=45.0),
            SignalGene("RSI", {"period": 14}, SignalRole.ADD_GUARD,
                       condition={"type": "cross_below", "threshold": 50.0}),
        ]
        guard_dna = StrategyDNA(
            signal_genes=guard_genes, logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes())
        guard_ss = dna_to_signal_set(guard_dna, df)
        assert not ss.adds.equals(guard_ss.adds), \
            "ADD_INHIBIT 与 ADD_GUARD 映射产物必须可区分（非取反形）"


class TestEngineScalingExecution:
    """引擎分批执行（B5 22-28）：独立比例/敞口/优先序/零触达。"""

    def _sig(self, n, entries, reduces=None, adds=None):
        e = pd.Series(False, index=pd.RangeIndex(n))
        for i in entries:
            e[i] = True
        x = pd.Series(False, index=pd.RangeIndex(n))  # exits 独立（非 reduce）
        r = pd.Series(False, index=pd.RangeIndex(n))
        for i in (reduces or []):
            r[i] = True
        a = pd.Series(False, index=pd.RangeIndex(n))
        for i in (adds or []):
            a[i] = True
        return SignalSet(entries=e, exits=x, adds=a,
                         reduces=r, entry_direction=None)

    def test_reduce_independent_fraction(self):
        """串用修正锚（B5-22）：exit_fraction=0.5+position_size=0.3 →
        减仓 50% 而非 30%。手工核算：bar1 入场 close=100 全仓 0.3（30%
        权益名义），bar2 reduce 50% → 持仓剩 15% 权益名义。"""
        closes = [100.0] * 8
        df = pd.DataFrame({
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [100.0] * 8,
        }, index=pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=8, freq="4h"),
            name="timestamp"))
        dna = StrategyDNA(
            signal_genes=[mk_gene(SignalRole.ENTRY_TRIGGER),
                          mk_gene(SignalRole.EXIT_TRIGGER, threshold=99.0)],
            logic_genes=LogicGenes(), execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(exit_fraction=0.5, position_size=0.3))
        eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        r = eng.run(dna, df, signal_set=self._sig(8, [0], reduces=[3]))
        assert r.trades_df is not None and len(r.trades_df) >= 1
        # 首笔 reduce：size=持仓×0.5（非 ×0.3）——持仓 30000 名义 → 减 15000
        first = r.trades_df.iloc[0]
        assert abs(abs(first["Size"]) - 150.0) < 1e-6, \
            f"reduce 应减 50%（150 股），实际 {first['Size']}"

    def test_reduce_default_fraction(self):
        """缺省完备：无 exit_fraction → 0.5（不是 position_size 串用）。"""
        closes = [100.0] * 8
        df = pd.DataFrame({
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [100.0] * 8,
        }, index=pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=8, freq="4h"),
            name="timestamp"))
        dna = StrategyDNA(
            signal_genes=[mk_gene(SignalRole.ENTRY_TRIGGER),
                          mk_gene(SignalRole.EXIT_TRIGGER, threshold=99.0)],
            logic_genes=LogicGenes(), execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(position_size=0.3))
        eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        r = eng.run(dna, df, signal_set=self._sig(8, [0], reduces=[3]))
        first = r.trades_df.iloc[0]
        assert abs(abs(first["Size"]) - 150.0) < 1e-6  # 0.5 缺省

    def test_add_fraction_default_uses_tenth(self):
        """add_fraction 缺省完备（手工 DNA 无字段）：加仓比例按 0.1
        （权益分母）——bar1 入场 30%（300 股）、bar3 加仓 0.1×权益
        （100 股）→ 期末平仓 400 股（与显式声明 0.1 逐位一致）。"""
        closes = [100.0] * 8
        df = pd.DataFrame({
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [100.0] * 8,
        }, index=pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=8, freq="4h"),
            name="timestamp"))
        dna = StrategyDNA(
            signal_genes=[mk_gene(SignalRole.ENTRY_TRIGGER),
                          mk_gene(SignalRole.EXIT_TRIGGER, threshold=99.0)],
            logic_genes=LogicGenes(), execution_genes=ExecutionGenes(),
            # add_fraction/max_exposure 均不声明（None 合法缺省态）
            risk_genes=RiskGenes(position_size=0.3))
        eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        r = eng.run(dna, df, signal_set=self._sig(8, [0], adds=[3]))
        last = r.trades_df.iloc[-1]
        assert abs(abs(last["Size"]) - 400.0) < 1e-6, \
            f"缺省 add_fraction=0.1 应加 100 股（300+100=400），实际 {last['Size']}"

    def test_add_fraction_manual_accounting(self):
        """加仓手工核算（B5-23）：add_fraction=0.1、分母=当前权益——
        bar1 入场 30%（30000 名义/300 股），bar3 加仓 10%（10000/100 股）
        → 持仓 400 股。"""
        closes = [100.0] * 8
        df = pd.DataFrame({
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [100.0] * 8,
        }, index=pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=8, freq="4h"),
            name="timestamp"))
        dna = StrategyDNA(
            signal_genes=[mk_gene(SignalRole.ENTRY_TRIGGER),
                          mk_gene(SignalRole.EXIT_TRIGGER, threshold=99.0)],
            logic_genes=LogicGenes(), execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(position_size=0.3, add_fraction=0.1,
                                 max_exposure=1.0))
        eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        r = eng.run(dna, df, signal_set=self._sig(8, [0], adds=[3]))
        # bar2: 入场 300 股（0.3）；bar4: 加仓（0.1×权益）——权益≈100000
        # （价格未动）→ 加 100 股 → 持仓 400 股；期末 exit 平 400
        trades = r.trades_df
        assert trades is not None and len(trades) >= 1
        last = trades.iloc[-1]
        assert abs(abs(last["Size"]) - 400.0) < 1e-6, \
            f"加仓后总持仓应 400 股（300+100），实际 {last['Size']}"

    def test_exposure_cap_skips_add(self):
        """敞口超限跳过（B5-24）：max_exposure=0.35（显式），持仓名义
        0.30，add_fraction=0.1（将使名义 0.40>0.35）→ 该 bar 不加仓。"""
        closes = [100.0] * 8
        df = pd.DataFrame({
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [100.0] * 8,
        }, index=pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=8, freq="4h"),
            name="timestamp"))
        # max_exposure=0.35 ≤ leverage=1 合法
        dna = StrategyDNA(
            signal_genes=[mk_gene(SignalRole.ENTRY_TRIGGER),
                          mk_gene(SignalRole.EXIT_TRIGGER, threshold=99.0)],
            logic_genes=LogicGenes(), execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(position_size=0.3, add_fraction=0.1,
                                 max_exposure=0.35))
        eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        r = eng.run(dna, df, signal_set=self._sig(8, [0], adds=[3]))
        last = r.trades_df.iloc[-1]
        assert abs(abs(last["Size"]) - 300.0) < 1e-6, \
            f"超限加仓应被跳过（持仓保持 300），实际 {last['Size']}"

    def test_exposure_default_1(self):
        """敞口缺省 1.0：持仓 0.95+加 0.1 → 1.05>1.0 跳过（无声明上限
        亦生效——保护性约束默认开）。"""
        closes = [100.0] * 8
        df = pd.DataFrame({
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [100.0] * 8,
        }, index=pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=8, freq="4h"),
            name="timestamp"))
        dna = StrategyDNA(
            signal_genes=[mk_gene(SignalRole.ENTRY_TRIGGER),
                          mk_gene(SignalRole.EXIT_TRIGGER, threshold=99.0)],
            logic_genes=LogicGenes(), execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes(position_size=0.95, add_fraction=0.1))
        eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        r = eng.run(dna, df, signal_set=self._sig(8, [0], adds=[3]))
        last = r.trades_df.iloc[-1]
        assert abs(abs(last["Size"]) - 950.0) < 1e-6  # 0.95 权益，未加仓

    def test_zero_touchdown_legacy(self):
        """零触达锚（B5-27）：无分批基因存量策略回测与 main 基线一致
        ——既有 tests/backtest 全量为特征网（此处锚定 reduce/add 信号
        全 False 的 DNA 行为=无 reduce/add 事件）。"""
        df = _with_rsi(mk_df())
        dna = StrategyDNA(
            signal_genes=[mk_gene(SignalRole.ENTRY_TRIGGER),
                          mk_gene(SignalRole.EXIT_TRIGGER, threshold=80.0)],
            logic_genes=LogicGenes(), execution_genes=ExecutionGenes(),
            risk_genes=RiskGenes())
        ss = dna_to_signal_set(dna, df)
        assert not ss.adds.any() and not ss.reduces.any()

    def test_evolution_guard_no_scaling_roles(self):
        """进化守护（B5-28）：population 不产 ADD/REDUCE/ADD_INHIBIT
        角色（现状断言防回归）。"""
        from core.evolution.population import create_random_dna
        import random
        random.seed(42)
        bad = {SignalRole.ADD_TRIGGER, SignalRole.ADD_GUARD,
               SignalRole.ADD_INHIBIT, SignalRole.REDUCE_TRIGGER,
               SignalRole.REDUCE_GUARD}
        for _ in range(30):
            dna = create_random_dna()
            roles = {g.role for g in dna.signal_genes}
            assert not (roles & bad), f"进化产物含分批角色: {roles & bad}"
