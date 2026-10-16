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
    )["hypothesis_id"]


def _save_strategy(db, sid) -> None:
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35})],
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id=sid, dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")


class TestIdempotentResultCorrupt:
    """幂等命中记录 result 损坏 → RESEARCH_STORED_DATA_INVALID。"""

    def test_isolated_dup_result_corrupt_rejected(self, db, data_dir):
        """隔离回测幂等重放：result_json 损坏 → 422 结构化拒绝点名
        record_id 与来源（修复前：裸 JSONDecodeError 炸 500）。"""
        from core.analysis.validation_service import (
            _range_hash, run_isolated_backtest,
        )
        hid = _register_hyp(db)
        req = {"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2024-01-01T00:00:00Z", "end": "2024-01-15T00:00:00Z"}
        rr.save_validation_with_log(
            db, record_id="rec_broken", hypothesis_id=hid, strategy_id="st_x",
            level="isolated_backtest", params=json.dumps({"request_range": req}),
            result=BROKEN_JSON, verdict="pass", range_hash=_range_hash(req))
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(db, data_dir, hypothesis_id=hid,
                                  strategy_id="st_x", request_range=req)
        assert ei.value.code == "RESEARCH_STORED_DATA_INVALID"
        assert ei.value.status == 422
        assert "validation_records.result" in ei.value.message
        assert ei.value.detail["record_id"] == "rec_broken"
        assert "parse_error" in ei.value.detail

    def test_random_window_dup_result_corrupt_rejected(self, db, data_dir):
        """随机窗幂等重放：result 损坏同面拒绝（同 helper 单源）。"""
        from core.analysis.validation_service import _range_hash
        from core.analysis.validation_service import random_window_test
        hid = _register_hyp(db)
        _save_strategy(db, "st_rw")
        rh = _range_hash({"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2023-11-01T00:00:00Z",
                          "end": "2024-02-01T00:00:00Z",
                          "window_bars": 200, "n": 8, "seed": 42})
        rr.save_validation_with_log(
            db, record_id="rec_rw_broken", hypothesis_id=hid,
            strategy_id="st_rw", level="random_window",
            params=json.dumps({"seed": 42}), result=BROKEN_JSON,
            verdict="pass", range_hash=rh)
        with pytest.raises(ResearchError) as ei:
            random_window_test(db, data_dir, strategy_id="st_rw",
                               symbol="BTCUSDT", timeframe="4h",
                               data_start="2023-11-01T00:00:00Z",
                               data_end="2024-02-01T00:00:00Z",
                               hypothesis_id=hid)
        assert ei.value.code == "RESEARCH_STORED_DATA_INVALID"
        assert ei.value.detail["record_id"] == "rec_rw_broken"


class TestAuthorityKeyBlacklist:
    """CARD-03：幂等 result_json 内嵌权威键不覆盖 base（记录列与
    调用方为权威源）；正常键合并与 direction 收敛不受影响。"""

    def _dup_with_result(self, db, result_json: str) -> dict:
        from core.analysis.validation_service import (
            _range_hash, run_isolated_backtest,
        )
        hid = _register_hyp(db)
        req = {"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2024-01-01T00:00:00Z", "end": "2024-01-15T00:00:00Z"}
        rr.save_validation_with_log(
            db, record_id="rec_auth", hypothesis_id=hid, strategy_id="st_a",
            level="isolated_backtest", params=json.dumps({"request_range": req}),
            result=result_json, verdict="pass", range_hash=_range_hash(req))
        return run_isolated_backtest(db, db, hypothesis_id=hid,
                                     strategy_id="st_a", request_range=req)

    def test_authority_keys_not_overridden(self, db, caplog):
        """伪造三键（record_id/verdict/duplicated）不覆盖权威值
        （修复前：duplicated=false / verdict=fake / record_id=FAKE
        会从 result 内嵌透出——幂等语义可被库内数据伪造）。"""
        forged = json.dumps({
            "record_id": "FAKE_ID", "verdict": "fail", "duplicated": False,
            "metrics": {"total_trades": 1}})
        with caplog.at_level("WARNING", logger="core.analysis.validation_service"):
            out = self._dup_with_result(db, forged)
        assert out["record_id"] == "rec_auth"
        assert out["verdict"] == "pass"
        assert out["duplicated"] is True
        # 非权威键仍顶层化合并（黑名单不误伤）
        assert out["metrics"] == {"total_trades": 1}
        # 命中跳过有 warning 留痕
        assert any("权威键" in r.message for r in caplog.records)

    def test_normal_result_merges_unchanged(self, db, caplog):
        """正常 result（无权威键）零 warning、合并行为不变（守卫
        不改既有正常链路）。"""
        normal = json.dumps({
            "metrics": {"total_trades": 2}, "bars_used": 300,
            "direction": "short"})
        with caplog.at_level("WARNING", logger="core.analysis.validation_service"):
            out = self._dup_with_result(db, normal)
        assert out["duplicated"] is True
        assert out["verdict"] == "pass"
        assert out["metrics"] == {"total_trades": 2}
        assert out["direction"] == "short"  # 合法二值原样回显
        assert not caplog.records

    def test_result_json_non_dict_guarded(self, db, data_dir):
        """result_json 为合法 JSON 但非 dict（如数组——load_stored_json
        只拦解析失败不拦形制）：isinstance 守卫兜住不炸，权威三键仍
        正确、direction 收敛 long（防御分支行为锁定——非 dict 内容
        不参与合并，result 内嵌键缺席如实缺席）。"""
        out = self._dup_with_result(db, "[1, 2, 3]")
        assert out["record_id"] == "rec_auth"
        assert out["verdict"] == "pass"
        assert out["duplicated"] is True
        assert out["direction"] == "long"
        assert "metrics" not in out  # 非 dict 无可合并键


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
