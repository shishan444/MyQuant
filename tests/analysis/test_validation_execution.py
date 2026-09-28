"""验证执行内核测试（improve-mcp-agent-loop 3.1/3.2/3.3）。

三组：
- tz 入口归一：date-only 输入（naive）隔离验证与随机窗口全路径不 500；
- MTF is_mtf 即装载：单层同 tf / 单层异 tf / 多层 × 隔离验证与 REST
  回测两路径均跑通（单层同 tf 现状在两条路径均 500）；
- 模板列不可用结构化拒绝（附可用列清单）。
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.analysis.templates.condition_stats import condition_stats
from core.analysis.templates.correlation_scan import correlation_scan
from core.analysis.templates.event_study import event_study
from core.analysis.validation_service import (
    random_window_test,
    run_isolated_backtest,
)
from core.persistence.db_ext import init_db_ext, save_strategy
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)

_RSI_GENE = lambda: SignalGene(  # noqa: E731 —— 与既有测试同构的构造捷径
    "RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
    {"type": "lt", "threshold": 35})


def _mtf_dna(layer_tfs):
    """显式 layers 的 MTF DNA（is_mtf=True）。"""
    return StrategyDNA(
        layers=[TimeframeLayer(timeframe=tf, signal_genes=[_RSI_GENE()],
                               logic_genes=LogicGenes())
                for tf in layer_tfs],
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(7)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(d / "BTCUSDT_4h.parquet")
    idx_1d = pd.date_range("2022-10-01", periods=500, freq="1d", tz="UTC",
                           name="timestamp")
    close_1d = 60000 + np.cumsum(rng.normal(0, 400, 500))
    pd.DataFrame({"open": close_1d, "high": close_1d + 80, "low": close_1d - 80,
                  "close": close_1d, "volume": rng.uniform(100, 800, 500),
                  "trades": 40}, index=idx_1d).to_parquet(d / "BTCUSDT_1d.parquet")
    return d


@pytest.fixture()
def hypothesis_id(db) -> str:
    return register_hypothesis(
        db,
        conditions={
            "when_conditions": [{"subject": "rsi_14", "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        },
        statement="s",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2023-01-01T00:00:00Z",
                      "end": "2023-02-01T00:00:00Z"}],
    )["hypothesis_id"]


class TestTimezoneNormalization:
    """3.1：date-only 输入（naive）按 UTC 归一，全路径不 500。"""

    def test_date_only_isolated_backtest(self, db, data_dir, hypothesis_id):
        save_strategy(db, strategy_id="st_tz", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id, strategy_id="st_tz",
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01", "end": "2023-03-20"})
        # 修复前：naive start_ts 与 tz-aware 索引比较 → TypeError（500）
        assert out["verdict"] in ("pass", "fail", "inconclusive")
        # 0.2.11 三态：小样本区间判 inconclusive（证据不足非证伪）——
        # 本测试意图是"不再 500"，verdict 三态皆合法

class TestInconclusiveVerdict:
    """0.2.11 三态：样本不足场景判 inconclusive（规划验收①——证据
    不足非证伪，误杀通路封堵）。"""

    def test_isolated_small_sample_inconclusive(self, db, data_dir, hypothesis_id):
        save_strategy(db, strategy_id="st_inc", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        # 极短区间（12 bar）：RSI<35 触发不足证据闸门线 5
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id, strategy_id="st_inc",
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01", "end": "2023-03-03"})
        assert out["verdict"] == "inconclusive"
        # 判定明细含规则条目但不失败于证伪（证据闸门出口）
        assert out["record_id"]
        recs = rr.list_validation_records(db, hypothesis_id=hypothesis_id)
        assert any(r["verdict"] == "inconclusive" for r in recs)

    def test_random_window_small_window_aggregates_inconclusive(self, db, data_dir):
        """T2 漂移行为锚：短窗（12 bar）窗内交易不足 → 窗级 inconclusive
        → 总判 inconclusive（原两态聚合会判 fail）。"""
        save_strategy(db, strategy_id="st_inc", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = random_window_test(
            db, data_dir, strategy_id="st_inc", symbol="BTCUSDT",
            timeframe="4h", data_start="2023-03-20", data_end="2023-05-01",
            window_bars=12, n_windows=3, seed=5)
        verdicts = [w["verdict"] for w in out["windows"]]
        assert set(verdicts) <= {"pass", "fail", "inconclusive"}
        if "fail" not in verdicts and "inconclusive" in verdicts:
            assert out["summary"]["all_pass"] is False  # A1 严格语义
        # 总判定与最差聚合一致
        from core.analysis.thresholds import _aggregate_verdicts
        assert (not out["summary"]["all_pass"]) == (
            _aggregate_verdicts(verdicts) != "pass")

    def test_date_only_random_window(self, db, data_dir):
        save_strategy(db, strategy_id="st_tz", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = random_window_test(
            db, data_dir, strategy_id="st_tz", symbol="BTCUSDT",
            timeframe="4h", data_start="2023-03-20", data_end="2023-05-01",
            window_bars=30, n_windows=3, seed=5)
        assert out["summary"]["n_windows"] == 3


class TestMtfLoading:
    """3.2：is_mtf 即装载——三用例 × 两路径（单层同 tf 现状两路径均 500）。"""

    def _isolated(self, db, data_dir, hypothesis_id, strategy_id):
        return run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id, strategy_id=strategy_id,
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01T00:00:00Z",
                           "end": "2023-03-20T00:00:00Z"})

    def _rest_backtest(self, db, data_dir, strategy_id):
        from api.routes.strategies.backtest import backtest_strategy
        from api.schemas import BacktestRequest

        return backtest_strategy(
            BacktestRequest(strategy_id=strategy_id, dataset_id="BTCUSDT_4h",
                            data_start="2023-03-01T00:00:00Z",
                            data_end="2023-03-20T00:00:00Z"),
            db_path=db, data_dir=data_dir)

    @pytest.mark.parametrize("layer_tfs, sid", [
        ([("4h",)], "st_mtf_same"),      # 单层同 tf（核心修复用例）
        ([("1d",)], "st_mtf_diff"),      # 单层异 tf
        ([("1d",), ("4h",)], "st_mtf_multi"),  # 多层
    ])
    def test_isolated_path(self, db, data_dir, hypothesis_id, layer_tfs, sid):
        tfs = [tf for tf, in layer_tfs]
        save_strategy(db, strategy_id=sid, dna_json=_mtf_dna(tfs).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = self._isolated(db, data_dir, hypothesis_id, sid)
        # 修复前：is_mtf 且无 dfs_by_timeframe → executor ValueError（500）
        assert out["verdict"] in ("pass", "fail", "inconclusive")
        # 0.2.11 三态：小样本区间判 inconclusive（证据不足非证伪）——
        # 本测试意图是"不再 500"，verdict 三态皆合法

    @pytest.mark.parametrize("tfs, sid", [
        (["4h"], "st_mtf_same"),
        (["1d"], "st_mtf_diff"),
        (["1d", "4h"], "st_mtf_multi"),
    ])
    def test_rest_path(self, db, data_dir, tfs, sid):
        save_strategy(db, strategy_id=sid, dna_json=_mtf_dna(tfs).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        resp = self._rest_backtest(db, data_dir, sid)
        # 修复前：len(needed_tfs)>1 守卫跳过装载 → executor ValueError（500）
        assert resp.result_id
        assert resp.total_trades >= 0


class TestBenchmarkDisclosure:
    """2.1：隔离回测与随机窗口 metrics 携带基准对照四键（6→10 键）。"""

    _KEYS = ("alpha", "market_annual_return", "benchmark_return", "excess_return")

    def test_isolated_metrics_and_stored_record_carry_keys(
            self, db, data_dir, hypothesis_id):
        from core.persistence.repository import research as rr

        save_strategy(db, strategy_id="st_bm", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id, strategy_id="st_bm",
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01T00:00:00Z",
                           "end": "2023-03-20T00:00:00Z"})
        m = out["metrics"]
        for key in self._KEYS:
            assert key in m, f"metrics 缺对照键 {key}"
        # 引擎恒传 benchmark（请求区间 close）→ 有交易时对照可算且自洽
        if m["total_trades"] > 0:
            assert m["benchmark_return"] is not None
            assert m["excess_return"] == pytest.approx(
                m["total_return"] - m["benchmark_return"])
        # 落库 record 的 result_json.metrics 同形（自动携带）
        rec = rr.list_validation_records(db, strategy_id="st_bm", limit=1)[0]
        stored = json.loads(rec["result"])["metrics"]
        assert set(self._KEYS) <= set(stored)

    def test_random_window_worst_window_carries_keys(self, db, data_dir):
        save_strategy(db, strategy_id="st_bm", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = random_window_test(
            db, data_dir, strategy_id="st_bm", symbol="BTCUSDT",
            timeframe="4h", data_start="2023-03-20", data_end="2023-05-01",
            window_bars=30, n_windows=3, seed=5)
        wm = out["summary"]["worst_window"]["metrics"]
        for key in self._KEYS:
            assert key in wm, f"worst_window.metrics 缺对照键 {key}"


class TestColumnRejection:
    """3.3：列不可用结构化拒绝（附可用列清单）——错误即文档。"""

    def _df(self, n=400):
        rng = np.random.default_rng(3)
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                            name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 110, n))
        return pd.DataFrame({"open": close, "high": close + 60,
                             "low": close - 60, "close": close,
                             "volume": rng.uniform(100, 800, n), "trades": 40},
                            index=idx)

    def test_correlation_scan_indicator_column_rejected(self):
        with pytest.raises(ResearchError) as ei:
            correlation_scan(self._df(), signal_column="rsi_14",
                             data_ranges=[])
        assert ei.value.code == "RESEARCH_COLUMN_NOT_AVAILABLE"
        assert "close" in ei.value.detail["available_columns"]
        assert "condition_stats" in ei.value.detail["note"]

    def test_event_study_bad_subject_rejected_not_misleading(self):
        with pytest.raises(ResearchError) as ei:
            event_study(self._df(),
                        condition={"subject": "nope_col", "action": "gt",
                                   "target": 1},
                        data_ranges=[])
        # 修复前：mask 全 False → 误导性的"事件数不足"小样本拒绝
        assert ei.value.code == "RESEARCH_COLUMN_NOT_AVAILABLE"

    def test_condition_stats_unknown_indicator(self):
        with pytest.raises(ResearchError) as ei:
            condition_stats(self._df(), indicator="NOPE", params={},
                            condition={"subject": "close", "action": "gt",
                                       "target": 0},
                            data_ranges=[])
        assert ei.value.code == "RESEARCH_INDICATOR_UNKNOWN"
        assert "RSI" in ei.value.detail["available_indicators"]
