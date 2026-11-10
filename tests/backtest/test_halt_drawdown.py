"""账户级硬熔断（沙盒路）引擎行为（sandbox-and-report-supplement A4）。

D3①：halt_drawdown_pct（(0,1)，未配置=不启用）；执行内嵌判定（权益
峰值跟踪，触发后 entries 屏蔽，持仓/平仓照常）；响应三态（未配置
无键/未触发 false/触发 true＋halted_at＋halt_reason）；达阈值 ≥ 含
等号。

合成曲线锚（tasks A4）：100→120→108 回撤恰 10%、阈值 0.1 触发；
恰差一档（108.01）不触发。判定口径=引擎组合当 bar 价值（原始口径
——杠杆放大前；测试曲线无杠杆，两口径一致）。
"""

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
from core.strategy.executor import SignalSet

N = 60


def _df(prices) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="4h",
                        tz="UTC", name="timestamp")
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({
        "open": p, "high": p * 1.001, "low": p * 0.999, "close": p,
        "volume": np.full(len(p), 100.0), "trades": 10,
    }, index=idx)


def _sig(entry_bars, exit_bars=(), add_bars=(), n=None):
    n = n or N
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    z = pd.Series(0.0, index=idx)
    e, x, a = z.copy(), z.copy(), z.copy()
    for b in entry_bars:
        e.iloc[b] = 1.0
    for b in exit_bars:
        x.iloc[b] = 1.0
    for b in add_bars:
        a.iloc[b] = 1.0
    return SignalSet(entries=e, exits=x, adds=a, reduces=z.copy())


def _gene():
    return SignalGene(
        indicator="RSI", params={"period": 14},
        role=SignalRole.ENTRY_TRIGGER, field_name="rsi_14",
        condition={"type": "lt", "threshold": 999})


def _dna():
    return StrategyDNA(
        signal_genes=[_gene()], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(direction="long", position_size=1.0,
                             leverage=1))


def _prices(path_108=108.0):
    """权益合成曲线：bar0-9 恒 100（bar1 信号 bar2 入场 100%＝100 股）
    →bar10-19 恒 120（权益 12000）→bar20+ 恒 path_108（权益回落）。"""
    return ([100.0] * 10 + [120.0] * 10 + [path_108] * 40)


def _run(prices, sig, *, halt=None, n=None, dna=None, task_leverage=None,
         task_margin_cap=None):
    df = _df(prices)
    return BacktestEngine(
        init_cash=10000, fee=0.0, slippage=0.0,
        task_leverage=task_leverage, task_margin_cap=task_margin_cap,
        halt_drawdown_pct=halt).run(
        dna if dna is not None else _dna(), df,
        dfs_by_timeframe={"4h": df}, signal_set=sig)


class TestThreeStates:
    """响应三态：未配置无键/未触发 false/触发 true＋两附键。"""

    def test_unset_no_key(self):
        res = _run(_prices(), _sig([1]))
        assert res.halt_info is None, "未配置不写键"

    def test_configured_not_triggered_false(self):
        """配置阈值 0.5（50% 回撤）但曲线回撤 10%——未触发
        {halted: false}（无 halted_at 键）。"""
        res = _run(_prices(), _sig([1]), halt=0.5)
        assert res.halt_info == {"halted": False}

    def test_triggered_true_with_at_and_reason(self):
        """阈值 0.1、曲线 120→108 回撤恰 10%（含等号）——触发
        halted:true＋halted_at（触发 bar 时刻）＋halt_reason。"""
        res = _run(_prices(), _sig([1]), halt=0.1)
        info = res.halt_info
        assert info is not None and info["halted"] is True
        assert info["halt_reason"] == (
            "equity_drawdown_from_peak_ge_halt_drawdown_pct")
        # 触发 bar=回撤首个判定 bar（bar20，权益 10800）
        assert info["halted_at"].startswith("2024-01-04")


class TestInclusiveEquality:
    """含等号两侧：恰 10% 触发；恰差一档不触发。"""

    def test_exactly_at_threshold_triggers(self):
        """120→108：回撤 (12000−10800)/12000=0.10 ≥ 0.1——触发
        （≥含等号，spec 显式）。"""
        res = _run(_prices(108.0), _sig([1]), halt=0.1)
        assert res.halt_info["halted"] is True

    def test_one_tick_below_not_triggers(self):
        """120→108.01：回撤 0.0999…＜ 0.1——不触发。"""
        res = _run(_prices(108.01), _sig([1]), halt=0.1)
        assert res.halt_info == {"halted": False}


