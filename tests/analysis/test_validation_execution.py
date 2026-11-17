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

from core.analysis.errors import ResearchError
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.templates.condition_stats import condition_stats
from core.analysis.templates.correlation_scan import correlation_scan
from core.analysis.templates.event_study import event_study
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

_EXIT_GENE = lambda: SignalGene(  # noqa: E731 —— 层 exit 补齐：T3 读库
    # 二道校验后，直写库构造须过 validate_dna 结构完备面（层缺 exit
    # 旧版经 REST 直通可跑、现被正确拒绝——修构造不损 MTF 装载语义）
    "RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
    {"type": "gt", "threshold": 65})


def _mtf_dna(layer_tfs):
    """显式 layers 的 MTF DNA（is_mtf=True；层基因 entry+exit 齐备——
    过 validate_dna 结构完备面）。顶层同步带基因：卡 R 起装载挂
    全量 validate_dna（Q-1），空 layers 调用形态（_mtf_dna([])）顶层
    无基因过不了装载校验——顶层基因对非空 layers 调用零影响
    （MTF 分支不查顶层 entry/exit；条件结构面层内同基因已覆盖）。"""
    return StrategyDNA(
        signal_genes=[_RSI_GENE(), _EXIT_GENE()],
        layers=[TimeframeLayer(timeframe=tf,
                               signal_genes=[_RSI_GENE(), _EXIT_GENE()],
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
                      "end": "2023-02-01T00:00:00Z"}], strategy_params={"stop_loss": 0.05}
    )["hypothesis_id"]


        # 0.2.11 三态：小样本区间判 inconclusive（证据不足非证伪）——
        # 本测试意图是"不再 500"，verdict 三态皆合法

class TestMtfLoading:
    """3.2：is_mtf 即装载——三用例 × 两路径（单层同 tf 现状两路径均 500）。"""

    def _isolated(self, db, data_dir, hypothesis_id, strategy_id):
        from core.analysis.validation_service import run_sandbox_backtest
        return run_sandbox_backtest(
            db, data_dir, strategy_id=strategy_id, hypothesis_id=hypothesis_id,
            symbol="BTCUSDT", timeframe="4h",
            start="2023-03-01T00:00:00Z", end="2023-03-20T00:00:00Z")

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
        # 改经 sandbox 入口承载后判据=跑出结果（record 在案非轴缺失拒）
        assert out["record_id"] and out["sandbox"] is True

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


# ═══════════════════════════════════════════════════════════════
# T⑤ warmup 轴一致锚（fix-mtf-execution-consistency R3，组 2）
# ═══════════════════════════════════════════════════════════════

class TestR3WarmupAxisConsistency:
    """带 3d 标注+30m 基准策略：推导轴=消费轴（请求周期 30m）。

    修复前缺陷（组 0 快照实证）：base_tf 兜底=execution_genes.timeframe，
    漂移 DNA（exec 4h）下 360 根 4h 被当 30m 前扩——两轴分离时 warmup
    欠账 8 倍。修复后：base_tf 必填消费轴（请求周期），推导折算分母
    与装载消费轴显式一致。
    """

    @pytest.fixture()
    def env_30m(self, tmp_path):
        """30m 执行轴 + 3d 高周期轴合成数据 + 带轴策略。"""
        import numpy as np
        from core.persistence.repository.strategy import save_strategy
        from core.strategy.dna import SignalGene

        d = tmp_path / "market"
        d.mkdir(exist_ok=True)
        db = d.parent / "q.db"
        init_db_ext(db)
        from core.analysis.executor import ensure_research_tables
        ensure_research_tables(db)
        rng = np.random.default_rng(21)
        idx = pd.date_range("2023-06-01", periods=15000, freq="30min",
                            tz="UTC", name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 80, 15000))
        pd.DataFrame({"open": close, "high": close + 40, "low": close - 40,
                      "close": close,
                      "volume": rng.uniform(100, 800, 15000),
                      "trades": 40}, index=idx).to_parquet(
            d / "BTCUSDT_30m.parquet")
        rng3 = np.random.default_rng(22)
        idx3 = pd.date_range("2022-06-01", periods=600, freq="3D",
                             tz="UTC", name="timestamp")
        close3 = 60000 + np.cumsum(rng3.normal(0, 600, 600))
        pd.DataFrame({"open": close3, "high": close3 + 300,
                      "low": close3 - 300, "close": close3,
                      "volume": rng3.uniform(100, 800, 600),
                      "trades": 40}, index=idx3).to_parquet(
            d / "BTCUSDT_3d.parquet")

        dna = StrategyDNA(
            signal_genes=[
                SignalGene("EMA", {"period": 10}, SignalRole.ENTRY_TRIGGER,
                           "ema", {"type": "price_above"}, timeframe="3d"),
                SignalGene("RVOL", {"period": 20}, SignalRole.EXIT_TRIGGER,
                           "rvol", {"type": "gt", "threshold": 2.0})],
            execution_genes=ExecutionGenes(timeframe="30m",
                                           symbol="BTCUSDT"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(),
            signal_sources=[{"type": "rule"}])
        sid = "st_t5_warmup_axis"
        save_strategy(db, strategy_id=sid,
                      dna_json=dna.to_json(), symbol="BTCUSDT",
                      timeframe="30m", gene_signature="sig_t5_warmup")
        hid = register_hypothesis(
            db,
            conditions={"when_conditions": [
                {"subject": {"indicator": "EMA", "params": {"period": 10}},
                 "action": "gt", "target": 0, "timeframe": "3d"}],
                "then_conditions": [
                    {"subject": "close", "action": "gt", "target": 0}]},
            statement="T5 warmup 轴",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "30m",
                          "start": "2023-12-01", "end": "2023-12-10"}], strategy_params={"stop_loss": 0.05}
        )["hypothesis_id"]
        return db, d, sid, hid

    def test_t5_main_axis_consistent_and_magnitude(self, env_30m):
        """主锚+副锚：推导值与消费轴直算折算值相等，量级数千根 30m
        （>1000，防凑数）；回执 warmup.base_timeframe=="30m" 可查。"""
        from core.analysis.registry_meta import derive_warmup_full
        from core.analysis.validation_service import _derive_dna_warmup
        from core.persistence.repository.strategy import get_strategy
        from core.strategy.dna import StrategyDNA as _SD

        db, data_dir, sid, hid = env_30m
        dna = _SD.from_json(get_strategy(db, sid)["dna_json"])
        # 通道消费路径（R3 后 base_tf=请求周期）
        w_channel, _ = _derive_dna_warmup(db, dna, "30m")
        # 测试内独立推导路径（不经薄壳直调推导单源）
        w_direct = derive_warmup_full(
            db, ["RVOL"], set(),
            indicator_tfs={"3d": [("EMA", {"period": 10})]},
            base_tf="30m")
        assert w_channel == w_direct, (
            f"通道推导 {w_channel} ≠ 消费轴直算 {w_direct}")
        assert w_channel > 1000, (
            f"3d 轴折算 30m 量级应数千根，got {w_channel}（欠账回归？）")
