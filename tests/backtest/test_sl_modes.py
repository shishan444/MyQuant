"""止损模式三分派测试（0.4.2 R2，stop-loss-modes 规格，B3 11-16）。

手工核算纪律：全部期望值独立人工推导（本文件注释附推导过程），
不经被测实现生成。

覆盖：trail 触发点与峰值生命周期（多/空对称/开仓重置）、同 bar 清算
优先、atr 绝对价与动态取列（atr_period 可区分/现算两路径/装载校验
参数化）、trail+TP 并存、pct 区分锚、batch 路径三模式与单路径一致。
交易链拒绝面（三插桩）见 tests/trading/test_trading_link_compatibility.py。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.backtest.engine import BacktestEngine, _load_atr_column
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)
from core.strategy.executor import SignalSet


def mk_df(closes, highs=None, lows=None, atr14=None, **extra_cols):
    n = len(closes)
    idx = pd.DatetimeIndex(
        pd.date_range("2024-01-01", periods=n, freq="4h"), name="timestamp")
    df = pd.DataFrame({
        "open": closes,
        "high": highs if highs is not None else closes,
        "low": lows if lows is not None else closes,
        "close": closes,
        "volume": [100.0] * n,
    }, index=idx)
    if atr14 is not None:
        df["atr_14"] = atr14
    for k, v in extra_cols.items():
        df[k] = v
    return df


def mk_dna(**risk):
    g = [SignalGene("RSI", {"period": 2}, SignalRole.ENTRY_TRIGGER,
                    condition={"type": "lt", "threshold": 30}),
         SignalGene("RSI", {"period": 2}, SignalRole.EXIT_TRIGGER,
                    condition={"type": "gt", "threshold": 70})]
    return StrategyDNA(signal_genes=g, logic_genes=LogicGenes(),
                       execution_genes=ExecutionGenes(),
                       risk_genes=RiskGenes(**risk))


def sig(entries_idx, n, exits_idx=()):
    e = pd.Series(False, index=pd.RangeIndex(n))
    for i in entries_idx:
        e[i] = True
    x = pd.Series(False, index=pd.RangeIndex(n))
    for i in exits_idx:
        x[i] = True
    return SignalSet(entries=e, exits=x,
                     adds=pd.Series(False, index=pd.RangeIndex(n)),
                     reduces=pd.Series(False, index=pd.RangeIndex(n)))


def exit_ts(r):
    if r.trades_df is not None and len(r.trades_df):
        return r.trades_df["Exit Timestamp"].iloc[0]
    return None


T = lambda h: pd.Timestamp("2024-01-01") + pd.Timedelta(hours=4 * h)

eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)

# 手工核算基准数据（信号 shift(1)：bar0 信号 → bar1 以 close[1]=105 成交）
CLOSES = [100, 105, 110, 115, 118, 100, 98, 96, 94, 92]
HIGHS = [101, 106, 111, 116, 120, 102, 100, 98, 96, 94]
LOWS = [99, 104, 109, 114, 117, 95, 97, 95, 93, 91]


class TestTrail:
    """trail 当 bar 口径：peak 先更新（含当 bar high/low）再判定。"""

    def test_long_peak_drawdown_trigger(self):
        """多头：peak 轨迹 106/111/116/120/120，sl 线 100.7/105.45/
        110.2/114.0/114.0——bar5 low=95<=114 触发全部离场（触发 bar 定位）。"""
        r = eng.run(mk_dna(sl_mode="trail", stop_loss=0.05),
                    mk_df(CLOSES, HIGHS, LOWS), signal_set=sig([0], 10))
        assert exit_ts(r) == T(5)
        assert r.total_trades == 1 and not r.liquidated

    def test_short_symmetric(self):
        """空头对称：peak=min(peak,low) 轨迹 104/99/94/91/91，sl 线
        109.2/103.95/98.7/95.55/95.55——bar5 high=97>=95.55 触发。"""
        closes_s = [100, 105, 100, 95, 92, 96, 99, 101, 103, 105]
        highs_s = [101, 106, 101, 96, 93, 97, 100, 102, 104, 106]
        lows_s = [99, 104, 99, 94, 91, 95, 98, 100, 102, 104]
        r = eng.run(mk_dna(sl_mode="trail", stop_loss=0.05, direction="short"),
                    mk_df(closes_s, highs_s, lows_s), signal_set=sig([0], 10))
        assert exit_ts(r) == T(5)

    def test_peak_reset_on_reentry(self):
        """开仓重置跨仓：第一笔 trail 离场后再入场，峰值重置为成交价
        （不沿用前仓峰值）。bar5 离场，bar6 信号 → bar7 成交 close[7]=96，
        峰值自 96 重新追踪——sl=96×0.95=91.2，bar9 low=91<=91.2 触发
        （重置生效的反证：若沿用前仓 peak=120，sl=114 在 bar7 即触发）。"""
        r = eng.run(mk_dna(sl_mode="trail", stop_loss=0.05),
                    mk_df(CLOSES, HIGHS, LOWS), signal_set=sig([0, 6], 10))
        ts = list(r.trades_df["Exit Timestamp"]) if r.trades_df is not None else []
        assert len(ts) == 2, f"两笔（trail+再入场），实际 {len(ts)}"
        assert ts[0] == T(5)
        # 第二笔：bar7 成交 96 → peak 轨迹 98（bar7 high）/98/98/98；
        # sl=91.2；bar8 low=93>91.2 不触发；bar9 low=91<=91.2 触发
        assert ts[1] == T(9), f"第二笔 bar9 触发（重置后峰值自 96 追踪），实际 {ts[1]}"

    def test_same_bar_liquidation_priority(self):
        """同 bar 清算优先单事件（DC-10）：高杠杆+深回撤同 bar 命中
        清算线与 trail 线——清算先执行单事件成交。"""
        r_liq = eng.run(mk_dna(sl_mode="trail", stop_loss=0.05, leverage=10),
                        mk_df(CLOSES, HIGHS, LOWS), signal_set=sig([0], 10))
        r_low = eng.run(mk_dna(sl_mode="trail", stop_loss=0.05, leverage=1),
                        mk_df(CLOSES, HIGHS, LOWS), signal_set=sig([0], 10))
        # 高杠杆触发清算（leverage=10 的深回撤 bar 权益击穿维持线）
        assert r_liq.liquidated
        # 低杠杆同数据纯 trail 离场（单事件——两杠杆各 1 笔）
        assert r_liq.total_trades == 1 and r_low.total_trades == 1

    def test_trail_with_take_profit_coexist(self):
        """trail+TP 并存（SL 先 TP 后）：tp=0.10 锚 entry 105 →
        tp_level=115.5，bar3 high=116>=115.5 触及（此前 trail 未触发：
        bar3 sl=110.2、low=114 不触发）→ bar3 TP 离场。"""
        r = eng.run(mk_dna(sl_mode="trail", stop_loss=0.05, take_profit=0.10),
                    mk_df(CLOSES, HIGHS, LOWS), signal_set=sig([0], 10))
        assert exit_ts(r) == T(3)

    def test_trail_vs_pct_distinguishable(self):
        """trail/pct 区分锚：peak=113（bar3）sl=107.35——bar4 low=107
        <=107.35 触发；pct sl=105×0.95=99.75 全程未触发（期末平仓）。"""
        closes2 = [100, 105, 110, 112, 108, 107, 106, 105, 104, 103]
        highs2 = [101, 106, 111, 113, 109, 108, 107, 106, 105, 104]
        lows2 = [99, 104, 109, 111, 107, 106, 105, 104, 103, 102]
        df2 = mk_df(closes2, highs2, lows2)
        r_trail = eng.run(mk_dna(sl_mode="trail", stop_loss=0.05), df2,
                          signal_set=sig([0], 10))
        r_pct = eng.run(mk_dna(sl_mode="pct", stop_loss=0.05), df2,
                        signal_set=sig([0], 10))
        assert exit_ts(r_trail) == T(4)
        assert exit_ts(r_pct) == T(9)  # 无 SL 触发，期末平仓


class TestAtrMode:
    """atr 倍数语义：止损价=入场价∓stop_loss×ATR（绝对价）。"""

    ATR = [np.nan] + [5.0] * 9  # 首位 NaN=预热（装载合法）

    def test_atr_absolute_price_trigger(self):
        """atr sl=2.0×ATR(5)=10 → sl_level=105−10=95；bar5 low=99>95
        不触发、bar6 low=89<=95 触发；同数据 pct sl=99.75 bar5 触发
        ——两模式触发 bar 可区分（人工核算与 ATR(14) 口径对照）。"""
        closes3 = [100, 105, 110, 112, 108, 100, 90, 88, 86, 84]
        highs3 = [101, 106, 111, 113, 109, 101, 91, 89, 87, 85]
        lows3 = [99, 104, 109, 111, 107, 99, 89, 87, 85, 83]
        df4 = mk_df(closes3, highs3, lows3, atr14=self.ATR)
        r_atr = eng.run(mk_dna(sl_mode="atr", stop_loss=2.0), df4,
                        signal_set=sig([0], 10))
        r_pct = eng.run(mk_dna(sl_mode="pct", stop_loss=0.05), df4,
                        signal_set=sig([0], 10))
        assert exit_ts(r_atr) == T(6)
        assert exit_ts(r_pct) == T(5)

    def test_atr_period_dynamic_column(self):
        """ATR 动态取列可区分：atr_20 值恒 8 → sl=105−2×8=89；同参数
        atr_14（值 5）sl=95——atr_20 触发更晚（数据 low 序列下逐 bar 对照）。"""
        closes3 = [100, 105, 110, 112, 108, 100, 90, 88, 86, 84]
        highs3 = [101, 106, 111, 113, 109, 101, 91, 89, 87, 85]
        lows3 = [99, 104, 109, 111, 107, 99, 89, 87, 85, 83]
        df14 = mk_df(closes3, highs3, lows3, atr14=self.ATR)
        df20 = mk_df(closes3, highs3, lows3, **{
            "atr_20": [np.nan] + [8.0] * 9})
        r14 = eng.run(mk_dna(sl_mode="atr", stop_loss=2.0), df14,
                      signal_set=sig([0], 10))
        r20 = eng.run(
            mk_dna(sl_mode="atr", stop_loss=2.0, atr_period=20), df20,
            signal_set=sig([0], 10))
        # 人工核算：atr_14 sl=95 → bar6 low=89 触发；
        # atr_20 sl=105−16=89 → bar6 low=89<=89 触发（同 bar 但线不同——
        # 两线成交价同，本数据下出场 bar 与权益均不可区分）
        assert exit_ts(r14) == exit_ts(r20)  # 本数据下同 bar 触发
        # 更强的可区分锚：抬高低点使两周期出场 bar 分离——
        # atr_14 sl=95：bar6 low=92<=95 触发；atr_20 sl=89：全程 low>=89，
        # 仅 bar9 low=89<=89 触发（实测 T(6)/T(9)——人工核算一致）
        lows_shallow = [99, 104, 109, 111, 107, 99, 92, 91, 90, 89]
        df_shallow14 = mk_df(closes3, highs3, lows_shallow, atr14=self.ATR)
        df_shallow20 = mk_df(closes3, highs3, lows_shallow, **{
            "atr_20": [np.nan] + [8.0] * 9})
        rs14 = eng.run(mk_dna(sl_mode="atr", stop_loss=2.0), df_shallow14,
                       signal_set=sig([0], 10))
        rs20 = eng.run(
            mk_dna(sl_mode="atr", stop_loss=2.0, atr_period=20),
            df_shallow20, signal_set=sig([0], 10))
        assert exit_ts(rs14) == T(6), \
            f"atr_14（sl=95）应 bar6 触发（low=92<=95），实际 {exit_ts(rs14)}"
        assert exit_ts(rs20) == T(9), \
            f"atr_20（sl=89）应 bar9 触发（low=89<=89），实际 {exit_ts(rs20)}"

    def test_atr_missing_column_computed(self):
        """现算路径：列缺失时 _compute_indicator 现算成功（数据足够）。"""
        df = mk_df(list(range(100, 130)), atr14=None)  # 无 atr 列
        df["open"] = [v - 0.5 for v in range(100, 130)]
        df["high"] = [v + 1.0 for v in range(100, 130)]
        df["low"] = [v - 1.0 for v in range(100, 130)]
        arr = _load_atr_column(df, 14)
        valid = ~np.isnan(arr)
        assert valid.sum() > 0  # 现算产出有效值
        assert not np.isnan(arr[int(np.argmax(valid)):]).any()

    def test_atr_compute_insufficient_rejected(self):
        """现算不可得显式拒：数据短于周期（5 行数据算 ATR(14)）。"""
        df = mk_df(list(range(100, 105)))
        with pytest.raises(ResearchError) as ei:
            _load_atr_column(df, 14)
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"

    @pytest.mark.parametrize(
        "atr_values,case",
        [([np.nan] * 10, "全 NaN 拒"),
         ([np.nan] + [5.0] * 4 + [np.nan] + [5.0] * 4, "中段洞拒"),
         ([np.nan] + [5.0, 5.0, 0.0] + [5.0] * 6, "含≤0 拒")],
        ids=["all-nan", "mid-hole", "non-positive"])
    def test_atr_loading_validation(self, atr_values, case):
        """装载校验（Q-D）：全 NaN/中段洞/≤0 显式拒。"""
        with pytest.raises(ResearchError):
            _load_atr_column(mk_df(list(range(10)), atr14=atr_values), 14)

    def test_atr_warmup_nan_legal(self):
        """头部预热 NaN 合法（前 atr_period bar 周期性缺失不拒）。"""
        arr = _load_atr_column(
            mk_df(list(range(10)), atr14=[np.nan] + [5.0] * 9), 14)
        assert np.isnan(arr[0]) and arr[1] == 5.0

    def test_nan_bar_no_sl_constraint(self):
        """引擎内 NaN bar 该 bar 无 SL 约束（预热期无止损保护——沿
        交易链 NaN guard 先例）：预热 bar 上深回撤不触发 SL（不炸不误判）。"""
        atr_warmup = [np.nan] * 3 + [5.0] * 7  # bar0-2 预热（NaN bar）
        closes3 = [100, 105, 90, 112, 108, 100, 90, 88, 86, 84]
        highs3 = [101, 106, 91, 113, 109, 101, 91, 89, 87, 85]
        lows3 = [99, 104, 88, 111, 107, 99, 89, 87, 85, 83]
        df = mk_df(closes3, highs3, lows3, atr14=atr_warmup)
        # bar1 成交 105；bar2 atr=NaN → 无 SL 约束（low=88 深回撤不触发
        # ——若误按 pct 语义 105×0.95=99.75 或按前值外推则 bar2 出场）；
        # bar3 起 atr=5 → sl=95；bar5 low=99>95 不触发；bar6 low=89 触发
        r = eng.run(mk_dna(sl_mode="atr", stop_loss=2.0), df,
                    signal_set=sig([0], 10))
        assert exit_ts(r) == T(6), f"bar6 触发（bar2 NaN 无约束），实际 {exit_ts(r)}"


class TestPctRegression:
    def test_pct_sl_semantics_unchanged(self):
        """pct 存量语义锚：入场价比例（105×0.95=99.75），bar5 low=99
        <=99.75 触发——与 trail/atr 在同数据上三者触发 bar 全不同。"""
        closes3 = [100, 105, 110, 112, 108, 100, 90, 88, 86, 84]
        highs3 = [101, 106, 111, 113, 109, 101, 91, 89, 87, 85]
        lows3 = [99, 104, 109, 111, 107, 99, 89, 87, 85, 83]
        df = mk_df(closes3, highs3, lows3, atr14=[np.nan] + [5.0] * 9)
        r = eng.run(mk_dna(sl_mode="pct", stop_loss=0.05), df,
                    signal_set=sig([0], 10))
        assert exit_ts(r) == T(5)  # bar5 low=99<=99.75


class TestBatchPathConsistency:
    def test_batch_matches_single_path(self):
        """B7：同一 DNA 三形态（pct/atr/trail）×两路径（batch_run vs
        _build_portfolio）行为一致性（trades 笔数/权益终值）。"""
        closes3 = [100, 105, 110, 112, 108, 100, 90, 88, 86, 84]
        highs3 = [101, 106, 111, 113, 109, 101, 91, 89, 87, 85]
        lows3 = [99, 104, 109, 111, 107, 99, 89, 87, 85, 83]
        atr = [np.nan] + [5.0] * 9
        df = mk_df(closes3, highs3, lows3, atr14=atr)

        dnas = [mk_dna(sl_mode="pct", stop_loss=0.05),
                mk_dna(sl_mode="atr", stop_loss=2.0),
                mk_dna(sl_mode="trail", stop_loss=0.05)]
        sig_sets = [sig([0], 10)] * 3
        batch_results = eng.batch_run(dnas, df, signal_sets=sig_sets)
        for dna, br in zip(dnas, batch_results):
            sr = eng.run(dna, df, signal_set=sig([0], 10))
            assert br.total_trades == sr.total_trades, \
                f"{dna.risk_genes.sl_mode}: batch {br.total_trades} vs 单路径 {sr.total_trades}"
            assert abs(br.total_return - sr.total_return) < 1e-9, \
                f"{dna.risk_genes.sl_mode}: 权益终值分叉"

    def test_batch_per_column_atr_period(self):
        """batch per-column 各取各 DNA 的 atr_period：同 batch 内
        atr_14（sl=95）与 atr_20（sl=89）各自按列执行——low 序列含
        (89,95] 段（bar6 low=94 触发 14 不触发 20）且后续无 low<=89，
        两列出场 bar 可区分（不写死 atr_14 的行为锚）。"""
        closes3 = [100, 105, 110, 112, 108, 100, 96, 95, 94, 93]
        highs3 = [101, 106, 111, 113, 109, 101, 97, 96, 95, 94]
        lows3 = [99, 104, 109, 111, 107, 99, 94, 93, 92, 91]
        df = mk_df(closes3, highs3, lows3, atr14=[np.nan] + [5.0] * 9)
        df["atr_20"] = [np.nan] + [8.0] * 9
        dnas = [mk_dna(sl_mode="atr", stop_loss=2.0),           # atr_14 → sl=95
                mk_dna(sl_mode="atr", stop_loss=2.0, atr_period=20)]  # sl=89
        sig_sets = [sig([0], 10)] * 2
        results = eng.batch_run(dnas, df, signal_sets=sig_sets)
        singles = [eng.run(d, df, signal_set=sig([0], 10)) for d in dnas]
        assert all(
            b.total_trades == s.total_trades and
            abs(b.total_return - s.total_return) < 1e-9
            for b, s in zip(results, singles))
        # 可区分锚（batch 路径 trades 时间列为 bar 序号形制，用单路径
        # 时间戳锚+batch 权益分叉）：atr_14 bar6 触发（94<=95）；atr_20
        # 全程 low>89 期末平——出场 bar 不同 → 两列权益终值必不同
        assert exit_ts(singles[0]) == T(6)
        assert exit_ts(singles[1]) == T(9)
        assert abs(results[0].total_return - results[1].total_return) > 1e-9