class TestEntryBlockedExitAlive:
    """触发后停开新仓但既有平仓信号仍成交。"""

    def test_exit_fills_after_halt_entry_blocked(self):
        """bar1 入场→bar20 触发→bar25 出场（应成交）→bar30 再入场
        （应被屏蔽，码 36）——出场照常、开新仓停。"""
        res = _run(_prices(), _sig([1, 30], exit_bars=[25]), halt=0.1)
        trades = res.trades_df
        # 唯一成交=首笔（入场 bar2、出场 bar26）——bar31 入场被屏蔽
        assert len(trades) == 1
        assert res.halt_info["halted"] is True
        # 出场成交价=触发后出场 bar 开盘价（108——正常成交非拒单）
        assert float(trades["Avg Exit Price"].iloc[0]) == pytest.approx(
            108.0)
        tb = res.trace_buf
        assert tb is not None and 36.0 in set(tb[:, 0]), \
            "被屏蔽的入场写留痕码 36"

    def test_no_new_entry_after_halt_even_if_flat(self):
        """触发后（持仓中触发→出场→空仓）再入场被屏蔽——halted
        单向置位不随空仓回落。出场在触发后（bar25 信号→bar26 成交
        @108），bar30 再入场屏蔽。"""
        res = _run(_prices(), _sig([1, 30], exit_bars=[25]), halt=0.1)
        # bar26 出场成交、bar31 入场被屏蔽：唯一一笔交易
        assert len(res.trades_df) == 1
        assert res.halt_info["halted"] is True
        assert 36.0 in set(res.trace_buf[:, 0])


class TestAddsAliveAfterHalt:
    """halted 后加仓照常（D3① 语义=仅 entries 屏蔽——加减仓是持仓
    管理非开新仓；引擎加仓通道不经熔断分支）。spec「停止开新仓（持仓
    与平仓照常）」对加仓侧未显式归类，本锚按 design D3「触发后
    entries 屏蔽」锚定现行语义（spec 含糊处已随特征网报告上报）。"""

    def test_add_fills_after_halt(self):
        """入场 50 股（position_size=0.5）→触发→bar30 加仓照常成交：
        add_value=value_now(8000)×0.36=2880@60=48 股——总 98 股、
        halted=true、同一笔 trade（加仓并入）。

        曲线尾段 60：峰值 50×120+5000=11000 → 50×60+5000=8000
        回撤 27.3%≥10% 触发（bar21 判定——c.value_now 为决策时点前
        一 bar 期末价值口径，bar20 回调用 bar19 期末价 120 无回撤；
        组合用例同口径注记）。stop_loss=0.9 显式宽线：缺省 0.05 会
        在尾段腰斩处触发 SL 平仓，bar30 加仓时 position_now==0 加仓
        分支前置不满足（构造偏差非熔断行为）。"""
        from core.strategy.dna import RiskGenes as _RG
        dna = StrategyDNA(
            signal_genes=[_gene()], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=_RG(direction="long", position_size=0.5,
                           leverage=1, add_fraction=0.36,
                           stop_loss=0.9))
        res = _run(_prices(60.0), _sig([1], add_bars=[30]), halt=0.1,
                   dna=dna)
        assert res.halt_info is not None and res.halt_info["halted"] is True
        assert float(res.trades_df["Size"].sum()) == 98.0, \
            "入场 50＋halted 后加仓 48 照常成交（adds 不被屏蔽）"
        assert len(res.trades_df) == 1, "加仓并入同一 trade 行"


class TestComboWithTaskCap:
    """③层任务级封顶与熔断同场并存（同一 Requirement 两可选参数面的
    组合格——互不干扰：缩量照计、触发照屏）。"""

    def test_scaled_entry_and_halt_coexist(self):
        """入场 fixed 0.5（名义 5000）被③顶 10000×0.04×3=1200 缩至
        12 股；随后峰值 10240→9520（价 120→60）回撤 7.03%≥5% 触发
        （bar21 判定——前一 bar 期末价值口径）；触发后出场照常（bar25
        信号→26 成交）、空仓后 bar30 再入场被屏蔽（码 36）——③层缩
        量、熔断触发、出场存活、再入场屏蔽四证据并存。"""
        from core.strategy.dna import RiskGenes as _RG
        dna = StrategyDNA(
            signal_genes=[_gene()], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=_RG(direction="long", position_size=0.5,
                           leverage=3, max_exposure=2.0,
                           stop_loss=0.9))
        res = _run(_prices(60.0), _sig([1, 30], exit_bars=[25]), halt=0.05,
                   dna=dna, task_leverage=3, task_margin_cap=0.04)
        # ③层证据：缩量计数＋生效面
        face = res.metrics_dict["position_sizing"]
        assert face["capped_by_task_cap"] == 1
        assert face["task_cap"] == {"margin_cap": 0.04,
                                    "effective_leverage": 3.0}
        # 熔断证据：触发＋触发后出场照常＋空仓后再入场屏蔽
        assert res.halt_info is not None and res.halt_info["halted"] is True
        assert 13.0 in set(res.trace_buf[:, 0]), "触发后出场信号照常成交"
        assert 36.0 in set(res.trace_buf[:, 0]), "空仓后再入场被熔断屏蔽"
        assert float(res.trades_df["Size"].sum()) == 12.0, \
            "入场 5000 缩至顶 1200=12 股（halted 屏蔽不误伤已成交）"
        assert len(res.trades_df) == 1, "bar31 再入场被熔断屏蔽"
