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







class TestIdempotentAndPrecheck:
    """T1-I8 幂等锚+T1-S3 precheck 409 不变锚。"""




class TestDisclosureKeys:
    """trail_series/mtf_gated_bars 披露面（T1-I5/T1-I7）。"""







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
        from core.analysis.validation_service import run_sandbox_backtest
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=strategy_saved,
            hypothesis_id=hypothesis_id, symbol="BTCUSDT", timeframe="4h",
            start="2023-03-01T00:00:00Z", end="2023-03-20T00:00:00Z")
        detail = validation_record_detail(out["record_id"], db_path=db)
        assert detail["signal_trace"] is True
        assert detail["trade_semantics"] is True
        assert detail["stop_price"] is True
        assert "mtf_gated_bars" in detail
        ex = detail["executions"][0]
        assert ex["exec_ref"] == "main"
        assert isinstance(ex["trace_rows"], list)
        assert "trail_series" in ex

    def test_composite_level_defensive_empty_metrics(self, db,
                                                     hypothesis_id):
        """阶段四闸门补：死值域 level 的防御路径（composite/component）。

        需有份明细（executions 非空——无明细在 per_exec 分支前即
        return False 骨架，走不到防御分支）：per_exec_metrics 三分支
        （isolated/random/staged）皆不匹配 composite→空列表→下游 idx
        越界退化 {}——端点不崩+trail_series null 如实（缺键语义）。
        """
        from api.routes.research.validation_detail import (
            validation_record_detail)
        from core.persistence.orm.engine import engine_for
        from sqlalchemy import text
        with engine_for(db).begin() as conn:
            conn.execute(text(
                "INSERT INTO validation_records (record_id, hypothesis_id,"
                " strategy_id, level, segmentation, params, result, verdict,"
                " range_hash, created_at) VALUES"
                " ('rec_comp', :h, NULL, 'composite', '', '{}',"
                " '{\"metrics\": {}}', 'pass', 'rh_c',"
                " '2026-01-01T00:00:00Z')"), {"h": hypothesis_id})
        from core.persistence.orm.models import VrEquityCurve
        from sqlalchemy.orm import Session
        from core.persistence.orm.engine import engine_for as _ef
        with Session(_ef(db)) as sess:
            sess.add(VrEquityCurve(
                record_id="rec_comp", exec_ref="main", exec_index=0,
                range_start="2024-01-01T00:00:00Z",
                range_end="2024-01-02T00:00:00Z", grid_aligned=1,
                timeframe="4h", start="2024-01-01T00:00:00Z", length=1,
                timestamps='["2024-01-01T00:00:00Z"]',
                values="[100000.0]"))
            sess.commit()
        detail = validation_record_detail("rec_comp", db_path=db)
        assert detail["detail_available"] is True
        ex = detail["executions"][0]
        assert ex["trail_series"] is None, (
            "composite 死值域份 metrics 越界退化 {}——trail_series "
            "缺键 null 如实（防御分支不崩不捏造）")

    def test_legacy_record_null_flags(self, db, data_dir, hypothesis_id,
                                      strategy_saved):
        """存量降级：手工造三布尔 NULL 的记录（模拟上线前）——null+
        trace_rows=[]+trail_series=null 合法降级非报错。"""
        from api.routes.research.validation_detail import (
            validation_record_detail)
        from core.analysis.validation_service import run_sandbox_backtest
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=strategy_saved,
            hypothesis_id=hypothesis_id, symbol="BTCUSDT", timeframe="4h",
            start="2023-03-01T00:00:00Z", end="2023-03-20T00:00:00Z")
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
        # spec「存量降级」THEN 第三要素（CARD-05 补）：
        # trail_series=null——样本非 trail 档，metrics 落 null 透传
        assert detail["executions"][0]["trail_series"] is None
        # 合法降级：端点正常返回（无异常抛出即证——200 语义）
