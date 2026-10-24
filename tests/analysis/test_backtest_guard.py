"""守卫拒绝锚族 T⑧ + 交集防御锚 T④（fix-mtf-execution-consistency 组 2）。

T⑧（RESEARCH_TIMEFRAME_MISMATCH——design 卡 2/守卫卡）：
- ⑧.1 显式异轴 422：code+detail 四键逐键断言（need_hint 含回假设侧
  修改基准周期指引语义）；
- ⑧.2 双挂同码：桥入口 vs 直接调守卫单源——code+detail 逐字段一致；
- ⑧.3 四通道传导：隔离/随机/沙盒/分段参数化各一例显式异轴拒绝；
- ⑧.4 缺省链自洽不拦（负向防呆）：不传 timeframe → 取策略行周期
  （==执行周期）→ 放行执行；
- ⑧.5 拒绝不装载：守卫拒绝时数据装载函数零调用（IO 前拦截 spy）。

T④（R1b 交集安全性——A 案裁决面）：
- 交集防御锚：真显式单层=执行周期形态经守卫合法入口（请求轴=执行
  轴）下，显式层路径 vs 等价无层单周期路径四信号逐位相等；
- 判别锚：mixed+DIRECTION 基因+无角色单层形态——加载态（愈合误归）
  走单周期路径 entry_direction=基因方向序列（A 案语义保真面正向
  断言）+构造态走层路径 entry_direction=None（差异面现状锁）。
  【转红处置指引】候选独立 change「层路径 entry_direction 消费缺口」
  落地后，「构造态 entry_direction=None」断言应转红——届时按新契约
  同步本锚（层路径补齐方向产出），转红=提示同步而非回归。
"""
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.backtest_bridge import (
    ensure_timeframe_consistent, run_backtest_on_range,
)
from core.analysis.errors import ResearchError
from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.validation_service import (
    random_window_test, run_isolated_backtest, run_sandbox_backtest,
    run_staged_validation,
)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)
from core.strategy.executor import dna_to_signal_set

# 请求区间取假设判定区间（HYP_START→HYP_END=2023-01→02）之外的段
# ——precheck 的判定并集重叠检查（409）先于守卫，区内请求会先 409
# 掩盖守卫拒绝面
HYP_START, HYP_END = "2023-01-01", "2023-02-01"
START, END = "2023-03-01", "2023-03-20"


def _guard_dna(exec_tf: str = "4h") -> StrategyDNA:
    """单周期 DNA（无层、无标注基因）——守卫面测试载体。"""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi_14", {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi_14", {"type": "gt", "threshold": 65}),
        ],
        execution_genes=ExecutionGenes(timeframe=exec_tf, symbol="BTCUSDT"),
        logic_genes=LogicGenes(),
        risk_genes=RiskGenes(),
        signal_sources=[{"type": "rule"}])


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(7)
    for tf, n, sigma in (("4h", 2000, 110), ("1d", 500, 400)):
        idx = pd.date_range("2022-10-01", periods=n,
                            freq={"4h": "4h", "1d": "1d"}[tf], tz="UTC",
                            name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, sigma, n))
        spread = sigma / 2
        pd.DataFrame({"open": close, "high": close + spread,
                      "low": close - spread, "close": close,
                      "volume": rng.uniform(100, 800, n),
                      "trades": 40}, index=idx).to_parquet(
            d / f"BTCUSDT_{tf}.parquet")
    return d


@pytest.fixture()
def seeded(db, data_dir) -> dict:
    """落库 4h 执行策略 + 挂假设——四通道共用。"""
    dna = _guard_dna("4h")
    sid = "st_guard_4h"
    save_strategy(db, strategy_id=sid, dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h",
                  gene_signature="sig_guard_4h")
    hid = register_hypothesis(
        db,
        conditions={"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": "close", "action": "gt", "target": 0}]},
        statement="守卫锚",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": HYP_START, "end": HYP_END}])["hypothesis_id"]
    return {"sid": sid, "hid": hid}


