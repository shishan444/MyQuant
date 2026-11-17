"""库内持久化 JSON 解析兜底测试（fix-review CARD-02）。

覆盖五处库内数据 json.loads 的结构化拒绝（research-error-channel
形制：RESEARCH_* 码 + 422 + detail 点名来源），消灭裸
JSONDecodeError 炸 500 的静默面：
- validation_service _dup_payload / random_window 幂等 result 损坏
  → RESEARCH_STORED_DATA_INVALID（新码，errors.load_stored_json 单源）
- hypothesis_to_strategy conditions / data_ranges 损坏
  → RESEARCH_STORED_DATA_INVALID
- signal_assembly ModelPlugin config 损坏
  → RESEARCH_SIGNAL_SOURCE_INVALID（同函数族既有码，:154/:165 先例）

构造法：正常登记 → sqlite 直改列值为截断 JSON → 走公开入口断言
结构化拒绝（不走私有函数——沿消费链真实触达）。
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.signal_assembly import assemble_signal_set
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, RiskGenes, SignalGene, SignalRole, StrategyDNA,
)

BROKEN_JSON = '{"direction": "long",'  # 截断 JSON（合法前缀+悬空逗号）


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    from core.analysis.executor import ensure_research_tables
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(7)
    idx = pd.date_range("2023-11-01", periods=900, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 120, 900))
    pd.DataFrame({"open": close, "high": close + 70, "low": close - 70,
                  "close": close, "volume": rng.uniform(100, 900, 900),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_4h.parquet")
    return d


def _register_hyp(db) -> str:
    return register_hypothesis(
        db, conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}},
                 "action": "gt", "target": 60}]},
        statement="s", data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                                     "start": "2024-05-01T00:00:00Z",
                                     "end": "2024-06-01T00:00:00Z"}]
    , strategy_params={"stop_loss": 0.05})["hypothesis_id"]


def _save_strategy(db, sid) -> None:
    # 卡 R 起装载挂全量 validate_dna（Q-1）：补恒假 exit（行为等效）
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35}),
                      SignalGene("RSI", {"period": 14},
                                 SignalRole.EXIT_TRIGGER, "rsi_14",
                                 {"type": "gt", "threshold": 1e9})],
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id=sid, dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")


class TestHypothesisJsonCorrupt:
    """假设 conditions / data_ranges 损坏 → 翻译结构化拒绝。"""

    def _translate(self, db, hid):
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        return translate_hypothesis(db, hypothesis_id=hid)

    @pytest.mark.parametrize("column", ["conditions", "data_ranges"])
    def test_corrupt_column_rejected(self, db, column):
        """逐列畸形：RESEARCH_STORED_DATA_INVALID，message 点名
        research_hypotheses.<列>，detail 带 hypothesis_id 与 parse_error
        （修复前：裸 JSONDecodeError 炸 500）。"""
        hid = _register_hyp(db)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                f"UPDATE research_hypotheses SET {column} = ? "
                "WHERE hypothesis_id = ?", (BROKEN_JSON, hid))
            conn.commit()
        with pytest.raises(ResearchError) as ei:
            self._translate(db, hid)
        assert ei.value.code == "RESEARCH_STORED_DATA_INVALID"
        assert ei.value.status == 422
        assert f"research_hypotheses.{column}" in ei.value.message
        assert ei.value.detail["hypothesis_id"] == hid
        assert "parse_error" in ei.value.detail

    def test_intact_hypothesis_still_translates(self, db):
        """完好假设翻译不受影响（守卫不误伤正常链路）。"""
        hid = _register_hyp(db)
        out = self._translate(db, hid)
        assert out["status"] == "draft"


class TestModelPluginConfigCorrupt:
    """ModelPlugin config 损坏 → RESEARCH_SIGNAL_SOURCE_INVALID
    （同函数族既有码，与无 valid 版本/求值失败同因=信号源不可用）。"""

    def _model_env(self, db):
        from sqlalchemy import insert
        from core.persistence.orm.models import ModelPlugin
        from core.persistence.repository.dataset_meta import _engine_for
        with _engine_for(db).begin() as conn:
            conn.execute(insert(ModelPlugin).values(
                plugin_id="mpl_m_v1", name="m", version=1,
                config=BROKEN_JSON, signal_column="model_m",
                status="valid", strategy_visible=1,
                created_at="2026-10-15T00:00:00"))
        rr.register_existing_plugin_signal(
            db, source="model", plugin_id="mpl_m_v1", name="m", version=1,
            column_name="model_m")
        return db

    def test_corrupt_config_rejected(self, db):
        db = self._model_env(db)
        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 14},
                                     SignalRole.ENTRY_TRIGGER, "rsi_14",
                                     {"type": "lt", "threshold": 35})],
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            signal_sources=[{"type": "model", "ref": "model_m"}])
        rng = np.random.default_rng(6)
        idx = pd.date_range("2024-01-01", periods=300, freq="4h", tz="UTC",
                            name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 110, 300))
        df = pd.DataFrame({"open": close, "high": close + 60,
                           "low": close - 60, "close": close,
                           "volume": rng.uniform(100, 800, 300),
                           "trades": 40}, index=idx)
        with pytest.raises(ResearchError) as ei:
            assemble_signal_set(db, dna, df)
        assert ei.value.code == "RESEARCH_SIGNAL_SOURCE_INVALID"
        assert ei.value.detail["ref"] == "model_m"
        assert "parse_error" in ei.value.detail


class TestCodeParamSwitch:
    """TD-02 通道扩展：load_stored_json 的 code 形参可切换错误码
    （纸盘域 PAPER_STORED_DATA_INVALID）；缺省行为逐字节不变。"""

    def test_default_code_unchanged(self):
        from core.analysis.errors import load_stored_json
        with pytest.raises(ResearchError) as ei:
            load_stored_json(BROKEN_JSON, source="t.c", detail={"k": 1})
        assert ei.value.code == "RESEARCH_STORED_DATA_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["k"] == 1
        assert "parse_error" in ei.value.detail

    def test_paper_code_switch(self):
        from core.analysis.errors import load_stored_json
        with pytest.raises(ResearchError) as ei:
            load_stored_json(BROKEN_JSON, source="pt_signal.gene_hits",
                             detail={"task_id": "t1"},
                             code="PAPER_STORED_DATA_INVALID")
        assert ei.value.code == "PAPER_STORED_DATA_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["task_id"] == "t1"

    def test_valid_json_roundtrip_both_modes(self):
        from core.analysis.errors import load_stored_json
        obj = {"a": [1, 2]}
        assert load_stored_json(json.dumps(obj), source="t.c") == obj
        assert load_stored_json(json.dumps(obj), source="t.c",
                                code="PAPER_STORED_DATA_INVALID") == obj
