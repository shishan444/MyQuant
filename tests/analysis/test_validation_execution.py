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
from core.persistence.repository.strategy import save_strategy
from core.persistence.db_ext import init_db_ext
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
            "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
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


class TestThresholdProfileWhitelist:
    """CARD-03（S-3 方向 B）：threshold_profile 按名白名单解析。

    全量 rules dict 透传等于调用方操纵判定门槛——只认
    {"name": "default"|"tightened"} 与 None；未知名/全量形制/混合
    注入/裸字符串一律 422。解析先于终态/幂等（结构校验前置）。
    """

    def test_解析_None透传(self) -> None:
        from core.analysis.validation_service import _resolve_threshold_profile
        assert _resolve_threshold_profile(None) is None

    def test_解析_default_返回缺省档(self) -> None:
        from core.analysis.thresholds import DEFAULT_PROFILE
        from core.analysis.validation_service import _resolve_threshold_profile
        assert _resolve_threshold_profile({"name": "default"}) is DEFAULT_PROFILE

    def test_解析_tightened_返回加严档(self) -> None:
        from core.analysis.validation_service import _resolve_threshold_profile
        prof = _resolve_threshold_profile({"name": "tightened"})
        assert prof["name"] == "default_tightened_x1.2"

    @pytest.mark.parametrize(
        "bad",
        [
            {"rules": {"min_trades": 0}},                      # 全量 rules 形制
            {"name": "default", "rules": {"min_trades": 0}},   # 名+rules 混合注入
            "default",                                          # 裸字符串
            {"name": "loose"},                                  # 未知名
            {"name": None},                                     # 名值 None
            {"name": True},                                     # 名值布尔
            {"name": ["default"]},                              # 名值列表
        ],
        ids=["全量rules", "名rules混合", "裸字符串", "未知名", "名值None",
             "名值True", "名值list"])
    def test_非法形制_422附合法名清单(self, bad) -> None:
        from core.analysis.validation_service import _resolve_threshold_profile
        with pytest.raises(ResearchError) as ei:
            _resolve_threshold_profile(bad)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["legal_names"] == ["default", "tightened"]
        assert ei.value.detail["param"] == "threshold_profile"

    def test_入口接线_校验先于假设不存在(self, db, data_dir) -> None:
        """结构校验前置锚：坏形制 + 不存在假设 → PARAM_INVALID 而非
        HYPOTHESIS_NOT_FOUND（解析在函数体最前）。"""
        bad = {"rules": {"min_trades": 0}}
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(
                db, data_dir, hypothesis_id="hyp_ghost",
                strategy_id="st_ghost",
                request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                               "start": "2023-03-01", "end": "2023-03-20"},
                threshold_profile=bad)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

        with pytest.raises(ResearchError) as ei:
            random_window_test(
                db, data_dir, strategy_id="st_ghost", symbol="BTCUSDT",
                timeframe="4h", data_start="2023-03-20",
                data_end="2023-05-01", threshold_profile=bad)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

    def test_缺省与default档_判定同结果(self, db, data_dir, hypothesis_id) -> None:
        """None（judge 侧落 DEFAULT_PROFILE）与显式 default 判定等价。"""
        save_strategy(db, strategy_id="st_tp_a", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        save_strategy(db, strategy_id="st_tp_b", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      gene_signature="sig_tp_b")
        kwargs = dict(
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01", "end": "2023-03-20"})
        out_none = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id="st_tp_a", **kwargs)
        out_default = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id="st_tp_b", threshold_profile={"name": "default"},
            **kwargs)
        assert out_default["verdict"] == out_none["verdict"]
        assert out_default["metrics"] == out_none["metrics"]

    def test_tightened档_落档名带乘数(self, db, data_dir, hypothesis_id) -> None:
        save_strategy(db, strategy_id="st_tp_t", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id, strategy_id="st_tp_t",
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01", "end": "2023-03-20"},
            threshold_profile={"name": "tightened"})
        rec = next(r for r in rr.list_validation_records(
            db, hypothesis_id=hypothesis_id)
            if r["record_id"] == out["record_id"])
        assert json.loads(rec["params"])["threshold_profile"] \
            == "default_tightened_x1.2"


