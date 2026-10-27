"""永续资金费边界测试（research-execution-config-alignment 卡 C 改写版）。

历史沿革：Phase B funding 修复期的 13 条测试经 B2 永续化改造逐条
翻转/重写（映射见本 change tasks.md 1.3）；本文件保留原函数名锚：
- 翻转 6 条（:95/:130/:154/:175/:282/:347）——1x 计费/杠杆解耦/
  比例折算进 rates 装配/同名义敞口费用相等；
- 语义重写 3 条（:57/:219/:312）——仅持仓时段收费性质保留/计费
  基数=名义敞口（borrowed_ratio 退役）/合理性上限；
- 待定定案 1 条（:205）——rates 缺省=无时序供给零费（legacy 全程
  扣费形制随费率时序化退役）；
- 保留 3 条（:104/:196/:262）——:196/:262 行为不变真不动作；:104
  意图不变构造适配（裸 df 无费率列恒零费——加列后才可断言正费）。

三性质（1x 计费+时变费率+多空互付）/连续折算/NaN 披露/双消费点
同源的系统锚见 tests/backtest/test_funding_perpetual.py。
"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.backtest.engine import (
    BacktestEngine, _apply_funding_costs, _funding_rates_for)
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)

def _make_dna(leverage=1, direction="long", sl=0.05, tp=0.10, pos_size=0.5):
    return StrategyDNA(
        signal_genes=[
            SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER, 'RSI_14',
                        {'type': 'lt', 'threshold': 30}),
            SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER, 'RSI_14',
                        {'type': 'gt', 'threshold': 70}),
        ],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(
            stop_loss=sl, take_profit=tp, position_size=pos_size,
            leverage=leverage, direction=direction,
        ),
    )

def _make_ohlcv(n=200, start="2024-01-01", seed=42, funding=None):
    """Create synthetic OHLCV DataFrame.

    funding 非 None 时注入 funding_rate 列+覆盖 attrs（全段覆盖——
    覆盖边界/gaps 分列的系统性锚在 test_funding_perpetual.py）。
    """
    np.random.seed(seed)
    dates = pd.date_range(start, periods=n, freq='4h', tz='UTC')
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = np.clip(50 + np.random.randn(n) * 20, 0, 100)
    if funding is not None:
        df['funding_rate'] = float(funding)
        df.attrs["derivatives_coverage"] = {"funding": {
            "available_start": dates[0].isoformat(),
            "available_end": dates[-1].isoformat()}}
    return df

# ── A2: Funding costs only while position is open（语义重写）──

def test_funding_only_during_open_position():
    """仅持仓时段收费性质保留（重写：费率列在场，一开一平的分段
    费用显著低于全程持仓；近似人工核算带——bar 数比即费用比）。"""
    n = 100
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = 100 + np.cumsum(np.random.randn(n) * 0.3)

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df['funding_rate'] = 0.001
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}
    # Entry at bar 5, exit at bar 25
    df.loc[df.index[5], 'rsi_14'] = 20
    df.loc[df.index[25], 'rsi_14'] = 80

    dna = _make_dna(leverage=5, sl=0.0, tp=0.0)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    # 持仓约 20/99 bars → 费用应显著低于全程（名义敞口近似恒定
    # 500k×0.0005/bar：全程≈99 bar 费 vs 分段≈20 bar 费）
    assert result.funding["total_cost"] > 0.0, "费率在场持仓必计费"
    assert result.total_funding_cost < 500000 * 0.0005 * 50, \
        f"分段费用 {result.total_funding_cost} 应低于 50 bar 全程量级"


def test_no_funding_with_1x_leverage():
    """（翻转）1x 杠杆同样计费——计费基数=名义敞口（杠杆解耦，
    C1 性质一）：L=1/L=3/L=5 同名义敞口（margin 反比）费用相等。"""
    n = 80
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = np.ones(n) * 100
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df['funding_rate'] = 0.001
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}
    df.loc[df.index[5], 'rsi_14'] = 20  # entry, no exit

    engine = BacktestEngine(init_cash=100000)
    results = {}
    for lev, pos in ((1, 1.0), (3, 1 / 3), (5, 0.2)):
        r = engine.run(_make_dna(leverage=lev, sl=0.0, tp=0.0,
                                 pos_size=pos), df)
        assert r.total_trades >= 1
        results[lev] = r.total_funding_cost
    assert results[1] > 0.0, "1x 同样计费（杠杆解耦）"
    # 同名义敞口（pos_size×L 恒 1.0）→ 费用一致（价格平稳近似）
    assert abs(results[1] - results[3]) / results[1] < 0.01, \
        f"L=1 ({results[1]:.1f}) 与 L=3 ({results[3]:.1f}) 同敞口费用应相等"
    assert abs(results[1] - results[5]) / results[1] < 0.01, \
        f"L=1 ({results[1]:.1f}) 与 L=5 ({results[5]:.1f}) 同敞口费用应相等"


def test_funding_cost_positive_with_open_leveraged_position():
    """（保留意图，构造适配）杠杆持仓+费率列在场 → 费用为正。
    裸 df 无费率列恒零费的负向锚见 perpetual 文件 C0。"""
    n = 100
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = np.ones(n) * 100

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df['funding_rate'] = 0.001
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}
    df.loc[df.index[5], 'rsi_14'] = 20  # Entry at bar 5, no exit

    dna = _make_dna(leverage=3, sl=0.0, tp=0.0)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result.total_trades >= 1
    assert result.total_funding_cost > 0.0, \
        f"Expected positive funding cost, got {result.total_funding_cost}"

# ── B2: 比例折算（翻转：折算语义迁移进 rates 装配层）──

def test_funding_rate_proportional_not_ceil():
    """（翻转）4h bar 单 bar 费率=费率×4/8=0.5 期（非 ceil）——经
    _funding_rates_for 装配断言；直调事后层按 rates 精确人工核算。"""
    n = 100
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    df = pd.DataFrame({"funding_rate": 0.001}, index=dates)
    rates, meta = _funding_rates_for(df, "4h")
    assert np.allclose(rates, 0.001 * 0.5), \
        "4h bar 折算=0.001×4/8（比例非 ceil）"

    equity = pd.Series(np.full(n, 100000.0), index=dates)
    trades_df = pd.DataFrame({
        'Entry Timestamp': [dates[0]], 'Exit Timestamp': [dates[-1]]})
    _, paid, received = _apply_funding_costs(
        equity, '4h', trades_df=trades_df, rates=rates)
    # 人工核算：99 bar × 100000 × 0.0005（权益基数无复利差——逐 bar
    # 就地扣产生轻微复利，容差带内精确对照）
    assert abs(paid - 99 * 100000 * 0.0005) / paid < 0.01
    assert received == 0.0

def test_funding_rate_1h_bar():
    """（翻转）1h bar=1/8 期；与 4h/1d 跨周期单位时间费用一致（C8）。"""
    n = 100
    dates = pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC')
    df = pd.DataFrame({"funding_rate": 0.001}, index=dates)
    rates, _ = _funding_rates_for(df, "1h")
    assert np.allclose(rates, 0.001 * 0.125), \
        "1h bar 折算=0.001×1/8"

def test_funding_rate_1d_bar():
    """（翻转）1d bar=3 期/日（24/8）——rate×3.0 折算。"""
    n = 100
    dates = pd.date_range('2024-01-01', periods=n, freq='1D', tz='UTC')
    df = pd.DataFrame({"funding_rate": 0.001}, index=dates)
    rates, _ = _funding_rates_for(df, "1d")
    assert np.allclose(rates, 0.001 * 3.0), \
        "1d bar 折算=0.001×24/8"

# ── Regression ──

def test_equity_starts_at_init_cash_phaseB():
    """Equity curve should still start at init_cash after funding fix."""
    df = _make_ohlcv()
    dna = _make_dna()
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert abs(result.equity_curve.iloc[0] - 100000) < 1

def test_backward_compat_no_trades_df():
    """（待定定案）rates 缺省=无时序供给零费——legacy「全程按持仓
    扣费」形制随费率时序化退役；裸调用不传 rates 恒零费直返。"""
    equity = pd.Series(np.full(100, 100000.0))

    result_curve, paid, received = _apply_funding_costs(
        equity, '4h',
    )
    assert paid == 0.0 and received == 0.0
    assert np.array_equal(result_curve.values,
                          np.full(100, 100000.0))


# ── Position-value based funding cost（基数=名义敞口，重写）──

def test_funding_cost_based_on_position_value_not_equity():
    """（重写）计费基数=|position_value| 名义敞口（borrowed_ratio
    折减退役——L=3 名义=pos_value×leverage 全额，非 (L−1)/L 部分）：
    费用≈名义×rate×bar 数（人工核算带对照，非折减量级）。"""
    n = 50
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = np.linspace(100, 120, n)  # rising price -> profitable position

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df['funding_rate'] = 0.001
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}
    df.loc[df.index[5], 'rsi_14'] = 20  # Entry at bar 5, no exit

    dna = _make_dna(leverage=3, sl=0.0, tp=0.0, pos_size=0.5)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    if result.total_trades == 0:
        pytest.skip("No trades generated")

    assert result.total_funding_cost > 0.0
    # 名义≈0.5×equity×3（价格 100→120 渐涨，名义随权益渐增）：
    # 全程 44 bar 均价 110 → 名义≈165000×0.0005×44≈3630（1% 容差带
    # 之外的宽松上界：显著高于 borrowed 折减口径也不超全额口径 2 倍）
    full_notional = 3 * 0.5 * 100000 * 1.10 * 0.0005 * 44
    assert result.total_funding_cost > full_notional * 0.5, \
        "费用量级应为全额名义口径（非 borrowed 折减口径）"
    assert result.total_funding_cost < full_notional * 2.0


def test_funding_cost_zero_when_no_position():
    """When position_value is 0 (flat), funding cost should be 0."""
    n = 50
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = np.ones(n) * 100

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0  # No entry signal
    df['funding_rate'] = 0.001
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}

    dna = _make_dna(leverage=3, sl=0.0, tp=0.0)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    assert result.total_funding_cost == 0.0


def test_funding_cost_higher_with_higher_leverage():
    """（翻转）同 position_size 更高杠杆=更大名义敞口=更高费用——
    全额名义口径（旧 borrowed_ratio 折减差被放大）。"""
    n = 80
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = np.ones(n) * 100

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df['funding_rate'] = 0.001
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}
    df.loc[df.index[5], 'rsi_14'] = 20  # Entry, no exit

    engine = BacktestEngine(init_cash=100000)

    dna_3 = _make_dna(leverage=3, sl=0.0, tp=0.0)
    result_3 = engine.run(dna_3, df)

    dna_5 = _make_dna(leverage=5, sl=0.0, tp=0.0)
    result_5 = engine.run(dna_5, df)

    if result_3.total_trades == 0 or result_5.total_trades == 0:
        pytest.skip("No trades generated")

    # 名义敞口=pos_size×equity×L → L=5 费用≈L=3 的 5/3 倍
    ratio = result_5.total_funding_cost / result_3.total_funding_cost
    assert 5 / 3 * 0.98 < ratio < 5 / 3 * 1.02, \
        f"全额名义口径费用比应≈5/3，got {ratio:.3f}"


def test_funding_cost_less_than_borrowed_amount():
    """（重写注记）短持仓费用远小于名义敞口——永续口径合理性上限
    保留（0.1%/8h 的常态费率下界断言）。"""
    n = 40
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = np.ones(n) * 100

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df['funding_rate'] = 0.001
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}
    df.loc[df.index[5], 'rsi_14'] = 20  # Entry
    df.loc[df.index[25], 'rsi_14'] = 80  # Exit

    dna = _make_dna(leverage=3, sl=0.0, tp=0.0, pos_size=0.5)
    engine = BacktestEngine(init_cash=100000)
    result = engine.run(dna, df)

    if result.total_trades == 0:
        pytest.skip("No trades generated")

    # 名义=0.5×100000×3=150000；20 bar×0.0005≈1500——远小于名义
    notional = 0.5 * 100000 * 3
    assert result.total_funding_cost < notional * 0.1, \
        f"Funding cost {result.total_funding_cost:.0f} should be < 10% of notional {notional}"


def test_funding_cost_batch_run_different_leverages():
    """（翻转）batch_run 同 df 多 DNA：费率列同源（result.funding 两
    DNA 付出口径可对照——同名义敞口费用一致，C7 批量同源锚）。"""
    n = 100
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = np.ones(n) * 100

    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df['funding_rate'] = 0.001
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}
    df.loc[df.index[5], 'rsi_14'] = 20  # Entry, no exit

    engine = BacktestEngine(init_cash=100000)

    dna_2 = _make_dna(leverage=2, sl=0.0, tp=0.0, pos_size=0.5)
    dna_5 = _make_dna(leverage=5, sl=0.0, tp=0.0, pos_size=0.2)

    results = engine.batch_run([dna_2, dna_5], df)
    assert len(results) == 2

    if results[0].total_trades > 0 and results[1].total_trades > 0:
        # 同名义敞口（0.5×2 == 0.2×5）→ 付出一致；coverage 元数据
        # 同源（status/covered/gaps 逐键相等——同 df 单点装配）
        assert abs(results[0].funding["total_cost"]
                   - results[1].funding["total_cost"]) \
            / results[0].funding["total_cost"] < 0.01, \
            "同名义敞口批量费用应一致（费率列同源复制）"
        assert results[0].funding["coverage"] == \
            results[1].funding["coverage"]
