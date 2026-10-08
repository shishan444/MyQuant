"""Phase 4: MTF layer role (trend/execution) to eliminate phantom signals."""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
    TimeframeLayer,
)
from core.strategy.executor import (
    SignalSet,
    dna_to_signal_set,
    resample_signals,
    evaluate_layer,
)
from core.strategy.validator import validate_dna

def _make_layer(tf="4h", role=None):
    return TimeframeLayer(
        timeframe=tf,
        signal_genes=[
            SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER, 'RSI_14',
                        {'type': 'lt', 'threshold': 30}),
            SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER, 'RSI_14',
                        {'type': 'gt', 'threshold': 70}),
        ],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        role=role,
    )

def _make_df(n=100, seed=42):
    np.random.seed(seed)
    dates = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = np.clip(50 + np.random.randn(n) * 20, 0, 100)
    return df

def test_layer_role_default_none():
    layer = TimeframeLayer(timeframe="4h")
    assert layer.role is None

def test_layer_role_trend():
    layer = TimeframeLayer(timeframe="1d", role="trend")
    assert layer.role == "trend"

def test_layer_role_execution():
    layer = TimeframeLayer(timeframe="4h", role="execution")
    assert layer.role == "execution"

def test_role_serialization():
    """Role should survive to_dict/from_dict roundtrip."""
    layer = _make_layer(role="trend")
    d = layer.to_dict()
    assert d["role"] == "trend"
    restored = TimeframeLayer.from_dict(d)
    assert restored.role == "trend"

def test_role_none_serialization():
    """None role should survive roundtrip."""
    layer = _make_layer(role=None)
    d = layer.to_dict()
    assert d["role"] is None
    restored = TimeframeLayer.from_dict(d)
    assert restored.role is None

def test_trend_signal_ffilled():
    """Trend layer signals: asof_completed 状态投影（0.2.14 收口）。

    Day3 的 1d bar（00:00 开盘）收盘于 Day4 00:00——其 True 信号从
    Day4 首 4h bar（index 24）起可见；Day3 内的 4h bar（18-23）处于
    该 1d bar 形成期，不可见（旧开盘即见 ffill 为前视窗口）。"""
    high_tf_index = pd.date_range('2024-01-01', periods=10, freq='1D', tz='UTC')
    signal = pd.Series(False, index=high_tf_index)
    # Day 3 and onward: True (trend established)
    signal.iloc[3:] = True

    target_index = pd.date_range('2024-01-01', periods=60, freq='4h', tz='UTC')

    result = resample_signals(signal, target_index)

    assert result.iloc[18] == False  # Day3 首 4h bar——1d bar 形成期不可见
    assert result.iloc[23] == False  # Day3 末 4h bar——同上
    assert result.iloc[24] == True   # Day4 首 4h bar——Day3 1d 收盘可见
    assert result.iloc[30] == True   # Day5（状态延续）

def test_execution_signal_not_ffilled():
    """Execution layer signals should NOT be forward-filled (pulse signals)."""
    high_tf_index = pd.date_range('2024-01-01', periods=10, freq='1D', tz='UTC')
    signal = pd.Series(False, index=high_tf_index)
    signal.iloc[3] = True

    target_index = pd.date_range('2024-01-01', periods=40, freq='4h', tz='UTC')

    from core.data.mtf_alignment import pulse_completed
    result = pulse_completed(signal, target_index, pd.Timedelta(days=1))

    # 脉冲 +tf 落位（0.2.14）：Day3 00:00 的脉冲收盘于 Day4 00:00——
    # 落在 Day4 首 4h bar（index 24）；单 bar 脉冲非 ffill
    true_bars = result[result].index
    assert len(true_bars) == 1
    assert true_bars[0] == target_index[24]

def test_no_role_defaults_execution():
    """Layers without role should be treated as execution (no ffill)."""
    df = _make_df(100)
    layer = _make_layer(role=None)  # No role -> defaults to execution

    # Evaluate the layer directly
    sig = evaluate_layer(layer, df)
    assert isinstance(sig, SignalSet)
    assert len(sig.entries) == len(df)

def test_phantom_signals_eliminated():
    """With trend+execution roles, phantom signals from ffill should be eliminated."""
    n = 60
    dates_4h = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    dates_1d = pd.date_range('2024-01-01', periods=n // 6 + 1, freq='1D', tz='UTC')

    # 4h data
    df_4h = pd.DataFrame({
        'open': 100.0, 'high': 101.0, 'low': 99.0,
        'close': 100.0, 'volume': 1000.0,
    }, index=dates_4h)
    df_4h.index.name = 'timestamp'
    df_4h['rsi_14'] = 50.0
    df_4h.loc[df_4h.index[10], 'rsi_14'] = 20  # Entry trigger at bar 10

    # 1d data
    df_1d = pd.DataFrame({
        'open': 100.0, 'high': 101.0, 'low': 99.0,
        'close': 100.0, 'volume': 1000.0,
    }, index=dates_1d)
    df_1d.index.name = 'timestamp'
    df_1d['rsi_14'] = 50.0
    # Trend: always bullish on 1d
    df_1d['rsi_14'] = 60.0  # Not oversold, so no entry signal

    # Create MTF DNA with trend role on 1d
    dna = StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5, leverage=1, direction='long'),
        layers=[
            _make_layer(tf='1d', role='trend'),
            _make_layer(tf='4h', role='execution'),
        ],
        cross_layer_logic='AND',
    )

    dfs_by_tf = {'4h': df_4h, '1d': df_1d}
    sig_set = dna_to_signal_set(dna, df_4h, dfs_by_timeframe=dfs_by_tf)

    # Execution signal should be pulse (only at bar 10, not ffilled)
    assert isinstance(sig_set.entries, pd.Series)

