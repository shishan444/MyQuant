"""M4 draft 生命周期测试（improve-mcp-agent-loop 4.3）。

核心断言：verdict 匹配强制、abandoned 终态、promote 独立证据条件
（防调包）、转正产物与被验证 DNA 逐位一致（防脱锚回潮）、draft 写锁。
"""
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import (
    ResearchError, register_hypothesis, transition_hypothesis)
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.persistence.repository.strategy import delete_strategy, get_strategy, save_strategy, update_strategy
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr

_CONDS = {
    "when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35},
        {"subject": {"indicator": "EMA", "params": {"period": 50}}, "action": "cross_above", "target": 60000}],
    "then_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}],
}
_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


def _hyp(db, conds=None, ranges=None) -> str:
    return register_hypothesis(
        db, conditions=conds or _CONDS, statement="s",
        data_ranges=ranges or _RANGES)["hypothesis_id"]


def _record(db, hid, sid, verdict, rec_id=None):
    rr.save_validation_with_log(
        db, record_id=rec_id or f"rec_{hid[:8]}_{verdict}",
        hypothesis_id=hid, strategy_id=sid, level="isolated_backtest",
        params="{}", result=None, verdict=verdict, range_hash=f"rh_{hid[:6]}_{verdict}")


class TestVerdictEnforcement:
    def test_fail_record_cannot_support(self, db):
        hid = _hyp(db)
        _record(db, hid, "st_x", "fail")
        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="supported",
                                  evidence_ref=f"rec_{hid[:8]}_fail")
        assert ei.value.code == "RESEARCH_TRANSITION_INVALID"
        assert "verdict" in ei.value.message

    def test_pass_record_cannot_refute(self, db):
        hid = _hyp(db)
        _record(db, hid, "st_x", "pass")
        with pytest.raises(ResearchError) as ei:
            transition_hypothesis(db, hid, target_status="refuted",
                                  evidence_ref=f"rec_{hid[:8]}_pass")
        assert ei.value.code == "RESEARCH_TRANSITION_INVALID"

    def test_inconclusive_record_cannot_support_or_refute(self, db):
        """0.2.11 验收②：inconclusive 证据不可定论——任一迁移方向均 422
        （_REQUIRED_VERDICT 严格相等天然拒，零改动回归锚）。"""
        hid = _hyp(db)
        _record(db, hid, "st_x", "inconclusive")
        for target in ("supported", "refuted"):
            with pytest.raises(ResearchError) as ei:
                transition_hypothesis(db, hid, target_status=target,
                                      evidence_ref=f"rec_{hid[:8]}_inconclusive")
            assert ei.value.code == "RESEARCH_TRANSITION_INVALID"
            assert "inconclusive" in ei.value.message
        # 0.2.11 验收③前半：被拒后假设保持 active（可换区间继续验证）
        assert rr.get_hypothesis(db, hid)["status"] == "active"

    def test_idempotent_replay_returns_stored_verdict(self, db):
        """R5 零重判：同 range_hash 重放幂等返回存量 verdict 原样
        （历史不重写——两态时代记录不受三态化影响）。"""
        hid = _hyp(db)
        _record(db, hid, "st_x", "pass")  # 模拟两态时代存量记录
        existing = rr.find_validation_by_identity(
            db, hid, "st_x", f"rh_{hid[:6]}_pass",
            level="isolated_backtest")
        assert existing is not None
        assert existing["verdict"] == "pass"


class TestAbandoned:
    def test_abandoned_terminal_without_evidence_record(self, db):
        hid = _hyp(db)
        out = transition_hypothesis(db, hid, target_status="abandoned",
                                    evidence_ref="方向拥挤，暂停该思路")
        assert out["status"] == "abandoned" and out["promoted"] is False
        # 落日志（放弃说明留痕）
        logs = rr.list_research_log(db, hypothesis_id=hid)
        transition_logs = [e for e in logs if e["entry_type"] == "transition"]
        assert any("abandoned" in e["summary"] for e in transition_logs)
        # 终态不可再迁移
        with pytest.raises(ResearchError, match="已终态"):
            transition_hypothesis(db, hid, target_status="refuted",
                                  evidence_ref="whatever")
        # 终态假设拒绝验证（隔离回测拒收）
        from core.analysis.validation_service import run_isolated_backtest
        with pytest.raises(ResearchError) as ei:
            run_isolated_backtest(db, Path("."), hypothesis_id=hid,
                                  strategy_id="st_x",
                                  request_range={"symbol": "BTCUSDT",
                                                 "timeframe": "4h",
                                                 "start": "2024-03-01",
                                                 "end": "2024-03-20"})
        assert ei.value.code == "RESEARCH_HYPOTHESIS_TERMINAL"


