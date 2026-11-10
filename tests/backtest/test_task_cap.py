"""任务级封顶（合成序③层）引擎行为（sandbox-and-report-supplement A2）。

三机制分界（D1）：①risk_budget/fixed 定仓 → ②策略层截断（加仓通道
超限跳过〔码 30〕／risk_budget_qty 入场截断）→ ③任务级封顶（本层：
名义敞口 ≤ 权益×margin_cap×有效杠杆，有效杠杆=min(DNA 声明, 任务级)；
缩量至顶非跳过〔码 35〕＋capped_by_task_cap 计数）。

手算算例锚（tasks A2）：名义顶 18000；敞口 15000＋新单 5000→缩至
3000。构造基准 init_cash=20000（vbt 现金账户约束：名义敞口≤现金，
权益 10000 时 15000 敞口物理不可开——顶与缩量结果两数字与手算
逐一对上，权益基准差异见各用例注记）。

价格恒定 100、fee=slippage=0——权益曲线无费滑噪声，缩量断言可
精确到股。
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
PRICE = 100.0


def _df() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=N, freq="4h", tz="UTC",
                        name="timestamp")
    p = np.full(N, PRICE)
    return pd.DataFrame({
        "open": p, "high": p * 1.001, "low": p * 0.999, "close": p,
        "volume": np.full(N, 100.0), "trades": 10,
    }, index=idx)


def _sig(entry_bars=(), add_bars=(), exit_bars=()):
    z = pd.Series(0.0, index=_df().index)
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


def _dna(**risk_kw):
    return StrategyDNA(
        signal_genes=[_gene()], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(direction="long", **risk_kw))


def _run(dna, sig, *, init_cash, task_leverage=None, task_margin_cap=None):
    return BacktestEngine(
        init_cash=init_cash, fee=0.0, slippage=0.0,
        task_leverage=task_leverage,
        task_margin_cap=task_margin_cap).run(
        dna, _df(), dfs_by_timeframe={"4h": _df()}, signal_set=sig)


def _total_size(result) -> float:
    """同方向加仓在 vbt records_readable 并入同一 trade 行——Size 为
    累计量（入场+加仓），断言用总量形制。"""
    return float(result.trades_df["Size"].sum())


class TestMinChain:
    """有效杠杆=min(DNA 声明, 任务级)——任务级只收严不放宽。"""

    def test_dna2_task3_effective2_scales_entry(self):
        """DNA=2＋任务级=3→有效=2：顶=0.45×2×10000=9000<现金——
        fixed 1.0 请求名义 10000 缩至 9000（90 股）。min 链若误用
        任务级 3：顶 13500>现金→现金钳至 10000（100 股）——Size
        断言区分两链。"""
        sig = _sig(entry_bars=[1])
        res = _run(
            _dna(position_size=1.0, leverage=2, max_exposure=1.0), sig,
            init_cash=10000, task_leverage=3, task_margin_cap=0.45)
        assert _total_size(res) == 90.0, "名义 10000 缩至顶 9000=90 股"
        face = res.metrics_dict["position_sizing"]
        assert face["task_cap"]["effective_leverage"] == 2.0
        assert face["capped_by_task_cap"] == 1


class TestScaleAddHandCalc:
    """手算算例（A2 完成标准）：名义顶 18000；敞口 15000＋新单
    5000→缩至 3000。

    构造：init=20000、入场 fixed 0.75=15000 名义（150 股）；③顶=
    20000×0.3×3=18000（margin_cap=0.3×任务级 3——手算的顶数字）；
    加仓 add_fraction=0.25→5000：15000+5000=20000>18000→缩至 3000
    （30 股，剩余现金 5000 足付）。
    """

    DNA_KW = dict(position_size=0.75, leverage=3, max_exposure=2.0,
                  add_fraction=0.25)

    def test_add_order_scaled_to_cap_hand_calc(self):
        res = _run(_dna(**self.DNA_KW), _sig(entry_bars=[1], add_bars=[5]),
                   init_cash=20000, task_leverage=3, task_margin_cap=0.3)
        # 入场 150 股（15000<18000 不缩）＋加仓 30 股（5000 缩至 3000）
        assert _total_size(res) == 180.0, "加仓请求 5000 缩至顶余量 3000=30 股（手算）"
        face = res.metrics_dict["position_sizing"]
        assert face["capped_by_task_cap"] == 1
        assert face["task_cap"] == {
            "margin_cap": 0.3, "effective_leverage": 3.0}


class TestThreeMechanismBoundary:
    """三机制跨层分界：①定仓超②声明→跳过；②放行超③顶→缩量。"""

    def test_layer2_skip_when_positioning_exceeds_declaration(self):
        """①add_fraction 定仓 6000 使总敞口 21000 超②声明
        （max_exposure=1.0×权益 20000）→跳过不发单（码 30）——③未
        启用（无任务参），既有行为。"""
        res = _run(
            _dna(position_size=0.75, leverage=3, max_exposure=1.0,
                 add_fraction=0.3),
            _sig(entry_bars=[1], add_bars=[5]), init_cash=20000)
        assert _total_size(res) == 150.0, "加仓被②跳过：总量=入场 150 股"
        trace = res.trace_buf if res.trace_buf is not None else None
        # trace 经 result 透出（30=敞口上限拒绝）
        assert trace is not None and 30.0 in set(trace[:, 0])

    def test_layer2_pass_layer3_scale(self):
        """②界宽松（max_exposure=2.0→40000）放行 21000，但超③顶
        18000→缩量至 3000（非跳过——同 bar 两层语义分界锚）。"""
        res = _run(
            _dna(position_size=0.75, leverage=3, max_exposure=2.0,
                 add_fraction=0.3),
            _sig(entry_bars=[1], add_bars=[5]), init_cash=20000,
            task_leverage=3, task_margin_cap=0.3)
        # 入场 150 股＋加仓 30 股（6000 缩至 3000——缩量非跳过）
        assert _total_size(res) == 180.0
        assert res.metrics_dict["position_sizing"][
            "capped_by_task_cap"] == 1

    def test_layer3_zero_residual_no_order(self):
        """③顶 3000（margin 0.05×3×20000）先作用于入场：fixed 0.75
        请求名义 15000 缩至顶 3000（30 股）；随后加仓 bar 敞口已=顶、
        余量≤0 无缩量空间=不发单（缩至零退化形）。两动作各留痕码
        35、计数=2。"""
        res = _run(
            _dna(position_size=0.75, leverage=3, max_exposure=2.0,
                 add_fraction=0.25),
            _sig(entry_bars=[1], add_bars=[5]), init_cash=20000,
            task_leverage=3, task_margin_cap=0.05)
        assert _total_size(res) == pytest.approx(30.0), "入场缩至顶 3000；加仓余量 0 不发单"
        trace = res.trace_buf
        assert trace is not None and 35.0 in set(trace[:, 0]), \
            "缩至零也写留痕码 35（rejected 形态）"
        assert res.metrics_dict["position_sizing"][
            "capped_by_task_cap"] == 2


class TestUnsetPathUnchanged:
    """未传两参=③层不生效：无新键、无码 35、加仓足额——存量行为
    逐位不变（结构性等价锚：加仓按①定仓 6000 足额成交=既有②放行
    行为；1015 项既有引擎测试全绿为主证据）。"""

    def test_no_keys_no_code35_full_add(self):
        """加仓 6000 请求被 vbt 现金钳至剩余 5000（50 股——既有现金
        账户语义，非③层行为）：③未启用时无新键、无码 35。"""
        res = _run(
            _dna(position_size=0.75, leverage=3, max_exposure=2.0,
                 add_fraction=0.3),
            _sig(entry_bars=[1], add_bars=[5]), init_cash=20000)
        assert _total_size(res) == 200.0, "入场 150＋加仓现金钳 50（既有）"
        face = res.metrics_dict["position_sizing"]
        assert "capped_by_task_cap" not in face, "未传两参无第八键"
        assert "task_cap" not in face
        trace = res.trace_buf
        assert trace is not None and 35.0 not in set(trace[:, 0])
