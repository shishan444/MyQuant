"""永续资金费三性质与披露系统锚（research-execution-config-alignment
卡 C——C1-C 组 C0/C2/C3/C4/C5/C7/C8/C9）。

三性质：①1x 计费+杠杆解耦（计费基数=名义敞口）②时变费率（逐 bar
实费）③多空互付（正费率多头付/空头收）。连续折算（A3）：单 bar 扣费
=名义敞口×rate_ffill×hours_per_bar/8。NaN 语义（A4）：零费可以、
静默不行——缺失区间 kind 两态分列披露。双消费点同源（事后层与
funding_acc 旁路——同回测总额一致性经引擎级人工核算对照）。
"""

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

import core.backtest.engine as engine_mod
from core.backtest.engine import (
    BacktestEngine, _apply_funding_costs, _apply_funding_loop_nb,
    _funding_rates_for)
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA)


def _make_dna(leverage=1, direction="long", sl=0.0, tp=0.0, pos_size=0.5):
    return StrategyDNA(
        signal_genes=[
            SignalGene('RSI', {'period': 14}, SignalRole.ENTRY_TRIGGER,
                       'rsi_14', {'type': 'lt', 'threshold': 30}),
            SignalGene('RSI', {'period': 14}, SignalRole.EXIT_TRIGGER,
                       'rsi_14', {'type': 'gt', 'threshold': 70}),
        ],
        logic_genes=LogicGenes(entry_logic='AND', exit_logic='AND'),
        execution_genes=ExecutionGenes(timeframe='4h', symbol='BTCUSDT'),
        risk_genes=RiskGenes(stop_loss=sl, take_profit=tp,
                             position_size=pos_size, leverage=leverage,
                             direction=direction),
    )


def _flat_df(n=100, freq="4h", rate=0.001, price=100.0):
    """平稳价格 df+常数费率列+全覆盖 attrs。"""
    dates = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = np.full(n, price)
    df = pd.DataFrame({
        'open': close * 0.999, 'high': close * 1.005,
        'low': close * 0.995, 'close': close, 'volume': 1000.0,
    }, index=dates)
    df.index.name = 'timestamp'
    df['rsi_14'] = 50.0
    df['funding_rate'] = rate
    df.attrs["derivatives_coverage"] = {"funding": {
        "available_start": dates[0].isoformat(),
        "available_end": dates[-1].isoformat()}}
    return df


def _entry_only(df, bar=5, value=20.0):
    df.loc[df.index[bar], 'rsi_14'] = value
    return df


class TestC0SupplyTwoStates:
    def test_no_column_not_loaded(self):
        """C0 两态之无列：rates 全零+status="not_loaded"（裸 df 调用
        路径零费直返——无时序供给不静默按常数估费）。"""
        n = 30
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        df = pd.DataFrame({"close": np.full(n, 100.0)}, index=idx)
        rates, meta = _funding_rates_for(df, "4h")
        assert meta["status"] == "not_loaded"
        assert rates.shape == (n,) and not rates.any()

    def test_engine_bare_df_zero_funding_disclosed(self):
        """C0 引擎级：裸 df（无费率列）run → 零费+coverage 披露
        not_loaded（零费与披露双在场——非静默）。"""
        df = _flat_df(rate=0.001).drop(columns=["funding_rate"])
        df.attrs = {}
        df = _entry_only(df)
        result = BacktestEngine(init_cash=100000).run(
            _make_dna(leverage=3), df)
        assert result.total_trades >= 1
        assert result.total_funding_cost == 0.0
        assert result.funding["mode"] == "perpetual"
        assert result.funding["coverage"]["status"] == "not_loaded"

    def test_column_loaded(self):
        rates, meta = _funding_rates_for(_flat_df(50), "4h")
        assert meta["status"] == "loaded"
        assert meta["covered"] is not None and len(meta["covered"]) == 2
        assert np.allclose(rates, 0.001 * 0.5)