class TestPromotion:
    def test_full_path_validate_draft_then_promote(self, db):
        """主路径：translate 产 draft → draft 自身 pass → transition 同事务转正；
        转正产物 DNA 与 pass 记录所跑 DNA 逐位一致（防脱锚回潮）。"""
        hid = _hyp(db)
        out = translate_hypothesis(db, hypothesis_id=hid)
        draft_id = out["strategy_id"]
        dna_before = get_strategy(db, draft_id)["dna_json"]
        _record(db, hid, draft_id, "pass")
        result = transition_hypothesis(db, hid, target_status="supported",
                                       evidence_ref=f"rec_{hid[:8]}_pass")
        assert result["promoted"] is True
        assert result["promoted_strategy_id"] == draft_id
        row = get_strategy(db, draft_id)
        assert row["status"] == "active"
        assert row["dna_json"] == dna_before, "转正产物 DNA 必须与被验证 DNA 逐位一致"

    def test_evidence_swap_blocked(self, db):
        """换证据调包被拦：用策略 S 的 pass 定证、未验证的 draft 不转正
        （promote 独立证据条件）。"""
        hid = _hyp(db)
        draft_id = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        # 证据挂在别的策略 S 上（draft 自身无任何验证记录）
        _record(db, hid, "st_other", "pass")
        result = transition_hypothesis(db, hid, target_status="supported",
                                       evidence_ref=f"rec_{hid[:8]}_pass")
        assert result["status"] == "supported", "假设照常定证（证据在假设层面真实）"
        assert result["promoted"] is False, "未验证的 draft 不得转正"
        assert get_strategy(db, draft_id)["status"] == "draft"
        assert result["promotion_note"] and "pass" in result["promotion_note"]

    def test_promotion_tolerates_deleted_draft(self, db):
        """promote 遇谱系 draft 已被人工删除：跳过转正、不阻塞假设定案。"""
        hid = _hyp(db)
        draft_id = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        delete_strategy(db, draft_id)
        _record(db, hid, "st_any", "pass")
        result = transition_hypothesis(db, hid, target_status="supported",
                                       evidence_ref=f"rec_{hid[:8]}_pass")
        assert result["status"] == "supported"
        assert result["promoted"] is False

    def test_promote_writes_transition_event_via_service(self, db):
        """3.1 经服务转正：事件表 draft→active（trigger=credential）
        在场且 evidence 含 pass record_id；ResearchLog 留痕保持。"""
        from core.persistence.orm.engine import engine_for
        from sqlalchemy import text

        hid = _hyp(db)
        draft_id = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        _record(db, hid, draft_id, "pass")
        result = transition_hypothesis(db, hid, target_status="supported",
                                       evidence_ref=f"rec_{hid[:8]}_pass")
        assert result["promoted"] is True
        engine = engine_for(db)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT from_status, to_status, trigger, evidence_json"
                " FROM strategy_transition_event"
                " WHERE strategy_id=:i"), {"i": draft_id}).all()
        assert len(rows) == 1
        assert rows[0][:3] == ("draft", "active", "credential")
        ev = json.loads(rows[0][3])
        assert ev["record_id"] == f"rec_{hid[:8]}_pass"
        assert ev["hypothesis_id"] == hid
        # ResearchLog 留痕保持（经服务不改日志形制）
        logs = rr.list_research_log(db, hypothesis_id=hid)
        promotion_logs = [e for e in logs if e["entry_type"] == "promotion"
                          and e["ref_id"] == draft_id]
        assert len(promotion_logs) == 1
        assert "draft promoted -> active" in promotion_logs[0]["summary"]

    def test_promote_same_transaction_atomic(self, db, monkeypatch):
        """3.1 同事务原子：迁移服务抛错→假设迁移与谱系转正整体回滚
        （事件零写入、假设保持 active、draft 保持 draft）。"""
        from core.strategy import lifecycle

        def _boom(*args, **kwargs):
            raise RuntimeError("transit 注入失败")

        monkeypatch.setattr(lifecycle, "transit", _boom)
        hid = _hyp(db)
        draft_id = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        _record(db, hid, draft_id, "pass")
        with pytest.raises(RuntimeError, match="transit 注入失败"):
            transition_hypothesis(db, hid, target_status="supported",
                                  evidence_ref=f"rec_{hid[:8]}_pass")
        # 假设迁移回滚（仍 active）+谱系回滚（仍 draft）+事件零行
        assert rr.get_hypothesis(db, hid)["status"] == "active"
        assert get_strategy(db, draft_id)["status"] == "draft"
        from core.persistence.orm.engine import engine_for
        from sqlalchemy import text
        with engine_for(db).connect() as conn:
            assert conn.execute(text(
                "SELECT COUNT(*) FROM strategy_transition_event")).scalar_one() == 0

    def test_refuted_keeps_draft(self, db):
        """证伪假设的草稿不转正（保持 draft，永不进入可用策略集）。"""
        hid = _hyp(db)
        draft_id = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        _record(db, hid, draft_id, "fail")
        result = transition_hypothesis(db, hid, target_status="refuted",
                                       evidence_ref=f"rec_{hid[:8]}_fail")
        assert result["status"] == "refuted" and result["promoted"] is False
        assert get_strategy(db, draft_id)["status"] == "draft"


class TestDraftWriteLock:
    def test_put_dna_on_draft_rejected(self, db):
        """draft 的 DNA 对 PUT 不可写（409 RESEARCH_DRAFT_IMMUTABLE）。"""
        from fastapi import HTTPException
        from api.routes.strategies.crud import update_strategy_endpoint
        from api.schemas import StrategyUpdate
        from core.strategy.dna import StrategyDNA

        hid = _hyp(db)
        draft_id = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        dna = StrategyDNA.from_json(get_strategy(db, draft_id)["dna_json"])
        from api.schemas import DNAModel
        with pytest.raises(HTTPException) as ei:
            update_strategy_endpoint(
                draft_id,
                StrategyUpdate(dna=DNAModel.model_validate(
                    json.loads(dna.to_json()))),
                db_path=db)
        assert ei.value.status_code == 409
        assert ei.value.detail["code"] == "RESEARCH_DRAFT_IMMUTABLE"

    def test_put_metadata_on_draft_allowed(self, db):
        """draft 的无害元数据（name/tags/notes）仍可更新。"""
        from api.routes.strategies.crud import update_strategy_endpoint
        from api.schemas import StrategyUpdate

        hid = _hyp(db)
        draft_id = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        resp = update_strategy_endpoint(
            draft_id, StrategyUpdate(name="renamed draft"), db_path=db)
        assert resp.name == "renamed draft"
        assert resp.status == "draft"
