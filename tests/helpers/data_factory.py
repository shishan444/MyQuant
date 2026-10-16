"""Unified test data factory for MyQuant test suite.

Replaces duplicated fixture setup across 40+ test files with
a single source of truth for test data generation.

Usage:
    from tests.helpers.data_factory import (
        make_ohlcv, make_dna, make_mtf_dna, make_enhanced_df,
        make_signal_gene, make_signal_set, make_ema_dna,
    )
"""

import numpy as np
import pandas as pd

from core.strategy.dna import (
    ConditionType,
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
    TimeframeLayer,
)


# ---------------------------------------------------------------------------
# OHLCV data
# ---------------------------------------------------------------------------

def make_ohlcv(
    n: int = 500,
    freq: str = "4h",
    base_price: float = 40000.0,
    seed: int = 42,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame.

    Args:
        n: Number of bars.
        freq: Pandas frequency string (e.g. "4h", "1h", "1d", "15m").
        base_price: Starting price.
        seed: Random seed for reproducibility.
        start: Start date string.

    Returns:
        DataFrame with columns: open, high, low, close, volume.
        Index: DatetimeIndex named "timestamp" with UTC timezone.
    """
    rng = np.random.default_rng(seed)
    returns = rng.standard_normal(n) * 0.01 + 0.0001
    close = base_price * np.cumprod(1 + returns)

    high = close * (1 + np.abs(rng.standard_normal(n)) * 0.005)
    low = close * (1 - np.abs(rng.standard_normal(n)) * 0.005)
    opn = close * (1 + rng.standard_normal(n) * 0.002)
    volume = rng.integers(100, 10000, size=n).astype(float)

    dates = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    df = pd.DataFrame(
        {"open": opn, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )
    df.index.name = "timestamp"
    return df


def make_flat_ohlcv(
    n: int = 140,
    freq: str = "4h",
    base_price: float = 100.0,
    step: float = 0.003,
    seed: int = 11,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """Generate flat-bar OHLCV (open≡high≡low≡close) for controlled equivalence.

    受控等价专用平价 bar：四价合一（每根 K 线开盘=最高=最低=收盘），
    行情由随机游走 close 驱动。用途（rebuild-paper-trading 8.1 受控
    等价层的受控手段）：纸盘引擎信号类意图按执行 bar 开盘价成交、回测
    引擎 vbt 按当根 close 成交——平价使两引擎的成交基准价重合，逐笔
    成交与逐 bar 权益的数值对照成为可能。

    Args:
        n: Number of bars.
        freq: Pandas frequency string (e.g. "4h", "1h").
        base_price: Starting price.
        step: Per-bar return stddev (keep well below stop distance to
            avoid triggering immediate exits in equivalence scenarios).
        seed: Random seed for reproducibility.
        start: Start date string.
    """
    rng = np.random.default_rng(seed)
    returns = rng.standard_normal(n) * step
    close = base_price * np.cumprod(1 + returns)
    volume = rng.integers(100, 10000, size=n).astype(float)

    dates = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close,
         "volume": volume},
        index=dates,
    )
    df.index.name = "timestamp"
    return df


# ---------------------------------------------------------------------------
# Signal Gene helpers
# ---------------------------------------------------------------------------

def make_signal_gene(
    indicator: str = "RSI",
    params: dict | None = None,
    role: SignalRole = SignalRole.ENTRY_TRIGGER,
    condition_type: str = "lt",
    condition_value: float = 30,
    field_name: str | None = None,
) -> SignalGene:
    """Create a SignalGene with sensible defaults.

    Args:
        indicator: Indicator name (e.g. "RSI", "EMA", "MACD").
        params: Indicator parameters dict.
        role: SignalRole enum value.
        condition_type: Condition type string (e.g. "lt", "gt", "price_above").
        condition_value: Threshold value for comparison conditions.
        field_name: Optional field name for multi-output indicators.

    Returns:
        SignalGene instance.
    """
    if params is None:
        params = {"period": 14} if indicator in ("RSI", "EMA") else {}

    condition = {"type": condition_type, "threshold": condition_value}
    if condition_type in ("price_above", "price_below"):
        condition = {"type": condition_type}

    return SignalGene(
        indicator=indicator,
        params=params,
        role=role,
        field_name=field_name,
        condition=condition,
    )


# ---------------------------------------------------------------------------
# Strategy DNA
# ---------------------------------------------------------------------------

def make_dna(
    indicator: str = "RSI",
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    direction: str = "long",
    stop_loss: float = 0.05,
    take_profit: float = 0.10,
    position_size: float = 0.5,
    leverage: int = 1,
    entry_condition: str = "lt",
    entry_value: float = 30,
    exit_condition: str = "gt",
    exit_value: float = 70,
) -> StrategyDNA:
    """Create a simple single-timeframe StrategyDNA for testing.

    Args:
        indicator: Indicator name for both entry and exit.
        timeframe: Execution timeframe.
        symbol: Trading pair.
        direction: "long" or "short".
        stop_loss: Stop loss fraction.
        take_profit: Take profit fraction.
        position_size: Position size fraction.
        leverage: Leverage multiplier.
        entry_condition: Entry condition type.
        entry_value: Entry threshold.
        exit_condition: Exit condition type.
        exit_value: Exit threshold.

    Returns:
        StrategyDNA with entry + exit signal genes.
    """
    gene_entry = make_signal_gene(
        indicator=indicator,
        role=SignalRole.ENTRY_TRIGGER,
        condition_type=entry_condition,
        condition_value=entry_value,
    )
    gene_exit = make_signal_gene(
        indicator=indicator,
        role=SignalRole.EXIT_TRIGGER,
        condition_type=exit_condition,
        condition_value=exit_value,
    )
    return StrategyDNA(
        signal_genes=[gene_entry, gene_exit],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        risk_genes=RiskGenes(
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=position_size,
            leverage=leverage,
            direction=direction,
        ),
    )


def make_ema_dna(
    timeframe: str = "4h",
    direction: str = "long",
) -> StrategyDNA:
    """Create an EMA crossover strategy DNA."""
    gene_entry = make_signal_gene(
        indicator="EMA",
        params={"period": 20},
        role=SignalRole.ENTRY_TRIGGER,
        condition_type="price_above",
    )
    gene_exit = make_signal_gene(
        indicator="EMA",
        params={"period": 20},
        role=SignalRole.EXIT_TRIGGER,
        condition_type="price_below",
    )
    return StrategyDNA(
        signal_genes=[gene_entry, gene_exit],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=timeframe),
        risk_genes=RiskGenes(direction=direction),
    )


def make_mtf_dna(
    timeframes: tuple[str, ...] = ("1d", "4h", "15m"),
    cross_layer_logic: str = "AND",
    mtf_mode: str | None = None,
    confluence_threshold: float = 0.3,
    proximity_mult: float = 1.5,
) -> StrategyDNA:
    """Create a multi-timeframe StrategyDNA with RSI on each layer.

    Args:
        timeframes: Tuple of timeframe strings. First is the execution layer.
        cross_layer_logic: "AND" or "OR".
        mtf_mode: MTF engine mode (None for legacy AND/OR).
        confluence_threshold: Confluence score threshold.
        proximity_mult: Proximity multiplier.

    Returns:
        StrategyDNA with MTF layers. Execution timeframe is timeframes[-1].
    """
    layers = []
    for tf in timeframes:
        entry = make_signal_gene(
            indicator="RSI",
            params={"period": 14},
            role=SignalRole.ENTRY_TRIGGER,
            condition_type="lt",
            condition_value=30,
        )
        exit_gene = make_signal_gene(
            indicator="RSI",
            params={"period": 14},
            role=SignalRole.EXIT_TRIGGER,
            condition_type="gt",
            condition_value=70,
        )
        layers.append(TimeframeLayer(
            timeframe=tf,
            signal_genes=[entry, exit_gene],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        ))

    return StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=timeframes[-1]),
        risk_genes=RiskGenes(direction="long"),
        layers=layers,
        cross_layer_logic=cross_layer_logic,
        mtf_mode=mtf_mode,
        confluence_threshold=confluence_threshold,
        proximity_mult=proximity_mult,
    )


# ---------------------------------------------------------------------------
# Form DNA helpers (rebuild-paper-trading 8.2 形态矩阵)
# ---------------------------------------------------------------------------

def make_scaled_dna(
    direction: str = "long",
    exit_fraction: float | None = 0.3,
    add_fraction: float | None = 0.2,
    max_exposure: float | None = 1.0,
    add_inhibit: bool = False,
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    **risk_kwargs,
) -> StrategyDNA:
    """Create a scaling (REDUCE+ADD roles) StrategyDNA for form matrix.

    可控触发形态段（design 测试基建②）：分批角色基因 DNA——REDUCE
    触发 RSI>55（与 EXIT 的 RSI>70 错开，持仓中先减后平可分辨）、
    ADD 触发 RSI<35（与 ENTRY 的 RSI<30 部分重叠，开仓后邻段加仓）；
    add_inhibit=True 时附 ADD_INHIBIT RSI<32（RSI<32 的 bar 抑制加仓——
    三参数显式/缺省由三参数传 None 切换（缺省物化形态）。

    Args:
        direction: "long" | "short" | "mixed".
        exit_fraction: 部分离场比例；None=缺省物化（0.5）。
        add_fraction: 加仓比例；None=缺省物化（0.1）。
        max_exposure: 总敞口上限；None=缺省物化（1.0）。
        add_inhibit: 是否附 ADD_INHIBIT 抑制基因。
        timeframe: Execution timeframe.
        symbol: Trading pair.
        **risk_kwargs: RiskGenes 其余字段覆写（sl_mode/max_hold 等，
            组合锚用）。

    Returns:
        StrategyDNA with entry + exit + REDUCE/ADD(/INHIBIT) genes.
    """
    genes = [
        make_signal_gene(role=SignalRole.ENTRY_TRIGGER,
                         condition_type="lt", condition_value=30),
        make_signal_gene(role=SignalRole.EXIT_TRIGGER,
                         condition_type="gt", condition_value=70),
        make_signal_gene(role=SignalRole.REDUCE_TRIGGER,
                         condition_type="gt", condition_value=55),
        make_signal_gene(role=SignalRole.ADD_TRIGGER,
                         condition_type="lt", condition_value=35),
    ]
    if add_inhibit:
        genes.append(make_signal_gene(
            role=SignalRole.ADD_INHIBIT,
            condition_type="lt", condition_value=32))
    risk = dict(
        direction=direction,
        exit_fraction=exit_fraction,
        add_fraction=add_fraction,
        max_exposure=max_exposure,
    )
    risk.update(risk_kwargs)
    return StrategyDNA(
        signal_genes=genes,
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        risk_genes=RiskGenes(**risk),
    )


def make_mixed_dna(
    direction_threshold: float = 25,
    timeframe: str = "4h",
    symbol: str = "BTCUSDT",
    **risk_kwargs,
) -> StrategyDNA:
    """Create a mixed-direction StrategyDNA with a DIRECTION gene.

    可控触发形态段（design 测试基建②）：mixed 方向须带 DIRECTION 基因
    （validate_dna 拒绝无基因形态）——DIRECTION 触发 RSI>threshold
    （True→+1 多 / False→−1 空）。threshold 缺省 25 与 ENTRY 的 RSI<30
    交叠出双向开仓段：RSI∈(25,30)→多、RSI≤25→空，且方向翻转 bar 上
    出现两步反手（close reason=reverse）。

    Args:
        direction_threshold: DIRECTION 基因的 RSI 阈值。
        timeframe: Execution timeframe.
        symbol: Trading pair.
        **risk_kwargs: RiskGenes 其余字段覆写。

    Returns:
        StrategyDNA with entry + exit + DIRECTION genes.
    """
    risk = dict(direction="mixed")
    risk.update(risk_kwargs)
    return StrategyDNA(
        signal_genes=[
            make_signal_gene(role=SignalRole.ENTRY_TRIGGER,
                             condition_type="lt", condition_value=30),
            make_signal_gene(role=SignalRole.EXIT_TRIGGER,
                             condition_type="gt", condition_value=70),
            make_signal_gene(role=SignalRole.DIRECTION,
                             condition_type="gt",
                             condition_value=direction_threshold),
        ],
        logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
        execution_genes=ExecutionGenes(timeframe=timeframe, symbol=symbol),
        risk_genes=RiskGenes(**risk),
    )


# ---------------------------------------------------------------------------
# SignalSet helpers
# ---------------------------------------------------------------------------

def make_signal_set(
    n: int = 100,
    entries: int = 5,
    exits: int = 5,
    adds: int = 0,
    reduces: int = 0,
    freq: str = "4h",
    seed: int = 42,
) -> dict:
    """Create a signal set dict suitable for backtest engine input.

    Args:
        n: Number of bars.
        entries: Number of True entry signals.
        exits: Number of True exit signals.
        adds: Number of True add signals.
        reduces: Number of True reduce signals.
        freq: Bar frequency.
        seed: Random seed.

    Returns:
        Dict with keys: entries, exits, adds, reduces (all pd.Series of bool).
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")

    def _sparse_bool(count: int) -> pd.Series:
        arr = np.zeros(n, dtype=bool)
        if count > 0 and count < n:
            indices = rng.choice(n, size=count, replace=False)
            arr[indices] = True
        return pd.Series(arr, index=dates)

    return {
        "entries": _sparse_bool(entries),
        "exits": _sparse_bool(exits),
        "adds": _sparse_bool(adds),
        "reduces": _sparse_bool(reduces),
    }


# ---------------------------------------------------------------------------
# Enhanced DataFrame (with indicators)
# ---------------------------------------------------------------------------

def make_enhanced_df(
    n: int = 500,
    freq: str = "4h",
    base_price: float = 40000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate OHLCV DataFrame with indicators computed.

    Convenience wrapper combining make_ohlcv + compute_all_indicators.

    Args:
        n: Number of bars.
        freq: Pandas frequency string.
        base_price: Starting price.
        seed: Random seed.

    Returns:
        DataFrame with OHLCV + indicator columns.
    """
    from core.features.indicators import compute_all_indicators
    df = make_ohlcv(n=n, freq=freq, base_price=base_price, seed=seed)
    return compute_all_indicators(df)


# ---------------------------------------------------------------------------
# Backtest engine (pre-configured)
# ---------------------------------------------------------------------------

def make_engine(init_cash: float = 100000) -> "BacktestEngine":
    """Create a pre-configured BacktestEngine.

    Args:
        init_cash: Initial cash amount.

    Returns:
        BacktestEngine instance.
    """
    from core.backtest.engine import BacktestEngine
    return BacktestEngine(init_cash=init_cash)


# ---------------------------------------------------------------------------
# Paper Trading helpers
# ---------------------------------------------------------------------------

def make_pm(
    direction: str = "long",
    leverage: int = 1,
    init_cash: float = 100_000.0,
    fee: float = 0.001,
    stop_loss: float = 0.05,
    take_profit: float = 0.10,
    position_size: float = 0.5,
    timeframe: str = "4h",
) -> "PositionManager":
    """Create a PositionManager with sensible defaults.

    Args:
        direction: "long" or "short".
        leverage: Leverage multiplier.
        init_cash: Initial cash balance.
        fee: Trading fee rate.
        stop_loss: Stop loss fraction.
        take_profit: Take profit fraction.
        position_size: Position size fraction.
        timeframe: Bar timeframe.

    Returns:
        PositionManager instance.
    """
    from core.trading.position import PositionManager
    dna = make_dna(
        direction=direction,
        leverage=leverage,
        stop_loss=stop_loss,
        take_profit=take_profit,
        position_size=position_size,
    )
    return PositionManager(dna, init_cash=init_cash, fee=fee)


def make_position(
    side: str = "long",
    entry_price: float = 100.0,
    quantity: float = 10.0,
    margin: float = 1000.0,
) -> "Position":
    """Create a Position dataclass instance.

    Args:
        side: "long" or "short".
        entry_price: Entry price.
        quantity: Position quantity.
        margin: Margin amount.

    Returns:
        Position instance.
    """
    from core.trading.position import Position
    return Position(
        side=side,
        entry_price=entry_price,
        quantity=quantity,
        margin=margin,
    )


def make_trading_task_params(
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    initial_cash: float = 100_000,
    leverage: int = 1,
    direction: str = "long",
    fee: float = 0.001,
) -> dict:
    """Create params dict for POST /api/trading/tasks.

    Args:
        symbol: Trading pair.
        timeframe: Bar timeframe.
        initial_cash: Initial cash balance.
        leverage: Leverage multiplier.
        direction: "long" or "short".
        fee: Trading fee rate.

    Returns:
        Dict suitable as JSON body for trading task creation.
    """
    dna = make_dna(direction=direction, leverage=leverage, timeframe=timeframe)
    return {
        "dna_json": dna.to_json(),
        "symbol": symbol,
        "timeframe": timeframe,
        "initial_cash": initial_cash,
        "leverage": leverage,
        "direction": direction,
        "fee": fee,
    }