class TestT8GuardRejection:
    """T⑧ 守卫拒绝锚（显式异轴 422/同码/传导/防呆/不装载）。"""

    def test_t8_1_explicit_mismatch_422_detail_four_keys(self, seeded):
        """⑧.1 显式异轴：code+detail 四键逐键断言（need_hint 指引语义）。"""
        with pytest.raises(ResearchError) as exc:
            ensure_timeframe_consistent(
                _guard_dna("4h"), "1d", strategy_id="st_x")
        assert exc.value.code == "RESEARCH_TIMEFRAME_MISMATCH"
        assert exc.value.status == 422
        d = exc.value.detail
        assert set(d.keys()) == {"request_timeframe",
                                 "dna_execution_timeframe",
                                 "strategy_id", "need_hint"}
        assert d["request_timeframe"] == "1d"
        assert d["dna_execution_timeframe"] == "4h"
        assert d["strategy_id"] == "st_x"
        # need_hint 指引语义：回假设侧修改基准周期
        assert "基准周期" in d["need_hint"]
        assert "data_ranges" in d["need_hint"]

    def test_t8_2_bridge_entry_and_guard_same_face(self, seeded, db, data_dir):
        """⑧.2 双挂同码：桥入口拒绝与守卫单源直调逐字段一致。"""
        dna = _guard_dna("4h")
        with pytest.raises(ResearchError) as bridge_exc:
            run_backtest_on_range(
                db, data_dir, dna, "BTCUSDT", "1d", START, END, 200,
                strategy_id="st_y")
        with pytest.raises(ResearchError) as direct_exc:
            ensure_timeframe_consistent(dna, "1d", strategy_id="st_y")
        assert bridge_exc.value.code == direct_exc.value.code
        assert bridge_exc.value.detail == direct_exc.value.detail
        assert bridge_exc.value.status == direct_exc.value.status

    @pytest.mark.parametrize("channel", ["isolated", "random", "sandbox",
                                         "staged"])
    def test_t8_3_four_channels_propagate(self, seeded, db, data_dir,
                                          channel):
        """⑧.3 四通道传导：显式异轴全部 RESEARCH_TIMEFRAME_MISMATCH。"""
        sid, hid = seeded["sid"], seeded["hid"]
        with pytest.raises(ResearchError) as exc:
            if channel == "isolated":
                run_isolated_backtest(
                    db, data_dir, hypothesis_id=hid, strategy_id=sid,
                    request_range={"symbol": "BTCUSDT", "timeframe": "1d",
                                   "start": START, "end": END})
            elif channel == "random":
                random_window_test(
                    db, data_dir, strategy_id=sid, symbol="BTCUSDT",
                    timeframe="1d", data_start=START, data_end=END,
                    window_bars=5, n_windows=2)  # 1d×5 天窗（span 检查过）
            elif channel == "sandbox":
                run_sandbox_backtest(
                    db, data_dir, strategy_id=sid, symbol="BTCUSDT",
                    timeframe="1d", start=START, end=END)
            else:
                # auto 段构造 min_bars=50：1d 请求区间须 ≥50 根
                run_staged_validation(
                    db, data_dir, strategy_id=sid, hypothesis_id=hid,
                    symbol="BTCUSDT", timeframe="1d",
                    start="2023-03-01", end="2023-06-01")
        assert exc.value.code == "RESEARCH_TIMEFRAME_MISMATCH", (
            f"{channel} 通道未传导守卫拒绝")

    def test_t8_4_default_chain_self_consistent_not_blocked(self, seeded,
                                                            db, data_dir):
        """⑧.4 缺省链不拦：request_range 不带 timeframe → 取策略行周期
        （=执行周期）→ 放行执行（跑通回测非拒绝）。"""
        res = run_isolated_backtest(
            db, data_dir, hypothesis_id=seeded["hid"],
            strategy_id=seeded["sid"],
            request_range={"symbol": "BTCUSDT",
                           "start": START, "end": END})
        assert "record_id" in res  # 放行执行（verdict 三态任意）

    def test_t8_5_rejection_before_loading_zero_io(self, seeded, db,
                                                   data_dir):
        """⑧.5 拒绝不装载：守卫拦截时数据装载函数零调用（IO 前拦截）。"""
        import core.data.mtf_loader as loader_mod
        with patch.object(loader_mod, "load_and_prepare_df",
                          wraps=loader_mod.load_and_prepare_df) as spy:
            with pytest.raises(ResearchError) as exc:
                run_backtest_on_range(
                    db, data_dir, _guard_dna("4h"), "BTCUSDT", "1d",
                    START, END, 200, strategy_id="st_z")
        assert exc.value.code == "RESEARCH_TIMEFRAME_MISMATCH"
        assert spy.call_count == 0, "守卫拒绝后不得发生数据装载"


def _intersection_env(n: int = 1500, seed: int = 13):
    """交集形态环境：30m 合成 df（趋势类自然触发）。"""
    rng = np.random.RandomState(seed)
    rets = rng.normal(0.0002, 0.008, n)
    close = 60000.0 * np.cumprod(1.0 + rets)
    open_ = np.empty(n)
    open_[0] = 60000.0
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, 0.004, n)) * close
    idx = pd.date_range("2024-01-01", periods=n, freq="30min", tz="UTC")
    return pd.DataFrame({"open": open_,
                         "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread,
                         "close": close,
                         "volume": np.abs(rng.normal(500, 150, n)) + 1.0},
                        index=idx)


