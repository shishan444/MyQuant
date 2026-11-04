"""vr-signal-trace 三入口集成验收（add-vr-signal-trace 任务 6.2/6.3
——T1-I1~I8/T1-S3 锚族）。

验收锚逐条：三类型留痕在场+三布尔=1（漏接即红）；幂等重放零重复
留痕行；precheck 并集拦截 409 行为不变；exec_ref 与份清单一致（错挂
红线）；吞与拒不混（reason 两族分立）；零信号记录三布尔=1 且 0 行；
trail_series 份 metrics 在场；mtf_gated_bars null/计数两态；detail
端点新键（三布尔直译/trace_rows/trail_series）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.validation_service import (
    random_window_test,
    run_isolated_backtest,
    run_staged_validation,
)
from core.analysis.hypothesis_service import register_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)


def _gene(role, threshold=45):
    return SignalGene("RSI", {"period": 14}, role, "rsi_14",
                      {"type": "lt", "threshold": threshold})


def _dna(**risk_kw) -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[_gene(SignalRole.ENTRY_TRIGGER),
                      SignalGene("RSI", {"period": 14},
                                 SignalRole.EXIT_TRIGGER, "rsi_14",
                                 {"type": "gt", "threshold": 65})],
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(**risk_kw),
    )


def _mtf_dna() -> StrategyDNA:
    layers = [
        TimeframeLayer(timeframe="1d",
                       signal_genes=[_gene(SignalRole.ENTRY_TRIGGER),
                                     SignalGene("RSI", {"period": 14},
                                                SignalRole.EXIT_TRIGGER,
                                                "rsi_14",
                                                {"type": "gt", "threshold": 65})],
                       logic_genes=LogicGenes(), role="trend"),
        TimeframeLayer(timeframe="4h",
                       signal_genes=[_gene(SignalRole.ENTRY_TRIGGER),
                                     SignalGene("RSI", {"period": 14},
                                                SignalRole.EXIT_TRIGGER,
                                                "rsi_14",
                                                {"type": "gt", "threshold": 65})],
                       logic_genes=LogicGenes()),
    ]
    return StrategyDNA(
        layers=layers, execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(), mtf_mode="direction+confluence",
        confluence_threshold=0.3)


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
    # 1d 数据（MTF 门控样本——趋势层轴）
    idx_1d = pd.date_range("2022-10-01", periods=340, freq="1d", tz="UTC",
                           name="timestamp")
    close_1d = 60000 + np.cumsum(rng.normal(30, 300, 340))
    pd.DataFrame({"open": close_1d, "high": close_1d + 150,
                  "low": close_1d - 150, "close": close_1d,
                  "volume": rng.uniform(500, 2000, 340),
                  "trades": 400}, index=idx_1d).to_parquet(
        d / "BTCUSDT_1d.parquet")
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
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]


@pytest.fixture()
def strategy_saved(db) -> Path:
    save_strategy(db, strategy_id="st_trace",
                  dna_json=_dna().to_json(), symbol="BTCUSDT",
                  timeframe="4h")
    return "st_trace"


def _flags(db: Path, record_id: str):
    from core.persistence.orm.engine import engine_for
    from sqlalchemy import text
    with engine_for(db).connect() as conn:
        row = conn.execute(text(
            "SELECT signal_trace, trade_semantics, stop_price FROM"
            " validation_records WHERE record_id=:r"),
            {"r": record_id}).mappings().first()
    return (row["signal_trace"], row["trade_semantics"],
            row["stop_price"]) if row else None


def _range(start, end):
    return {"symbol": "BTCUSDT", "timeframe": "4h",
            "start": start, "end": end}


class TestThreeEntryPointsTrace:
    """T1-I1：三类型留痕在场+三布尔=1（漏接任一入口即红）。"""

    def test_isolated(self, db, data_dir, hypothesis_id, strategy_saved):
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved,
            request_range=_range("2023-03-01T00:00:00Z",
                                 "2023-03-20T00:00:00Z"))
        rid = out["record_id"]
        assert _flags(db, rid) == (1, 1, 1), "三布尔未打标=入口漏接红线"
        rows = rr.get_vr_signal_trace_rows(db, rid)
        assert rows, "isolated 留痕行缺失"
        assert all(r["exec_ref"] == "main" for r in rows)
        # 吞与拒不混：outcome 三值域+reason 两族分立
        for r in rows:
            assert r["outcome"] in ("filled", "rejected", "swallowed")
            if r["outcome"] == "swallowed":
                assert r["reason"] in ("entry_exit_conflict",
                                       "add_inhibited")
            elif r["outcome"] == "rejected" and r["reason"]:
                assert r["reason"] in ("exposure_cap",
                                       "insufficient_funds",
                                       "risk_budget_fallback")

    def test_random_window(self, db, data_dir, hypothesis_id,
                           strategy_saved):
        out = random_window_test(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved, symbol="BTCUSDT", timeframe="4h",
            data_start="2023-01-01T00:00:00Z",
            data_end="2023-05-01T00:00:00Z",
            window_bars=120, n_windows=2, seed=11)
        rid = out["record_id"]
        assert _flags(db, rid) == (1, 1, 1)
        rows = rr.get_vr_signal_trace_rows(db, rid)
        assert rows, "random 留痕行缺失"
        refs = {r["exec_ref"] for r in rows}
        assert refs <= {f"win_{i}" for i in range(2)}, (
            f"exec_ref 错挂: {refs}")

    def test_staged(self, db, data_dir, hypothesis_id, strategy_saved):
        out = run_staged_validation(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved, symbol="BTCUSDT", timeframe="4h",
            start="2023-03-01T00:00:00Z", end="2023-04-20T00:00:00Z")
        rid = out["record_id"]
        assert _flags(db, rid) == (1, 1, 1)
        rows = rr.get_vr_signal_trace_rows(db, rid)
        assert rows, "staged 留痕行缺失"
        # exec_ref 错挂红线：staged 份 label 与段清单逐份一致
        result = json.loads(rr.get_validation_record_by_id(
            db, rid)["result"])
        seg_labels = {s["label"] for s in result["segments"]}
        trace_refs = {r["exec_ref"] for r in rows}
        assert trace_refs <= seg_labels, (
            f"留痕份标识 {trace_refs - seg_labels} 不在段清单——错挂红线")

    def test_zero_signal_still_flagged(self, db, data_dir, hypothesis_id):
        """零信号记录：管线通而事实空（三布尔=1 且 0 行，非降级）。"""
        zero_dna = StrategyDNA(
            signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 5}),
                SignalGene("RSI", {"period": 14},
                           SignalRole.EXIT_TRIGGER, "rsi_14",
                           {"type": "gt", "threshold": 65})],
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        save_strategy(db, strategy_id="st_zero2",
                      dna_json=zero_dna.to_json(), symbol="BTCUSDT",
                      timeframe="4h")
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id="st_zero2",
            request_range=_range("2023-03-01T00:00:00Z",
                                 "2023-03-20T00:00:00Z"))
        rid = out["record_id"]
        assert _flags(db, rid) == (1, 1, 1), "零信号也须打标（管线通）"
        assert rr.get_vr_signal_trace_rows(db, rid) == []


class TestIdempotentAndPrecheck:
    """T1-I8 幂等锚+T1-S3 precheck 409 不变锚。"""

    def test_replay_no_duplicate_rows(self, db, data_dir, hypothesis_id,
                                      strategy_saved):
        req = _range("2023-03-01T00:00:00Z", "2023-03-20T00:00:00Z")
        first = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved, request_range=req)
        n1 = len(rr.get_vr_signal_trace_rows(db, first["record_id"]))
        second = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved, request_range=req)
        assert second["duplicated"] is True
        assert second["record_id"] == first["record_id"]
        n2 = len(rr.get_vr_signal_trace_rows(db, first["record_id"]))
        assert n1 == n2, "幂等重放不得产生重复留痕行"

    def test_precheck_overlap_409_unchanged(self, db, data_dir,
                                            hypothesis_id, strategy_saved):
        """precheck 并集拦截 409 不变锚（A6 守卫③）：区间进判定并集
        的通道=sandbox 挂假设日志（judged_ranges_union 取日志条目
        data_ranges）——沙盒先跑重叠区间后，隔离验证重叠段被拦。"""
        from core.analysis.validation_service import run_sandbox_backtest
        run_sandbox_backtest(
            db, data_dir, strategy_id=strategy_saved, symbol="BTCUSDT",
            timeframe="4h", start="2023-03-05T00:00:00Z",
            end="2023-03-15T00:00:00Z", hypothesis_id=hypothesis_id)
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(
                db, data_dir, hypothesis_id=hypothesis_id,
                strategy_id=strategy_saved,
                request_range=_range("2023-03-10T00:00:00Z",
                                     "2023-03-25T00:00:00Z"))
        assert ei.value.code == "RESEARCH_RANGE_OVERLAP", (
            "precheck 并集拦截行为变化——A6 守卫③红线")
        assert ei.value.status == 409


class TestDisclosureKeys:
    """trail_series/mtf_gated_bars 披露面（T1-I5/T1-I7）。"""

    def test_trail_series_in_metrics(self, db, data_dir, hypothesis_id):
        save_strategy(db, strategy_id="st_trail",
                      dna_json=_dna(sl_mode="trail").to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id="st_trail",
            request_range=_range("2023-03-01T00:00:00Z",
                                 "2023-03-20T00:00:00Z"))
        metrics = out["metrics"]
        assert "trail_series" in metrics
        trail = metrics["trail_series"]
        assert trail is not None and any(
            p["value"] is not None for p in trail), (
            "trail 档须有非空线值段")

    def test_mtf_gated_bars_two_states(self, db, data_dir, hypothesis_id):
        save_strategy(db, strategy_id="st_mtf",
                      dna_json=_mtf_dna().to_json(), symbol="BTCUSDT",
                      timeframe="4h")
        gated = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id, strategy_id="st_mtf",
            request_range=_range("2023-03-01T00:00:00Z",
                                 "2023-03-20T00:00:00Z"))
        # 门控 DNA：记录级键为计数（int）——null 与 0 严格区分的在场断言
        result = json.loads(rr.get_validation_record_by_id(
            db, gated["record_id"])["result"])
        assert isinstance(result.get("mtf_gated_bars"), int)
        # 无门控 DNA（既有 isolated 样本）：null 透传
        plain = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=save_strategy_and_return(db),
            request_range=_range("2023-05-01T00:00:00Z",
                                 "2023-05-20T00:00:00Z"))
        result2 = json.loads(rr.get_validation_record_by_id(
            db, plain["record_id"])["result"])
        assert result2.get("mtf_gated_bars") is None, (
            "无门控面必须 null——与 0=门控全过严格区分")


def save_strategy_and_return(db) -> str:
    save_strategy(db, strategy_id="st_plain", dna_json=_dna().to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    return "st_plain"


class TestDetailEndpointKeys:
    """T1-I6/T1-I7：detail 端点三布尔直译+trace_rows/trail_series。"""

    def test_new_record_full_keys(self, db, data_dir, hypothesis_id,
                                  strategy_saved):
        from api.routes.research.validation_detail import (
            validation_record_detail)
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved,
            request_range=_range("2023-03-01T00:00:00Z",
                                 "2023-03-20T00:00:00Z"))
        detail = validation_record_detail(out["record_id"], db_path=db)
        assert detail["signal_trace"] is True
        assert detail["trade_semantics"] is True
        assert detail["stop_price"] is True
        assert "mtf_gated_bars" in detail
        ex = detail["executions"][0]
        assert ex["exec_ref"] == "main"
        assert isinstance(ex["trace_rows"], list)
        assert "trail_series" in ex

    def test_legacy_record_null_flags(self, db, data_dir, hypothesis_id,
                                      strategy_saved):
        """存量降级：手工造三布尔 NULL 的记录（模拟上线前）——null+
        trace_rows=[]+trail_series=null 合法降级非报错。"""
        from api.routes.research.validation_detail import (
            validation_record_detail)
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved,
            request_range=_range("2023-03-01T00:00:00Z",
                                 "2023-03-20T00:00:00Z"))
        rid = out["record_id"]
        # 手工将三布尔置 NULL（模拟存量）+清空留痕行
        from core.persistence.orm.engine import engine_for
        from sqlalchemy import text
        with engine_for(db).begin() as conn:
            conn.execute(text(
                "UPDATE validation_records SET signal_trace=NULL,"
                " trade_semantics=NULL, stop_price=NULL"
                " WHERE record_id=:r"), {"r": rid})
            conn.execute(text(
                "DELETE FROM vr_signal_trace WHERE record_id=:r"),
                {"r": rid})
        detail = validation_record_detail(rid, db_path=db)
        assert detail["signal_trace"] is None
        assert detail["executions"][0]["trace_rows"] == []
        # 合法降级：端点正常返回（无异常抛出即证——200 语义）