class TestPrecheckSharedByBothEntries:
    """CARD-09 防漂移锚：验证族前置四步（终态/幂等/并集/扫描复用）
    提取为 _hypothesis_precheck 后，isolated 与 staged 两入口必须
    共用同一实现——入口再内联前置块即红（对齐 to_utc/safe_symbol/
    stat_window_mask 收敛锚形制）。四步行为面由既有锚覆盖（终态/
    幂等/并集/screen 复用/staged 双路径）。"""

    def test_两入口均调用共享precheck(self) -> None:
        import ast
        import core.analysis.validation_service as vs

        tree = ast.parse(Path(vs.__file__).read_text(encoding="utf-8"))
        callers = {
            n.name: any(
                isinstance(x, ast.Call)
                and isinstance(x.func, ast.Name)
                and x.func.id == "_hypothesis_precheck"
                for x in ast.walk(n))
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and n.name in ("run_isolated_backtest", "run_staged_validation")}
        assert callers == {"run_isolated_backtest": True,
                           "run_staged_validation": True}, (
            "验证入口未共用 _hypothesis_precheck——前置块内联回潮")


class TestGapClosureValidation:
    """阶段四盲区消解：random_window 边界与 warmup 推导转译。"""

    def test_区间不容窗_DATA_INSUFFICIENT(self, db, data_dir):
        save_strategy(db, strategy_id="st_rw_short", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        with pytest.raises(ResearchError) as ei:
            random_window_test(
                db, data_dir, strategy_id="st_rw_short", symbol="BTCUSDT",
                timeframe="4h", data_start="2023-03-20",
                data_end="2023-04-01",  # 12 天 4h ≈ 72 bar，窗 200 容不下
                window_bars=200, n_windows=3, seed=5)
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"

    def test_同参数二次请求_幂等命中(self, db, data_dir):
        save_strategy(db, strategy_id="st_rw_idem",
                      dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      gene_signature="sig_rw_idem")
        kw = dict(strategy_id="st_rw_idem", symbol="BTCUSDT",
                  timeframe="4h", data_start="2023-03-20",
                  data_end="2023-05-01", window_bars=30, n_windows=3, seed=5)
        first = random_window_test(db, data_dir, **kw)
        replay = random_window_test(db, data_dir, **kw)
        assert replay["duplicated"] is True
        assert replay["record_id"] == first["record_id"]

    @pytest.mark.parametrize("runner", ["isolated", "staged"], ids=["隔离", "分段"])
    def test_warmup推导KeyError_转译INVALID(self, db, data_dir, hypothesis_id,
                                             runner):
        """DNA 带注册表外指标 → _derive_dna_warmup KeyError → 422
        （两入口同转译）。"""
        bad_gene = SignalGene("GHOST_IND", {"period": 14},
                              SignalRole.ENTRY_TRIGGER, "ghost_14",
                              {"type": "lt", "threshold": 35})
        bad_dna = StrategyDNA(
            signal_genes=[bad_gene], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        sid = f"st_bad_{runner}"
        save_strategy(db, strategy_id=sid, dna_json=bad_dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      gene_signature=f"sig_bad_{runner}")
        from core.analysis.validation_service import run_staged_validation
        with pytest.raises(ResearchError) as ei:
            if runner == "isolated":
                run_isolated_backtest(
                    db, data_dir, hypothesis_id=hypothesis_id,
                    strategy_id=sid,
                    request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                                   "start": "2023-03-01", "end": "2023-03-20"})
            else:
                run_staged_validation(
                    db, data_dir, strategy_id=sid, symbol="BTCUSDT",
                    timeframe="4h", start="2023-03-01", end="2023-03-20")
        assert ei.value.code == "RESEARCH_CONDITION_INVALID"

    def test_random_window_top_level_verdict_both_paths(self, db, data_dir):
        """0.3.3 G6：随机窗顶层 verdict 双路径同形同值——首次请求与
        幂等重放（duplicated=True）顶层 verdict 均在场且相等（提取源：
        首次=在场 agg、重放=记录 verdict 列——两路径一致性锚）。"""
        save_strategy(db, strategy_id="st_g6", dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        kwargs = dict(
            strategy_id="st_g6", symbol="BTCUSDT", timeframe="4h",
            data_start="2023-03-20", data_end="2023-05-01",
            window_bars=30, n_windows=3, seed=5)
        first = random_window_test(db, data_dir, **kwargs)
        replay = random_window_test(db, data_dir, **kwargs)

        assert first.get("duplicated") is not True  # 正常路径无此键
        assert replay["duplicated"] is True
        # 双路径顶层 verdict 在场且同值
        assert first["verdict"] in {"pass", "fail", "inconclusive"}
        assert replay["verdict"] == first["verdict"]
        # 与既有 summary 聚合一致（worst_window 兼容保留）
        assert "worst_window" in first["summary"]

    def test_random_window_same_seed_computational_reproducibility(
            self, tmp_path, data_dir):
        """阶段四盲区消解：同种子计算级可复现（spec 场景「同种子可
        复现」）——两个独立 db 各自首跑（不经幂等通道），同种子同参数
        → 同窗口集逐窗一致（start/end/metrics/verdict）与同 summary。
        幂等锚只证 record 级复现，此处证计算级。"""
        dbs = []
        for i in range(2):
            d = tmp_path / f"q{i}.db"
            init_db_ext(d)
            from core.analysis.executor import ensure_research_tables
            ensure_research_tables(d)
            save_strategy(d, strategy_id="st_repro",
                          dna_json=_mtf_dna([]).to_json(),
                          symbol="BTCUSDT", timeframe="4h")
            dbs.append(d)
        kwargs = dict(strategy_id="st_repro", symbol="BTCUSDT",
                      timeframe="4h", data_start="2023-03-20",
                      data_end="2023-05-01", window_bars=30,
                      n_windows=4, seed=13)
        run_a = random_window_test(dbs[0], data_dir, **kwargs)
        run_b = random_window_test(dbs[1], data_dir, **kwargs)
        assert run_a.get("duplicated") is not True
        assert run_b.get("duplicated") is not True  # 两库各自首跑
        keys = ("start", "end", "metrics", "verdict")
        assert [{k: w[k] for k in keys} for w in run_a["windows"]] == [
            {k: w[k] for k in keys} for w in run_b["windows"]]
        assert run_a["verdict"] == run_b["verdict"]
        assert run_a["summary"]["returns_median"] == pytest.approx(
            run_b["summary"]["returns_median"])

    def test_random_window_verdict_matches_worst_case_aggregation(self,
                                                                  db, data_dir):
        """特征网：顶层 verdict 提取源同源性——等于窗级 verdict 列表
        经 _aggregate_verdicts（最差聚合三态唯一实现）的聚合值；禁作
        提取源的 worst_window.verdict（收益最差窗的窗口判定，语义
        不同于三态聚合）单列断言以防提取源漂移。"""
        from core.analysis.thresholds import _aggregate_verdicts
        save_strategy(db, strategy_id="st_g6b",
                      dna_json=_mtf_dna([]).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = random_window_test(
            db, data_dir, strategy_id="st_g6b", symbol="BTCUSDT",
            timeframe="4h", data_start="2023-03-20",
            data_end="2023-05-01", window_bars=30, n_windows=4, seed=9)
        window_verdicts = [w["verdict"] for w in out["windows"]]
        expected = _aggregate_verdicts(window_verdicts)
        assert out["verdict"] == expected
        # 落库 verdict 列与顶层同值（程序化判定链单点取值的持久化面）
        recs = [r for r in rr.list_validation_records(
            db, strategy_id="st_g6b") if r["record_id"] == out["record_id"]]
        assert len(recs) == 1
        assert recs[0]["verdict"] == out["verdict"]


class TestGeneTfFourValidationEntries:
    """T4b/T5c（tester 补——unblock-research-validation spec「验证链
    装载 MUST 覆盖基因级 timeframe」）：四验证入口（隔离/随机窗口/
    分段/沙盒）× 基因级 tf 策略跑通（装载判据三层并集生效——若
    装载桥未扩展，入口将以轴缺失 fail-loud 拒，用例天然鉴别）；
    T5c 探针锚：高周期轴装载与基准轴同窗前扩（时长等价传递）。"""

    @pytest.fixture()
    def gene_tf_strategy(self, db) -> str:
        """entry RSI(14)@1d + exit RSI@4h 基准的策略（tf=基因级）。"""
        gene_entry = SignalGene(
            "RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
            {"type": "lt", "threshold": 35})
        gene_entry.timeframe = "1d"
        gene_exit = SignalGene(
            "RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
            {"type": "gt", "threshold": 70})
        dna = StrategyDNA(
            signal_genes=[gene_entry, gene_exit],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        save_strategy(db, strategy_id="st_gene_tf", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        return "st_gene_tf"

    def test_t4b_isolated_backtest_runs(self, db, data_dir, hypothesis_id,
                                        gene_tf_strategy):
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=gene_tf_strategy,
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-06-01", "end": "2023-07-01"})
        assert out["verdict"] in ("pass", "fail", "inconclusive"), \
            "隔离验证跑出三态判定（非轴缺失拒绝）"

    def test_t4b_random_window_runs(self, db, data_dir, gene_tf_strategy):
        """随机窗口主线：窗长 240 bar（40 天）——1d 轴窗内收盘数+
        前扩 ≥ load_mtf_data min_bars=50，装载跑通。"""
        from core.analysis.validation_service import random_window_test
        out = random_window_test(
            db, data_dir, strategy_id=gene_tf_strategy, symbol="BTCUSDT",
            timeframe="4h", data_start="2023-05-01",
            data_end="2023-08-15", window_bars=240, n_windows=2, seed=5)
        assert len(out["windows"]) == 2, "随机窗口全部跑出（非轴缺失拒）"

    def test_t4b_random_window_short_window_preflight_rejected(
            self, db, data_dir, gene_tf_strategy):
        """短窗×基因级高周期契约锚（DEV-5 裁决 B 落地 247aa74：
        统计无意义窗入口显式拒绝——30 bar@4h 窗内 1d 采样 5 bar+
        轴收敛 42 bar < min_bars=50，detail 附可行动六键）。"""
        from core.analysis.validation_service import random_window_test
        with pytest.raises(ResearchError) as ei:
            random_window_test(
                db, data_dir, strategy_id=gene_tf_strategy, symbol="BTCUSDT",
                timeframe="4h", data_start="2023-06-01",
                data_end="2023-08-01", window_bars=30, n_windows=1, seed=5)
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"
        detail = ei.value.detail
        assert detail["gene_tf"] == "1d"
        assert detail["in_window_bars"] == 5, "窗内 1d 采样 5 bar（30/6）"
        assert detail["warmup_bars"] == 42, "轴收敛 42 bar（14×3 收敛粒度）"
        assert detail["min_bars"] == 50
        assert "need_hint" in detail, "附建议窗长（可行动形制）"

    def test_t4b_staged_validation_runs(self, db, data_dir, gene_tf_strategy):
        """分段主线：显式两段各 45 天（段窗+42 天前扩的 1d 轴窗
        87 根 > min_bars=50）。auto 分段按 regime 切段、短段（如
        30 天内）在基因级 1d 下与随机短窗同根因——1d 轴装载窗不足
        min_bars 被跳过→基因级 fail-loud（现状记录锚见
        test_t4b_random_window_short_window_rejected_current，能力
        边界已上报裁决）。"""
        from core.analysis.validation_service import run_staged_validation
        out = run_staged_validation(
            db, data_dir, strategy_id=gene_tf_strategy, symbol="BTCUSDT",
            timeframe="4h", start="2023-05-01", end="2023-08-01",
            segmentation="explicit",
            segments=[
                {"label": "seg_a", "start": "2023-05-01",
                 "end": "2023-06-15"},
                {"label": "seg_b", "start": "2023-06-15",
                 "end": "2023-08-01"}])
        assert out.get("segments") or out.get("windows") or out.get(
            "record_id"), "分段验证产出成绩单（非轴缺失拒）"

    def test_t4b_sandbox_runs_and_t5c_same_window_prefix(
            self, db, data_dir, gene_tf_strategy, monkeypatch):
        """沙盒跑通 + T5c 探针：spy load_mtf_data 捕获装载参数——
        needed_tfs 含基因级 1d，且高周期轴 data_start 与基准轴装载
        前扩起点同窗（时长等价传递，无二次折算）。"""
        from core.analysis.validation_service import run_sandbox_backtest
        from core.data import mtf_loader
        captured: dict = {}
        _orig_mtf = mtf_loader.load_mtf_data
        _orig_base = mtf_loader.load_and_prepare_df

        def spy_mtf(data_dir_, symbol_, exec_tf, enhanced, needed_tfs,
                    data_start=None, data_end=None, **kw):
            captured["hi"] = {"needed": set(needed_tfs),
                              "data_start": data_start}
            return _orig_mtf(data_dir_, symbol_, exec_tf, enhanced,
                             needed_tfs, data_start=data_start,
                             data_end=data_end, **kw)

        def spy_base(data_dir_, symbol_, tf, data_start=None, **kw):
            captured["base"] = data_start
            return _orig_base(data_dir_, symbol_, tf,
                              data_start=data_start, **kw)

        monkeypatch.setattr(mtf_loader, "load_mtf_data", spy_mtf)
        monkeypatch.setattr(mtf_loader, "load_and_prepare_df", spy_base)
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=gene_tf_strategy, symbol="BTCUSDT",
            timeframe="4h", start="2023-06-01", end="2023-07-01")
        assert out.get("metrics") is not None or out.get("result") is not \
            None or len(out) > 0, "沙盒跑出结果"
        assert "1d" in captured["hi"]["needed"], \
            "装载集含基因级 timeframe（三层并集）"
        assert captured["hi"]["data_start"] == captured["base"], (
            f"高周期轴前扩起点 {captured['hi']['data_start']} 应与基准轴"
            f" {captured['base']} 同窗（时长等价传递）")


class TestExplicitSegmentShortWindowPreflight:
    """短窗预检边界（tester 补——247aa74 设计明文：explicit 段不在
    预检面，段内采样充分性调用方自担；auto 段/随机窗口才预检拒。
    本锚披露两分面：explicit 短段不预检拒（自担语义），装载可行时
    正常出成绩单）。"""

    def test_explicit_short_segment_not_preflighted(
            self, db, data_dir, hypothesis_id):
        gene_entry = SignalGene(
            "RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
            {"type": "lt", "threshold": 35})
        gene_entry.timeframe = "1d"
        gene_exit = SignalGene(
            "RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
            {"type": "gt", "threshold": 70})
        dna = StrategyDNA(
            signal_genes=[gene_entry, gene_exit],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        save_strategy(db, strategy_id="st_exp_short", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        from core.analysis.validation_service import run_staged_validation
        # 15 天段（窗内 1d 采样约 2 bar<warmup 42——统计意义弱）：
        # explicit 自担语义=不预检拒；段+warmup 前扩装载窗 56 根 1d
        # ≥ min_bars → 装载可行 → 正常出成绩单
        out = run_staged_validation(
            db, data_dir, strategy_id="st_exp_short", symbol="BTCUSDT",
            timeframe="4h", start="2023-05-01", end="2023-05-15",
            segmentation="explicit",
            segments=[{"label": "s", "start": "2023-05-01",
                       "end": "2023-05-15"}])
        assert out.get("segments") or out.get("record_id"), \
            "explicit 段不预检（自担语义），装载可行即出成绩单"