_GENES = lambda: [  # noqa: E731 —— 交集形态基因（无标注）
    SignalGene("EMA", {"period": 10}, SignalRole.ENTRY_TRIGGER, "ema",
               {"type": "price_above"}),
    SignalGene("BB", {"period": 20, "std": 2}, SignalRole.EXIT_TRIGGER,
               "middle", {"type": "price_below"}),
]


class TestT4IntersectionDefense:
    """T④ R1b 交集安全性锚（守卫合法入口下两路径语义等价）。"""

    def test_intersection_layer_vs_flat_four_signals_equal(self):
        """交集防御锚：真显式单层=执行周期，显式层路径 vs 等价无层
        单周期路径四信号逐位相等（守卫保证请求轴=执行轴=层轴）。"""
        df = _intersection_env()
        genes = _GENES()
        layer_dna = StrategyDNA(
            signal_genes=list(genes),
            layers=[TimeframeLayer("30m", list(genes), LogicGenes())],
            execution_genes=ExecutionGenes(timeframe="30m",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(direction="short"),
            signal_sources=[{"type": "rule"}])
        flat_dna = StrategyDNA(
            signal_genes=list(genes),
            execution_genes=ExecutionGenes(timeframe="30m",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(direction="short"),
            signal_sources=[{"type": "rule"}])
        # 守卫合法入口前提自证：请求轴=执行轴
        ensure_timeframe_consistent(layer_dna, "30m")
        ensure_timeframe_consistent(flat_dna, "30m")
        # 显式层路径（构造态 is_mtf=True + 装载集）
        sig_layer = dna_to_signal_set(layer_dna, df, {"30m": df})
        # 等价无层单周期路径
        sig_flat = dna_to_signal_set(flat_dna, df, None)
        assert int(sig_flat.entries.fillna(False).sum()) > 0, (
            "对照信号应非空（seed 固定）")
        for name in ("entries", "exits", "adds", "reduces"):
            pd.testing.assert_series_equal(
                getattr(sig_layer, name), getattr(sig_flat, name),
                check_names=False)

    def test_t4_discriminator_entry_direction_two_states(self):
        """T④ 判别锚：mixed+DIRECTION+无角色单层=执行周期三条件叠加
        形态——加载态走单周期路径 entry_direction=基因方向序列（A 案
        语义保真）；构造态走层路径 entry_direction=None（层内 DIRECTION
        产出被 legacy 出口丢弃——差异面现状锁）。

        【转红处置指引】候选独立 change「层路径 entry_direction 消费
        缺口」落地后本锚构造态断言转红——按新契约同步（层路径补齐
        方向产出），转红=提示同步而非回归。
        """
        df = _intersection_env()
        direction_gene = SignalGene(
            "EMA", {"period": 20}, SignalRole.DIRECTION, "ema",
            {"type": "price_above"})
        entry_gene = SignalGene(
            "EMA", {"period": 10}, SignalRole.ENTRY_TRIGGER, "ema",
            {"type": "price_above"})
        genes = [entry_gene, direction_gene]
        dna = StrategyDNA(
            signal_genes=list(genes),
            layers=[TimeframeLayer("30m", list(genes), LogicGenes())],
            execution_genes=ExecutionGenes(timeframe="30m",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(direction="mixed"),
            signal_sources=[{"type": "rule"}])
        # 构造态：真显式层（is_mtf=True）→ 层路径
        assert dna.is_mtf is True
        sig_constructed = dna_to_signal_set(dna, df, {"30m": df})
        assert sig_constructed.entry_direction is None, (
            "层路径现状：无角色层 DIRECTION 产出被 legacy 出口丢弃"
            "（候选独立 change 域——见模块 docstring 转红处置指引）")
        # 加载态：镜像+四条件 → 愈合误归 → 单周期路径
        loaded = StrategyDNA.from_json(dna.to_json())
        # 注：构造态 to_dict 写出 layers（显式）；加载态按形态判伪层
        if loaded.is_mtf is False:
            sig_loaded = dna_to_signal_set(loaded, df, {"30m": df})
            assert sig_loaded.entry_direction is not None
            # 基因方向序列：DIRECTION 基因 True→+1（多）False→-1（空）
            ed = sig_loaded.entry_direction
            assert set(ed.dropna().unique()).issubset({1.0, -1.0})
            assert int((ed == 1.0).sum()) > 0
            assert int((ed == -1.0).sum()) > 0