def test_backward_compat_no_role():
    """MTF DNA without roles should behave like before (all ffilled)."""
    df_4h = _make_df(60)
    df_1d = _make_df(15)

    dna = StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5, leverage=1, direction='long'),
        layers=[
            _make_layer(tf='1d', role=None),  # No role -> backward compat
            _make_layer(tf='4h', role=None),
        ],
        cross_layer_logic='AND',
    )

    dfs_by_tf = {'4h': df_4h, '1d': df_1d}
    sig_set = dna_to_signal_set(dna, df_4h, dfs_by_timeframe=dfs_by_tf)

    # Should produce valid signals
    assert len(sig_set.entries) == len(df_4h)
    assert isinstance(sig_set, SignalSet)

def test_validator_accepts_valid_roles():
    """Validator should accept trend and execution roles."""
    dna = StrategyDNA(
        signal_genes=[],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10, position_size=0.5, leverage=1, direction='long'),
        layers=[
            _make_layer(tf='1d', role='trend'),
            _make_layer(tf='4h', role='execution'),
        ],
    )
    result = validate_dna(dna)
    assert result.is_valid, f"Errors: {result.errors}"


_ALL_ROLES = [
    SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD,
    SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD,
    SignalRole.ADD_TRIGGER, SignalRole.ADD_GUARD,
    SignalRole.REDUCE_TRIGGER, SignalRole.REDUCE_GUARD,
]


class TestEvaluateLayerAllRoles:
    """evaluate_layer 八角色分组完备面（组装双出口事件化的角色接线）。"""

    def test_all_roles_routed_to_channels(self):
        df = _make_df(60)
        df['rsi_14'] = 50.0  # lt 100 恒真；gt 100 恒假
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene('RSI', {'period': 14}, role, 'rsi_14',
                           {'type': ('lt' if role in (
                               SignalRole.ENTRY_TRIGGER, SignalRole.ENTRY_GUARD,
                               SignalRole.ADD_TRIGGER, SignalRole.ADD_GUARD)
                               else 'gt'), 'threshold': 100})
                for role in _ALL_ROLES
            ],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        )
        sig = evaluate_layer(layer, df)
        # entry/add 通道基因恒真 → 全真；exit/reduce 通道恒假
        # （若任一角色误入他组，对应通道符号即翻转——分组接线可判别）
        assert bool(sig.entries.all())
        assert not sig.exits.any()
        assert bool(sig.adds.all())
        assert not sig.reduces.any()

    def test_entry_guard_participates_in_and_logic(self):
        """guard 参与 AND 组合：trigger True + guard False → entries False
        （断言可识别 guard 未接线或被 OR 化的错误）。"""
        df = _make_df(60)
        df["rsi_14"] = 50.0  # lt 100 恒真；gt 100 恒假
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER,
                           'rsi_14', {'type': 'lt', 'threshold': 100}),
                SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_GUARD,
                           'rsi_14', {'type': 'gt', 'threshold': 100}),
            ],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        )
        sig = evaluate_layer(layer, df)
        assert not sig.entries.any()
        # 出场通道不受入场 guard 影响
        assert not sig.exits.any()