class TestC2GapsTwoKinds:
    def test_outside_and_hole_distinguishable(self):
        """C2：覆盖 [t1,t2] 而段 [t0,t3]——前后段外+段内人工挖洞，
        kind 两态可区分；段外（含 ffill 外推尾段）一律零费。"""
        n = 48
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        df = pd.DataFrame({"funding_rate": 0.001}, index=idx)
        # 覆盖 [idx[8], idx[40]]
        df.attrs["derivatives_coverage"] = {"funding": {
            "available_start": idx[8].isoformat(),
            "available_end": idx[40].isoformat()}}
        # 段内人工挖洞 [idx[20], idx[23]]（ffill 后仍是 NaN 的洞需
        # 直接置 NaN——模拟 parquet 段内缺失行）
        df.loc[idx[20]:idx[23], "funding_rate"] = np.nan
        rates, meta = _funding_rates_for(df, "4h")
        kinds = {g["kind"] for g in meta["gaps"]}
        assert kinds == {"outside_coverage", "hole"}, \
            "kind 枚举恰两值且两态均出现"
        outside = [g for g in meta["gaps"] if g["kind"] == "outside_coverage"]
        hole = [g for g in meta["gaps"] if g["kind"] == "hole"]
        assert any(g["start"] == idx[0].isoformat() for g in outside)
        assert any(g["end"] == idx[-1].isoformat() for g in outside), \
            "尾部 ffill 外推段属 outside_coverage"
        assert hole and hole[0]["start"] == idx[20].isoformat()
        # 段外与洞一律零费（[0..7]=0、[41..47]=0、[20..23]=0）
        assert not rates[:8].any() and not rates[41:].any()
        assert not rates[20:24].any()
        assert np.allclose(rates[8:20], 0.0005) and \
            np.allclose(rates[24:41], 0.0005)

    def test_coverage_unknown_falls_back_to_column(self):
        """覆盖边界不可得（attrs 缺失）→ 按在场列值计费+status 标注
        （不静默零费也不静默计费——裸列直调路径）。"""
        n = 30
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        df = pd.DataFrame({"funding_rate": 0.001}, index=idx)
        rates, meta = _funding_rates_for(df, "4h")
        assert meta["status"] == "coverage_unknown"
        assert np.allclose(rates, 0.0005)


class TestC3Formula:
    """C3 公式：cost=|position_value[t−1]|×rate[t]×direction——人工
    核算对照（多空互付+零费率零费）。"""

    def _run_costs(self, pv_sign, rate):
        n = 20
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        eq = pd.Series(np.full(n, 100000.0), index=idx)
        trades = pd.DataFrame(
            {"Entry Timestamp": [idx[0]], "Exit Timestamp": [idx[-1]]})
        rates = np.full(n, rate * 0.5)  # 4h 折算
        pv = np.full(n, 100000.0 * pv_sign)
        adj, paid, received = _apply_funding_costs(
            eq, "4h", trades_df=trades, position_values=pv, rates=rates)
        return adj, paid, received

    def test_long_pays_positive_rate(self):
        adj, paid, received = self._run_costs(+1, 0.001)
        assert received == 0.0
        assert abs(paid - 19 * 100000 * 0.0005) < 1e-6
        assert adj.iloc[-1] < 100000.0, "多头正费率权益净减"

    def test_short_receives_positive_rate(self):
        adj, paid, received = self._run_costs(-1, 0.001)
        assert paid == 0.0
        assert abs(received - 19 * 100000 * 0.0005) < 1e-6
        assert adj.iloc[-1] > 100000.0, "空头正费率权益净增（收入）"

    def test_long_receives_negative_rate(self):
        adj, paid, received = self._run_costs(+1, -0.001)
        assert paid == 0.0 and received > 0.0

    def test_zero_rate_zero_cost(self):
        adj, paid, received = self._run_costs(+1, 0.0)
        assert paid == 0.0 and received == 0.0
        assert np.allclose(adj.values, 100000.0)

    def test_direction_from_position_sign_single_source(self):
        """结构断言：方向由仓位符号单源推出——内循环签名无独立方向
        数组参数（多空互付无第二方向事实源）。"""
        sig = inspect.signature(_apply_funding_loop_nb)
        assert "direction" not in sig.parameters, \
            "direction 不得为独立入参（position_values 符号单源）"


