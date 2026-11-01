"""series 档止损线序列输出测试（hypothesis-workbench 组 2.3，spec stop-loss-modes 2 Scenario）。

已知 fx 列手工锚：fx_sl=close*0.95（装载期列→序列首值逐位对账）；
三入口透传经 bridge metrics 单点（run_backtest_on_range 组装——
backtest/backtest-sandbox/inspect-cases 全消费本面）。

补充两面（审查建网批次）：bridge 时间形制契约锚（前端 toTime 消费
兼容）+ batch_run 输出面对拍锚（现状记录语义）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.backtest_bridge import run_backtest_on_range
from core.backtest.engine import BacktestEngine
from core.persistence.db_ext import init_db_ext
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene,
    SignalRole, StrategyDNA, TimeframeLayer,
)
from core.strategy.executor import SignalSet
from tests.helpers.regime_factory import make_two_regime_df


def _env(tmp_path: Path, *, rg: RiskGenes):
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_two_regime_df(n_trend=300, n_range=150, seed=42)
    df["fx_sl"] = df["close"] * 0.95  # 已知 fx 列（手工锚值源）
    df.to_parquet(data_dir / "BTCUSDT_1d.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    gene = lambda role: SignalGene(  # noqa: E731
        "TEMA", {"period": 20}, role, "tema_20", {"type": "lt", "threshold": 1e9})
    dna = StrategyDNA(
        layers=[TimeframeLayer(timeframe="1d",
                               signal_genes=[gene(SignalRole.ENTRY_TRIGGER),
                                             gene(SignalRole.EXIT_TRIGGER)],
                               logic_genes=LogicGenes())],
        execution_genes=ExecutionGenes(timeframe="1d"), risk_genes=rg)
    start = df.index[40].isoformat()
    end = df.index[-1].isoformat()
    return db, data_dir, dna, start, end, df


class TestSlSeriesOutput:
    def test_series_mode_outputs_sequence(self, tmp_path):
        """Scenario: series 档输出——响应含止损价逐 bar 序列（手工锚逐位）。"""
        rg = RiskGenes(sl_mode="series", sl_series_source={"column": "fx_sl"})
        db, data_dir, dna, start, end, df = _env(tmp_path, rg=rg)
        metrics, _, _, bars = run_backtest_on_range(
            db, data_dir, dna, "BTCUSDT", "1d", start, end, warmup_bars=20)
        sl = metrics["sl_series"]
        assert sl is not None and len(sl) > 0
        assert set(sl[0].keys()) == {"time", "value"}
        # 手工锚：序列值=装载列原值——序列索引=bridge 传入 engine 的执行
        # 段 bt_df（[start,end) 切回段，不含 warmup 前缀；engine 侧索引=
        # 调用方传入 df，段形制由调用方装载链决定——CARD-02 注释改实同步）。
        # 锚三件（段形制已定，逐件收紧）：首值=首时间处 fx_sl 原值（逐位）+
        # 首时间=执行段起点 start+序列长=执行段 bar 数
        first_t = pd.Timestamp(sl[0]["time"])
        assert sl[0]["value"] == pytest.approx(df.fx_sl.loc[first_t], abs=1e-6)
        assert first_t == pd.Timestamp(start)
        assert len(sl) == bars

    def test_non_series_mode_none(self, tmp_path):
        """Scenario: 非 series 档（pct）——sl_series=None（null 非空序列非零值）。"""
        rg = RiskGenes(sl_mode="pct", stop_loss=0.05)
        db, data_dir, dna, start, end, _ = _env(tmp_path, rg=rg)
        metrics, _, _, _ = run_backtest_on_range(
            db, data_dir, dna, "BTCUSDT", "1d", start, end, warmup_bars=20)
        assert metrics["sl_series"] is None


class TestBridgeTimeForm:
    """bridge sl_series 时间形制契约锚（审查建网：前端 toTime 消费兼容）。

    前端消费链（web/src/pages/Lab.tsx）——toTime(p.time.replace(" ", "T"))
    → new Date(...)：解析前提是 time 键为「空格分隔 ISO+UTC 偏移后缀」
    字面（str(tz-aware UTC Timestamp) 现状形制）。锚的必然面从 spec
    「前端可绘连续止损线」推导（可绘=每条时间串可被解析为绝对时刻）；
    字面形制记录实现现状——任一环漂移（分隔符/偏移丢失/无法解析）
    本锚红。
    """

    def test_time_str_space_separated_utc_offset_parsable(self, tmp_path):
        """时间键字面形制：空格分隔（无 T）+ "+00:00" 后缀 + replace 后
        ISO 可解析回 UTC 偏移零（new Date 绝对时刻解析前提）。"""
        rg = RiskGenes(sl_mode="series", sl_series_source={"column": "fx_sl"})
        db, data_dir, dna, start, end, _ = _env(tmp_path, rg=rg)
        metrics, _, _, _ = run_backtest_on_range(
            db, data_dir, dna, "BTCUSDT", "1d", start, end, warmup_bars=20)
        sl = metrics["sl_series"]
        assert sl  # 前置：series 档非空（主 Scenario 已锚，此处形制面）
        for p in sl:
            t = p["time"]
            assert isinstance(t, str)
            # 空格分隔形制——前端 replace(" ", "T") 的前提（T 分隔则
            # replace 不变亦可解析，但锚定现状单一形制防静默漂移）
            assert " " in t and "T" not in t, f"非空格分隔形制: {t!r}"
            # UTC 偏移后缀明示——无偏移串 new Date 按本地时区解析，
            # 线与 K 线错位偏移小时数
            assert t.endswith("+00:00"), f"缺 UTC 偏移后缀: {t!r}"
            # 前端等价解析锚（fromisoformat ≙ new Date 对 ISO 带偏移
            # 输入的解析）：replace 后可解析且偏移为零
            dt = datetime.fromisoformat(t.replace(" ", "T"))
            assert dt.utcoffset() == timedelta(0), f"解析后非 UTC 零偏移: {t!r}"


# -- engine 直调形制（batch_run 对拍锚用；沿 tests/backtest/test_sl_modes.py
#    的 mk_df/mk_series_dna/sig 精简复制——指标形制 BB/lower 直取列，
#    免 db 装载依赖（fx 形制走 enrich_with_plugin_columns 须 db_path）--

_CLOSES = [100, 105, 110, 115, 118, 100, 98, 96, 94, 92]
_HIGHS = [101, 106, 111, 116, 120, 102, 100, 98, 96, 94]
_LOWS = [99, 104, 109, 114, 117, 95, 97, 95, 93, 91]
# 手工线值列（阶梯递减——NaN 头部=指标 rolling 预热期合法形态）
_LINE = [np.nan] * 4 + [110.0, 109.0, 108.0, 107.0, 106.0, 105.0]


def _mk_engine_df():
    idx = pd.DatetimeIndex(
        pd.date_range("2024-01-01", periods=10, freq="4h"), name="timestamp")
    df = pd.DataFrame({
        "open": _CLOSES, "high": _HIGHS, "low": _LOWS,
        "close": _CLOSES, "volume": [100.0] * 10,
    }, index=idx)
    df["bb_lower_20_2"] = _LINE  # 手工锚值源（列名=BB(20,2) lower 命名规则）
    return df


def _mk_series_dna():
    g = [SignalGene("RSI", {"period": 2}, SignalRole.ENTRY_TRIGGER,
                    condition={"type": "lt", "threshold": 30}),
         SignalGene("RSI", {"period": 2}, SignalRole.EXIT_TRIGGER,
                    condition={"type": "gt", "threshold": 70})]
    return StrategyDNA(
        signal_genes=g, logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(),
        risk_genes=RiskGenes(
            sl_mode="series",
            sl_series_source={"indicator": "BB",
                              "params": {"period": 20, "std": 2},
                              "field": "lower"}))


def _mk_pct_dna():
    g = [SignalGene("RSI", {"period": 2}, SignalRole.ENTRY_TRIGGER,
                    condition={"type": "lt", "threshold": 30}),
         SignalGene("RSI", {"period": 2}, SignalRole.EXIT_TRIGGER,
                    condition={"type": "gt", "threshold": 70})]
    return StrategyDNA(
        signal_genes=g, logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(),
        risk_genes=RiskGenes(sl_mode="pct", stop_loss=0.05))


def _sig(entries_idx, n):
    e = pd.Series(False, index=pd.RangeIndex(n))
    for i in entries_idx:
        e[i] = True
    return SignalSet(
        entries=e, exits=pd.Series(False, index=pd.RangeIndex(n)),
        adds=pd.Series(False, index=pd.RangeIndex(n)),
        reduces=pd.Series(False, index=pd.RangeIndex(n)))


class TestBatchRunSlSeriesFace:
    """batch_run 路径 sl_series 输出面（对拍锚·现状记录语义）。

    spec 承诺面=backtest/backtest_sandbox/inspect-cases 三入口（bridge
    metrics 单点透传）；batch_run（进化评估链种群批评估）不在承诺范
    围。现状：批量路径结果面 sl_series 恒 None（_last_sl_col 实例属
    性仅 _build_portfolio 单路径装配，batch_run 直调 vbt 不经该点），
    执行面仍生效（线值列经 atr_matrix 逐列装载——test_sl_modes.py
    test_batch_per_column_series_sources 已锚 trades/权益与单路径一致）。
    本锚记录「单路径透出/批量不透出」的分叉现状：未来任一侧漂移
    （批量开始透出，或单路径丢失透出）本锚红，防止静默语义变化。
    """

    def test_batch_run_none_single_run_emits(self):
        """对拍锚：同一 series 档 DNA 同 df——单路径 sl_series 全段透出
        （值=装载列逐位），batch_run 同 DNA 得 None（分叉仅输出面，
        执行面锚 trades 笔数两路一致）。"""
        eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        df = _mk_engine_df()
        dna = _mk_series_dna()
        ss = _sig([0], 10)
        single = eng.run(dna, df, signal_set=ss)
        # 单路径：全段透出，值与装载列逐位对账（含 NaN 头部预热段）
        assert single.sl_series is not None
        assert len(single.sl_series) == len(df)
        assert np.allclose(single.sl_series.to_numpy(),
                           df["bb_lower_20_2"].to_numpy(), equal_nan=True)
        batch = eng.batch_run([dna], df, signal_sets=[ss])
        assert len(batch) == 1
        assert batch[0].sl_series is None  # 现状：批量路径不透出
        # 执行面一致（分叉仅输出面——批量执行语义仍按线值列）
        assert batch[0].total_trades == single.total_trades

    def test_batch_run_mixed_no_leak_after_single(self):
        """混装+无残留：同实例先 run(series)（装配→消费即清空），紧接
        batch_run([series, pct]) 两列全 None——单路径残留不串入批量
        列堆叠（消费即清语义跨路径成立）。"""
        eng = BacktestEngine(init_cash=100000, fee=0.0, slippage=0.0)
        df = _mk_engine_df()
        series_dna, pct_dna = _mk_series_dna(), _mk_pct_dna()
        ss = _sig([0], 10)
        single = eng.run(series_dna, df, signal_set=ss)
        assert single.sl_series is not None  # 前置：单路径已装配透出
        batch = eng.batch_run([series_dna, pct_dna], df, signal_sets=[ss, ss])
        assert [r.sl_series for r in batch] == [None, None]