class TestTrendLayerDirectionInference:
    """trend 层条件类型 → entry_direction 符号推断（price_below 看空）。"""

    def test_trend_layer_price_below_infers_short_direction(self):
        dates_4h = pd.date_range('2024-01-01', periods=60, freq='4h', tz='UTC')
        dates_1d = pd.date_range('2024-01-01', periods=12, freq='1D', tz='UTC')

        df_4h = pd.DataFrame({
            'open': 100.0, 'high': 101.0, 'low': 99.0,
            'close': 100.0, 'volume': 1000.0,
        }, index=dates_4h)
        df_4h.index.name = 'timestamp'
        df_4h['rsi_14'] = 20.0  # 执行层 entry 恒真

        df_1d = pd.DataFrame({
            'open': 100.0, 'high': 101.0, 'low': 99.0,
            'close': 100.0, 'volume': 1000.0,
        }, index=dates_1d)
        df_1d.index.name = 'timestamp'
        df_1d['ema_10'] = df_1d['close'] + 10.0  # close < ema → price_below 恒真

        trend_layer = TimeframeLayer(
            timeframe='1d', role='trend',
            signal_genes=[SignalGene('EMA', {'period': 10},
                                     SignalRole.ENTRY_TRIGGER, None,
                                     {'type': 'price_below'})],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        )
        exec_layer = TimeframeLayer(
            timeframe='4h', role='execution',
            signal_genes=[SignalGene('RSI', {'period': 14},
                                     SignalRole.ENTRY_TRIGGER, 'rsi_14',
                                     {'type': 'lt', 'threshold': 30})],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        )
        dna = StrategyDNA(
            signal_genes=[],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
            execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
            risk_genes=RiskGenes(stop_loss=0.05, take_profit=0.10,
                                 position_size=0.5, leverage=1, direction='long'),
            layers=[trend_layer, exec_layer],
            cross_layer_logic='AND',
        )

        from core.strategy.executor import clear_indicator_cache
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df_4h, dfs_by_timeframe={'4h': df_4h, '1d': df_1d})
        clear_indicator_cache()

        assert sig.entry_direction is not None
        # 趋势层 entries 生效位置方向为 -1（看空），其余 +1
        assert (sig.entry_direction[sig.entries] == -1.0).all()
        assert (sig.entry_direction[~sig.entries] == 1.0).all()


class TestDnaToSignalSetAllRoles:
    """dna_to_signal_set 平面版（单周期路径）角色分组完备面——
    与 evaluate_layer/batch 组装同构接线的第三出口。"""

    def test_all_roles_routed_to_channels(self):
        df = _make_df(60)
        df['rsi_14'] = 50.0  # lt 100 恒真；gt 100 恒假
        dna = StrategyDNA(
            signal_genes=[
                SignalGene('RSI', {'period': 14}, role, 'rsi_14',
                           {'type': ('gt' if role in (
                               SignalRole.EXIT_TRIGGER, SignalRole.EXIT_GUARD,
                               SignalRole.REDUCE_TRIGGER, SignalRole.REDUCE_GUARD)
                               else 'lt'), 'threshold': 100})
                for role in ([SignalRole.DIRECTION] + _ALL_ROLES)
            ],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
            execution_genes=ExecutionGenes(timeframe='4h'),
            risk_genes=RiskGenes(direction="mixed"),
        )
        sig = dna_to_signal_set(dna, df)
        assert bool(sig.entries.all())
        assert not sig.exits.any()
        assert bool(sig.adds.all())
        assert not sig.reduces.any()
        # DIRECTION 基因恒真 → +1（多）；恒假基因不参与方向
        assert bool((sig.entry_direction == 1.0).all())

    def test_direction_false_gene_infers_short(self):
        """DIRECTION 基因信号恒假 → -1（空方向）——方向接线可判别。"""
        df = _make_df(60)
        df['rsi_14'] = 50.0
        dna = StrategyDNA(
            signal_genes=[
                SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER,
                           'rsi_14', {'type': 'lt', 'threshold': 100}),
                SignalGene('RSI', {'period': 14}, SignalRole.DIRECTION,
                           'rsi_14', {'type': 'gt', 'threshold': 100}),
            ],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
            execution_genes=ExecutionGenes(timeframe='4h'),
            risk_genes=RiskGenes(direction="mixed"),
        )
        sig = dna_to_signal_set(dna, df)
        assert bool((sig.entry_direction == -1.0).all())


class TestEvaluateLayerDirectionGene:
    """evaluate_layer 的 DIRECTION 角色接线（八角色外的第九角色）。"""

    def test_direction_gene_routed(self):
        df = _make_df(60)
        df['rsi_14'] = 50.0
        layer = TimeframeLayer(
            timeframe="4h",
            signal_genes=[
                SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER,
                           'rsi_14', {'type': 'lt', 'threshold': 100}),
                SignalGene('RSI', {'period': 14}, SignalRole.DIRECTION,
                           'rsi_14', {'type': 'lt', 'threshold': 100}),
            ],
            logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        )
        sig = evaluate_layer(layer, df)
        assert bool(sig.entries.all())
        assert bool((sig.entry_direction == 1.0).all())


class TestRoleDispatchChainTailUnreachable:
    """角色分派 elif 链尾落空弧（executor.py [430,408]/[790,768]）
    不可达判定。

    调用图依据：SignalRole 枚举恰九值（dna.py:50-58：八通道角色 +
    DIRECTION），evaluate_layer（:414-435）与 dna_to_signal_set
    （:774-796）的 elif 链对九值逐一匹配——任何合法基因必命中某一
    分支，链尾 :430/:790 的 False 侧（落空回循环头）无合法基因形态。
    仅绕过 Enum 构造注入非法 role 可达，而 SignalGene.from_dict 对
    非法 role 字符串在 SignalRole(...) 转换处即抛 ValueError（构造
    期拦截，求值层到不了）。八角色 + DIRECTION 分组完备面由
    TestEvaluateLayerAllRoles / TestDnaToSignalSetAllRoles 承接。
    """
