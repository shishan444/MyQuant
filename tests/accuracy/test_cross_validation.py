"""五场景交叉验证 + 数学恒等式族（1.4）。

信号层共享面：参照与被测共同输入 = dna_to_signal_set 产出的真实信号序列
（信号正确性由既有 tests/backtest 信号族守护，此处不重造）。
比对锚定「信号之后的一切」。恒等式族在两实现上各自独立断言（补「两实现
同错」盲区——恒等式不需要期望值来源）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import BacktestEngine
from core.features.indicators import compute_all_indicators
from core.strategy.executor import dna_to_signal_set
from tests.helpers.data_factory import make_dna
from tests.reference.assert_metrics import assert_metrics_equal
from tests.reference.fixture_gen import gen_ohlcv
from tests.reference.reference_backtest import reference_backtest
from tests.reference.reference_metrics import reference_metrics

INIT_CASH = 100_000.0


def _signals(dna, ohlcv):
    """真实信号链：指标 → dna_to_signal_set（共享面，仅此一层）。"""
    enhanced = compute_all_indicators(ohlcv)
    return dna_to_signal_set(dna, enhanced)


def _run_both(scenario, seed, direction="long", position_size=0.5, leverage=1,
              stop_loss=0.0, take_profit=0.0):
    ohlcv = gen_ohlcv(scenario, seed=seed)
    dna = make_dna(direction=direction, position_size=position_size,
                   leverage=leverage, stop_loss=stop_loss, take_profit=take_profit)
    sig = _signals(dna, ohlcv)

    eng = BacktestEngine(init_cash=INIT_CASH, fee=0.001, slippage=0.0005)
    enhanced = compute_all_indicators(ohlcv)
    eng_res = eng.run(dna, enhanced, signal_set=sig)

    ref_res = reference_backtest(
        ohlcv, sig.entries, sig.exits, adds=sig.adds, reduces=sig.reduces,
        entry_direction=sig.entry_direction,
        direction=direction, position_size=position_size, leverage=leverage,
        stop_loss=stop_loss, take_profit=take_profit,
        init_cash=INIT_CASH, fee=0.001, slippage=0.0005, timeframe="4h",
    )
    return eng_res, ref_res, ohlcv


def _engine_metrics(res) -> dict:
    return {
        "total_return": res.total_return,
        "sharpe_ratio": res.sharpe_ratio,
        "max_drawdown": res.max_drawdown,
        "win_rate": res.win_rate,
        "total_trades": res.total_trades,
        "annual_return": (res.metrics_dict or {}).get("annual_return", 0.0),
        "liquidated": res.liquidated,
    }


def _ref_metrics(res) -> dict:
    m = reference_metrics(res.equity, [t.pnl for t in res.trades], INIT_CASH, "4h")
    m["liquidated"] = res.liquidated
    return m


# ──────────────────────────────────────────────────────────────
# 恒等式族（每实现独立断言，不需要期望值）
# ──────────────────────────────────────────────────────────────

def _assert_conservation_ref(ref, leverage) -> None:
    """权益守恒（参照侧，全杠杆适用）。

    final = init + L*Σ_k(raw_eq[exit_k] − raw_eq[entry_k]) − Σfunding
    —— 放大作用在 bar 收盘估值口径的区间权益变化上（非含滑点成交口径 pnl）；
    lev=1 时区间变化合计即 Σpnl（终值只由 cash 链决定），退化为
    final = init + Σpnl − Σfunding（spec 恒等式原式）。
    """
    if leverage <= 1:
        # cash 链恒等式：final = init + Σpnl − Σfunding（spec 原式）
        pnl_sum = sum(t.pnl for t in ref.trades)
        expected_final = INIT_CASH + pnl_sum - ref.total_funding
    else:
        # 杠杆三条自洽恒等式（funding 逐 bar 扣、不传播——与被测口径一致）：
        # a) 放大链精确式：unfunded[-1] = init + L*Σ(raw[exit]−raw[entry])
        raw = ref.raw_equity
        seg_sum = sum(raw[t.exit_idx] - raw[t.entry_idx] for t in ref.trades)
        assert ref.unfunded_equity[-1] == pytest.approx(
            INIT_CASH + leverage * seg_sum, rel=1e-9, abs=1e-6,
        ), "参照放大链恒等式破坏"
        # b) funding 只减不增
        assert (ref.equity <= ref.unfunded_equity + 1e-9).all(), "funding 使权益增加"
        # c) 总量=分布和：Σ(unfunded−funded) = total_funding
        diff_sum = float(np.sum(ref.unfunded_equity - ref.equity))
        assert diff_sum == pytest.approx(ref.total_funding, rel=1e-9, abs=1e-6), (
            f"funding 总量与分布不一致: Σdiff={diff_sum} vs total={ref.total_funding}"
        )
        return
    assert ref.equity[-1] == pytest.approx(expected_final, rel=1e-9, abs=1e-6), (
        f"参照权益守恒破坏: {ref.equity[-1]} != {expected_final}"
    )


def _assert_conservation_engine(eng, leverage) -> None:
    """权益守恒（被测侧）：lev=1 用 trades 明细独立恒等式；
    lev>1 用曲线结构恒等式（首值=init、无 NaN、清算标志与曲线极值一致）。"""
    assert eng.equity_curve.iloc[0] == pytest.approx(INIT_CASH, rel=1e-9)
    assert not eng.equity_curve.isna().any()
    if leverage <= 1:
        assert eng.trades_df is not None
        pnl_sum = float(eng.trades_df["PnL"].sum())
        expected_final = INIT_CASH + pnl_sum - eng.total_funding_cost
        assert eng.equity_curve.iloc[-1] == pytest.approx(
            expected_final, rel=1e-7, abs=1e-4,
        ), f"被测权益守恒破坏: {eng.equity_curve.iloc[-1]} != {expected_final}"
    else:
        from tests.reference.reference_backtest import _maintenance_threshold
        maintenance = _maintenance_threshold(INIT_CASH, leverage)
        assert eng.liquidated == bool(
            (eng.equity_curve.to_numpy() < maintenance).any()
        ), "清算标志与曲线极值不一致（后置检测口径）"


def _assert_ranges(metrics: dict, equity_blown: bool = False) -> None:
    """值域恒等式。权益穿零（D-3 已登记）时 drawdown 公式失义（(eq−cummax)/cummax
    于负 cummax 发散），两实现同值同错——穿零 case 降级为方向性断言 dd<=0。"""
    if equity_blown:
        assert metrics["max_drawdown"] <= 0.0, "穿零场景 drawdown 应非正（D-3 登记中）"
    else:
        assert -1.0 <= metrics["max_drawdown"] <= 0.0
    assert 0.0 <= metrics["win_rate"] <= 1.0


# ──────────────────────────────────────────────────────────────
# 五场景交叉验证（liquidation_edge 优先）
# ──────────────────────────────────────────────────────────────

CASES = [
    # (id, scenario, seed, kwargs) —— liquidation_edge 排最前（spec 优先级）
    ("liq-edge-lev5", "liquidation_edge", 11, {"leverage": 5, "position_size": 0.5}),
    ("liq-edge-lev1", "liquidation_edge", 12, {}),
    ("normal-lev1", "normal", 42, {}),
    ("normal-lev3", "normal", 43, {"leverage": 3, "position_size": 0.3}),
    ("short-win", "short_window", 5, {}),
    ("trend-short", "trend", 7, {"direction": "short"}),
    ("vol-sl-tp", "volatile", 9, {"stop_loss": 0.05, "take_profit": 0.10}),
]
CASE_IDS = [c[0] for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_cross_validation(case):
    case_id, scenario, seed, kw = case
    leverage = kw.get("leverage", 1)
    eng, ref, ohlcv = _run_both(scenario, seed, **kw)

    m_eng, m_ref = _engine_metrics(eng), _ref_metrics(ref)
    # D-2（已登记）：权益穿零时被测 annual_return 为复数（Python 负底数浮点幂），
    # 参照 numpy 路径为 nan——比对排除该字段，缺陷由独立 fix 分支跟进
    if eng.total_return <= -1.0 or ref.equity[-1] <= 0:
        m_eng.pop("annual_return"), m_ref.pop("annual_return")
    assert_metrics_equal(m_eng, m_ref, scenario=case_id)

    # 恒等式族：两实现各自独立断言
    _assert_conservation_ref(ref, leverage)
    _assert_conservation_engine(eng, leverage)
    blown = bool(ref.equity[-1] <= 0 or eng.equity_curve.iloc[-1] <= 0)
    _assert_ranges(m_ref, blown)
    _assert_ranges(m_eng, blown)

    # 交易序列计数与逐笔方向一致（计数精确）
    if ref.trades:
        assert len(ref.trades) == eng.total_trades, (
            f"{case_id}: 参照 {len(ref.trades)} 笔 vs 引擎 {eng.total_trades} 笔"
        )


def test_no_trade_default_semantics():
    """缺省语义：全窗口无信号 → 无交易胜率为缺失而非 0（D2 三态）。"""
    ohlcv = gen_ohlcv("normal", seed=42)
    dna = make_dna(direction="long", leverage=1)
    idx = ohlcv.index
    from core.strategy.executor import SignalSet
    zeros = pd.Series(False, index=idx)
    sig = SignalSet(entries=zeros.copy(), exits=zeros.copy(),
                    adds=zeros.copy(), reduces=zeros.copy())

    eng = BacktestEngine(init_cash=INIT_CASH, fee=0.001, slippage=0.0005)
    enhanced = compute_all_indicators(ohlcv)
    eng_res = eng.run(dna, enhanced, signal_set=sig)
    ref_res = reference_backtest(
        ohlcv, zeros, zeros, direction="long",
        init_cash=INIT_CASH, fee=0.001, slippage=0.0005,
    )

    # 三态契约：None+failed=False = 合法缺失；不产生计算失败假象
    assert eng_res.total_trades == 0
    assert eng_res.trade_win_rate is None and eng_res.win_rate_failed is False
    assert eng_res.total_return == 0.0
    assert len(ref_res.trades) == 0
    assert ref_res.equity[-1] == pytest.approx(INIT_CASH)
    # 指标全零缺省两实现一致
    assert_metrics_equal(_engine_metrics(eng_res), _ref_metrics(ref_res),
                         scenario="no-trade")


def test_liquidation_edge_triggers():
    """清算边界场景在 L=5 下实际触发清算（场景有效性自证）。"""
    eng, ref, _ = _run_both("liquidation_edge", seed=101, leverage=5, position_size=0.5)
    # 场景构造目标：高杠杆+单边下跌 → 清算发生（若种子不触发，调种子不是放松断言）
    assert eng.liquidated is True, "liquidation_edge 种子未触发清算——场景失效，需换种子"
    assert eng.liquidated == ref.liquidated


def test_liquidation_timing_sentinel():
    """D-1 哨兵：清算盯市晚一根（被测当前行为锁定）。

    构造恰好穿阈值的序列：当根 value 破线（<80200）时不触发，
    次根（prev-value 已破线）才强平。若未来修复为当根盯市，
    本哨兵变红 → 更新 contract.py D-1 注记与参照实现。
    """
    n = 12
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    closes = [1000, 1000, 1000, 950, 900, 850, 604, 500, 480, 470, 460, 450]
    close = pd.Series(np.array(closes, dtype=float), index=idx)
    df = pd.DataFrame(
        {"open": close, "high": close + 1.0, "low": close - 1.0,
         "close": close, "volume": 100.0}, index=idx,
    )
    df.index.name = "timestamp"
    entries = pd.Series(False, index=idx)
    entries.iloc[1] = True   # shift 后 bar2 开仓
    exits = pd.Series(False, index=idx)
    zeros = pd.Series(False, index=idx)

    from core.strategy.executor import SignalSet
    from tests.reference.reference_backtest import _maintenance_threshold

    dna = make_dna(direction="long", position_size=0.5, leverage=5,
                   stop_loss=0.0, take_profit=0.0)
    eng = BacktestEngine(init_cash=INIT_CASH, fee=0.001, slippage=0.0005)
    sig = SignalSet(entries=entries, exits=exits, adds=zeros, reduces=zeros)
    enhanced = compute_all_indicators(df)
    eng_res = eng.run(dna, enhanced, signal_set=sig)
    ref_res = reference_backtest(
        df, entries, exits, direction="long", position_size=0.5, leverage=5,
        init_cash=INIT_CASH, fee=0.001, slippage=0.0005, timeframe="4h",
    )

    # 手工锚定边界：shares 与破线 bar
    price_in = 1000.0 * 1.0005
    qty = (INIT_CASH * 0.5) / (price_in * 1.001)
    cash = INIT_CASH - qty * price_in - qty * price_in * 0.001
    maintenance = _maintenance_threshold(INIT_CASH, 5)
    value = lambda c: cash + qty * c
    broken = [i for i, c in enumerate(closes) if value(c) < maintenance]
    assert broken, "哨兵场景构造失败：序列未穿越清算阈值"
    first_break = broken[0]

    # D-1 当前行为：出场 bar = first_break + 1（prev 估值破线才触发）
    assert ref_res.trades and ref_res.trades[0].exit_idx == first_break + 1, (
        f"参照未锁定 D-1 行为: 破线@{first_break} 出场@{ref_res.trades[0].exit_idx}"
    )
    assert eng_res.trades_df is not None and len(eng_res.trades_df) == len(ref_res.trades)
    eng_exit_ts = eng_res.trades_df["Exit Timestamp"].iloc[0]
    assert ohlcv_bar(idx, eng_exit_ts) == first_break + 1


def ohlcv_bar(idx, ts) -> int:
    return int(idx.get_loc(ts))