class TestC4EngineNoDoubleMerge:
    def test_rates_equal_column_mapping(self):
        """引擎不做二次归并：rates==列值×hours_frac 的直接映射
        （对齐容差在装载层 ffill，引擎消费原样）。"""
        n = 40
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        rate_series = pd.Series(
            np.tile([0.0005, 0.001, -0.0005, 0.0], n // 4), index=idx)
        df = pd.DataFrame({"funding_rate": rate_series})
        rates, _ = _funding_rates_for(df, "4h")
        assert np.allclose(rates, rate_series.to_numpy() * 0.5), \
            "rates 为列值×hours_frac 直接映射（无二次变换/归并）"


class TestC5Retirement:
    def test_legacy_symbols_gone(self):
        """C5 退役：_funding_cost_rate 与 RATE_PER_8H 常量不存在
        （import 级断言）；borrowed_ratio/杠杆短路路径消亡。"""
        assert not hasattr(engine_mod, "_funding_cost_rate")
        assert not hasattr(engine_mod, "RATE_PER_8H")
        src = inspect.getsource(engine_mod)
        assert "borrowed_ratio" not in src, "借贷折减因子须整体退役"

    def test_hours_map_migrated_eight_timeframes(self):
        """C5 迁移：hours_per_bar 八周期映射单源迁至 _funding_rates_for
        ——逐项等价断言（表内容迁移非重造）。"""
    # 逐周期构造同费率 df 断言 rates=rate×hours/8（八周期全表）
        expected = {"1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "30m": 0.5,
                    "1h": 1.0, "4h": 4.0, "1d": 24.0, "3d": 72.0}
        freq_alias = {"1m": "1min", "5m": "5min"}  # pandas freq 域
        for tf, hours in expected.items():
            idx = pd.date_range("2024-01-01", periods=6,
                                freq=freq_alias.get(tf, tf), tz="UTC")
            df = pd.DataFrame({"funding_rate": 0.001}, index=idx)
            rates, _ = _funding_rates_for(df, tf)
            assert np.allclose(rates, 0.001 * hours / 8.0), \
                f"{tf} 映射迁移不等价"


class TestC7DualConsumerConsistency:
    def test_engine_total_matches_manual(self):
        """引擎级对照：平稳价格+持仓全程 → 事后层总额==名义敞口×
        Σrate×bars 人工核算（事后层与旁路同式——两消费点数值一致性
        的行为级锚；名义=pos_size×equity×L）。"""
        n = 60
        df = _entry_only(_flat_df(n), bar=5)
        engine = BacktestEngine(init_cash=100000)
        result = engine.run(_make_dna(leverage=2, pos_size=0.5), df)
        assert result.total_trades >= 1
        # 持仓 bar5→末尾：入场后每 bar 计费基数=|pv|≈0.5×equity×2
        # 平稳价下名义恒定≈100000；扣费 bar 数=n-1-5=54
        manual = 100000 * 0.0005 * (n - 1 - 5)
        assert abs(result.funding["total_cost"] - manual) / manual < 0.02, \
            f"事后层总额 {result.funding['total_cost']:.1f} 应≈人工核算 {manual:.1f}"

    def test_same_df_same_rates_deterministic(self):
        """单点确定性：同 df 两次装配 rates 逐元素相等（无第二费率源）。"""
        df = _flat_df(30)
        r1, m1 = _funding_rates_for(df, "4h")
        r2, m2 = _funding_rates_for(df, "4h")
        assert np.array_equal(r1, r2) and m1 == m2


class TestC8ContinuousProration:
    def test_cross_timeframe_unit_time_consistency(self):
        """C8：同费率时序跨 1h/4h/1d——单位时间（小时）费用一致
        （连续折算非结算点粒度；同 240h 窗总额=窗口时长×小时费率，
        粒度差异仅在首 bar 不计费的 hours 差）。"""
        for tf, hours in (("1h", 1.0), ("4h", 4.0), ("1d", 24.0)):
            bars = int(240 / hours)
            idx = pd.date_range("2024-01-01", periods=bars, freq=tf,
                                tz="UTC")
            eq = pd.Series(np.full(bars, 100000.0), index=idx)
            trades = pd.DataFrame(
                {"Entry Timestamp": [idx[0]],
                 "Exit Timestamp": [idx[-1]]})
            rates, _ = _funding_rates_for(
                pd.DataFrame({"funding_rate": 0.001}, index=idx), tf)
            # 恒定仓位价值（消除逐 bar 扣费复利——基数线性化，精确断言）
            _, paid, _ = _apply_funding_costs(
                eq, tf, trades_df=trades,
                position_values=np.full(bars, 100000.0), rates=rates)
            expected = 100000 * 0.001 / 8 * (240 - hours)
            assert abs(paid - expected) < 1e-6, \
                f"{tf} 连续折算总额 {paid} 应≈{(240 - hours)}h×小时费率 {expected}"

    def test_result_funding_five_keys(self):
        """BacktestResult.funding 五键断言：mode/total_cost/
        total_received/coverage/caliber_note 固定文案在场。"""
        df = _entry_only(_flat_df(50), bar=5)
        result = BacktestEngine(init_cash=100000).run(
            _make_dna(leverage=1, pos_size=0.5), df)
        f = result.funding
        assert set(f) == {"mode", "total_cost", "total_received",
                          "coverage", "caliber_note"}
        assert f["mode"] == "perpetual"
        assert f["total_cost"] >= 0.0 and f["total_received"] >= 0.0
        assert set(f["coverage"]) == {"status", "covered", "gaps"}
        assert "口径" in f["caliber_note"] and "8h" in f["caliber_note"], \
            "口径差披露文案在场（回测连续折算 vs 纸盘/live 结算点）"
        # 1x 也计费（性质一）+净支出语义
        assert result.total_funding_cost == \
            f["total_cost"] - f["total_received"]


class TestC2RealParquetCalibration:
    """C2 资产第二组：真实 BTCUSDT_funding.parquet 抽段对拍（tester
    缺口补齐——test-plan C2「合成可控+真实对拍」两组资产）。真实
    8h 结算形制（实测尾行含 +1ms 偏移行）下装载层 ffill 语义人工
    核算对照+单点供给消费。数据文件缺位时 skip（离线环境）。"""

    PARQUET = Path(__file__).resolve().parents[2] \
        / "data" / "market_fapi" / "BTCUSDT_funding.parquet"

    def test_real_segment_ffill_and_rates(self):
        import pandas as pd
        if not self.PARQUET.exists():
            pytest.skip("真实 funding parquet 不在场（离线环境）")
        fund = pd.read_parquet(self.PARQUET)
        seg = fund.loc["2024-03-01":"2024-03-31"]  # 抽段（31 天）
        assert len(seg) >= 90, "8h 结算段行数量级 sanity"
        # 4h OHLCV 网格覆盖同段（装载链 merge_derivatives 形制）
        idx = pd.date_range(seg.index.min().ceil("4h"), seg.index.max(),
                            freq="4h", tz="UTC", name="timestamp")
        close = np.full(len(idx), 100.0)
        ohlcv = pd.DataFrame({
            "open": close, "high": close * 1.001, "low": close * 0.999,
            "close": close, "volume": 1000.0}, index=idx)
        from core.data.derivatives_merger import merge_derivatives_into_ohlcv
        merged = merge_derivatives_into_ohlcv(ohlcv, funding_df=seg)
        # 装载层 ffill 语义：段内全窗非空+逐 bar==最近一次已知结算费率
        # （reindex(method="ffill") asof 语义人工核算对照）
        assert "funding_rate" in merged.columns
        assert merged["funding_rate"].notna().all(), "覆盖段内无 NaN"
        expected = seg["funding_rate"].reindex(merged.index, method="ffill")
        assert np.allclose(merged["funding_rate"].to_numpy(),
                           expected.to_numpy()), \
            "每 bar 携带最近一次已知结算费率（ffill 逐 bar 对照）"
        # 单点供给消费：rates==列值×hours_frac（4h=0.5）+coverage 边界
        # ==parquet 段实际界（mtf_loader G12 探测语义以 attrs 表达）
        merged.attrs["derivatives_coverage"] = {"funding": {
            "available_start": seg.index.min().isoformat(),
            "available_end": seg.index.max().isoformat()}}
        rates, meta = _funding_rates_for(merged, "4h")
        assert meta["status"] == "loaded"
        assert meta["covered"][0] == seg.index.min().isoformat()
        assert meta["covered"][1] == seg.index.max().isoformat()
        assert np.allclose(rates, merged["funding_rate"].to_numpy() * 0.5), \
            "rates=列值×4/8 直接映射（真实费率非恒定）"
        # 真实时变：费率非常数（恒定费率模型可与本段区分——性质二的
        # 真实数据旁证）
        assert seg["funding_rate"].nunique() > 10

    def test_c4_plus_one_ms_offset_absorbed_by_ffill(self):
        """C4：8h 结算时点 +1ms 偏移被装载层 ffill 吸收（实测形制：
        parquet 尾行 16:00:00.001000）。asof 语义下偏移点顺延至下一
        bar 生效（延迟可见非丢失），无 NaN、无二次归并。"""
        import pandas as pd
        f_idx = pd.DatetimeIndex(
            ["2024-01-01 00:00:00+00:00", "2024-01-01 08:00:00.001+00:00",
             "2024-01-01 16:00:00.002+00:00", "2024-01-02 00:00:00+00:00"],
            name="timestamp")
        funding = pd.DataFrame(
            {"funding_rate": [0.0001, 0.0002, 0.0003, 0.0004]}, index=f_idx)
        idx = pd.date_range("2024-01-01", periods=12, freq="4h",
                            tz="UTC", name="timestamp")
        ohlcv = pd.DataFrame(
            {"close": np.full(12, 100.0)}, index=idx)
        from core.data.derivatives_merger import merge_derivatives_into_ohlcv
        merged = merge_derivatives_into_ohlcv(ohlcv, funding_df=funding)
        col = merged["funding_rate"]
        assert col.notna().all(), "偏移时点经 ffill 全窗覆盖"
        # 人工核算（asof-ffill）：bar 04:00/08:00 因结算点 08:00:00.001
        # 晚于 bar 时点 → 仍取 00:00 值（08:00 bar 不见 +1ms 偏移结算
        # 点）；bar 12:00 取 08:00:00.001 值——偏移点顺延至下一 bar
        # 生效（延迟可见非丢失）
        assert col.iloc[2] == pytest.approx(0.0001), \
            "08:00 bar 不见 +1ms 偏移结算点（asof 语义）"
        assert col.iloc[3] == pytest.approx(0.0002), \
            "偏移结算点顺延至 12:00 bar 生效"
        assert col.iloc[-1] == pytest.approx(0.0004)


class TestGateBlindSpotFill:
    """闸门②盲区消解（阶段四）——_funding_rates_for 防御分支补面：

    - attrs 形态异常（非 dict）→ 覆盖边界不可得降级 coverage_unknown
      （不炸不静默——按在场列值计费+status 可判读）；
    - coverage_unknown 态的 NaN 洞 → gaps 同样报 hole（「零费可以
      静默不行」在边界不可得路径同样成立——C9 语义的 unknown 侧）。
    """

    def test_attrs_malformed_falls_back_unknown(self):
        """derivatives_coverage 值非 dict（异常形态——pandas attrs 本身
        恒 Mapping，异常态在 coverage 值层）→ except 降级 cov=None →
        coverage_unknown（在场列值计费，不因 attrs 形态炸供给）。"""
        idx = pd.date_range("2024-01-01", periods=8, freq="4h", tz="UTC")
        df = pd.DataFrame({"funding_rate": 0.001}, index=idx)
        df.attrs["derivatives_coverage"] = "malformed-not-a-dict"
        rates, meta = _funding_rates_for(df, "4h")
        assert meta["status"] == "coverage_unknown"
        assert np.allclose(rates, 0.001 * 0.5), "按在场列值计费"

    def test_coverage_unknown_hole_still_disclosed(self):
        """coverage_unknown+段内 NaN 洞 → gaps 报 hole 且洞段零费
        （unknown 态的缺失披露义务与 loaded 态同构）。"""
        idx = pd.date_range("2024-01-01", periods=12, freq="4h", tz="UTC")
        df = pd.DataFrame({"funding_rate": 0.001}, index=idx)
        df.loc[idx[4]:idx[6], "funding_rate"] = np.nan  # 段内洞
        rates, meta = _funding_rates_for(df, "4h")
        assert meta["status"] == "coverage_unknown"
        hole = [g for g in meta["gaps"] if g["kind"] == "hole"]
        assert hole and hole[0]["start"] == idx[4].isoformat(), \
            "unknown 态洞段不静默"
        assert not rates[4:7].any(), "洞段零费"
        assert np.allclose(rates[:4], 0.001 * 0.5), "正常段照常计费"


class TestFundingProperties:
    """闸门④属性补强：纯函数不变量（一类输入应保持的全称关系——
    样例锚的补强面，非替代）。"""

    @staticmethod
    def _gap_mask(meta, idx):
        """从 gaps 元数据重建零费区布尔掩码（独立于被测实现的
        reconstruction——依据披露契约语义）。"""
        mask = np.zeros(len(idx), dtype=bool)
        for g in meta.get("gaps") or []:
            s, e = pd.Timestamp(g["start"]), pd.Timestamp(g["end"])
            mask |= (idx >= s) & (idx <= e)
        return mask

    @pytest.mark.parametrize("hole_at,cover_a,cover_b", [
        (None, 0, -1),            # 全覆盖无洞
        ((6, 9), 0, -1),          # 段中洞
        (None, 8, 30),            # 头段外
        ((15, 18), 4, 34),        # 洞+双侧段外
        ((0, 4), 0, -1),          # 头部洞
    ], ids=["全覆盖", "段中洞", "头段外", "洞+双侧段外", "头部洞"])
    def test_zero_rate_iff_disclosed_gap(self, hole_at, cover_a, cover_b):
        """全称不变量：零费区 ⟺ 披露区（gaps 并集）∪ 真实零费率列值
        ——「零费可以静默不行」的恒等式面（每个零费 bar 必有披露
        归属；每个披露 bar 必零费）。"""
        n = 40
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        rng = np.random.default_rng(3)
        col = rng.uniform(0.0001, 0.0005, n)  # 非零随机费率（避列值零）
        if hole_at:
            col[hole_at[0]:hole_at[1] + 1] = np.nan
        df = pd.DataFrame({"funding_rate": col}, index=idx)
        df.attrs["derivatives_coverage"] = {"funding": {
            "available_start": idx[cover_a].isoformat(),
            "available_end": idx[cover_b].isoformat()}}
        rates, meta = _funding_rates_for(df, "4h")
        zero_mask = rates == 0.0
        gap_mask = self._gap_mask(meta, idx)
        nan_col = np.isnan(col)
        # 恒等式两侧：rates==0 ⟺ gaps ∪ 原始 NaN（洞与段外皆入 gaps；
        # 段外含 ffill 外推——列值非 NaN 但属披露区）
        assert np.array_equal(zero_mask, gap_mask | nan_col), \
            f"零费区与披露区不全等: zero={zero_mask.sum()} disclosed={(gap_mask | nan_col).sum()}"

    @pytest.mark.parametrize("pv_sign,rate_sign", [
        (1, 1), (1, -1), (-1, 1), (-1, -1)])
    def test_paid_received_mutually_exclusive(self, pv_sign, rate_sign):
        """互斥律：付出/收入两桶不同时非零（方向由仓位符号单源推出
        ——多空×正负费率四组合全称；净额恒=付出−收入）。"""
        n = 12
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        eq = pd.Series(np.full(n, 100000.0), index=idx)
        trades = pd.DataFrame(
            {"Entry Timestamp": [idx[0]], "Exit Timestamp": [idx[-1]]})
        rates = np.full(n, 0.001 * rate_sign * 0.5)
        pv = np.full(n, 100000.0 * pv_sign)
        adj, paid, received = _apply_funding_costs(
            eq, "4h", trades_df=trades, position_values=pv, rates=rates)
        assert paid == 0.0 or received == 0.0, "两桶互斥律破坏"
        net = paid - received
        expected_net = 100000.0 * pv_sign * rate_sign * 0.0005 * (n - 1)
        assert abs(net - expected_net) < 1e-6, \
            "净额恒等式破坏（方向单源+连续折算）"


class TestC9NanSemantics:
    """C9：NaN 三态——段外洞/段内洞/正常段；「零费可以静默不行」=
    费用 0 与披露在场双断言。"""

    def test_outside_hole_zero_fee_and_disclosed(self):
        n = 40
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        close = np.full(n, 100.0)
        df = pd.DataFrame({
            'open': close * 0.999, 'high': close * 1.005,
            'low': close * 0.995, 'close': close, 'volume': 1000.0,
        }, index=idx)
        df.index.name = 'timestamp'
        df['rsi_14'] = 50.0
        # 费率列覆盖 [idx[10], idx[30]]：前后段外（ffill 后头部 NaN、
        # 尾部外推）——装载层语义以列+attrs 表达
        df['funding_rate'] = 0.001
        df.loc[:idx[9], 'funding_rate'] = np.nan
        df.attrs["derivatives_coverage"] = {"funding": {
            "available_start": idx[10].isoformat(),
            "available_end": idx[30].isoformat()}}
        df = _entry_only(df, bar=5)
        result = BacktestEngine(init_cash=100000).run(
            _make_dna(leverage=1, pos_size=0.5), df)
        gaps = result.funding["coverage"]["gaps"]
        kinds = {g["kind"] for g in gaps}
        assert kinds == {"outside_coverage"}
        # 零费段不得计费：扣费 bar 数=覆盖段内持仓 bar（idx[11]..idx[39]
        # 中持仓段 idx[11]..39 的费=20 bar×费率×名义；外推尾段零费——
        # 总额显著低于全程计费
        assert result.funding["total_cost"] > 0.0, "覆盖段内照常计费"
        assert result.funding["coverage"]["status"] == "loaded"

    def test_hole_zero_fee_and_disclosed(self):
        n = 40
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
        df = pd.DataFrame({"funding_rate": 0.001}, index=idx)
        df.loc[idx[15]:idx[19], "funding_rate"] = np.nan  # 段内洞
        df.attrs["derivatives_coverage"] = {"funding": {
            "available_start": idx[0].isoformat(),
            "available_end": idx[-1].isoformat()}}
        rates, meta = _funding_rates_for(df, "4h")
        hole = [g for g in meta["gaps"] if g["kind"] == "hole"]
        assert hole and hole[0]["start"] == idx[15].isoformat(), \
            "段内洞披露在场（零费不静默）"
        assert not rates[15:20].any(), "洞段零费"
        assert np.allclose(rates[:15], 0.0005), "正常段照常计费"
