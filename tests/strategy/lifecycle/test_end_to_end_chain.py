"""延后窗端到端链验收（build-strategy-lifecycle 5.4，族 G+第一批判定
脚本 #2 段）。

一条身份不丢的完整链：研究（假设→翻译→pass 定证→转正入库）→
REST 挂盘（active→paper 经迁移服务+任务创建）→research_status 查询期
现算随动（REQ-1-06）→纸盘任务终态回调回流（paper→active 含守卫与
回补通道）。「入库→纸盘挂载」子句在此真实跑通（先行窗在途声明③项
的销项面）；G3 先行窗接口级用例→端到端映射核对表住 change 目录
acceptance-2.md。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper as paper_repo
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import get_strategy

_CONDS = {
    "when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14}},
         "action": "lt", "target": 35}],
    "then_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 30}},
         "action": "gt", "target": 60}],
}
_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture
def chain_db(tmp_path: Path) -> Path:
    path = tmp_path / "chain.db"
    init_db_ext(path)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(path)
    return path


@pytest.fixture
def chain_client(chain_db: Path, tmp_path: Path):
    """REST 全链（沿 test_api_paper 形制：TestClient 不进 with——不起
    lifespan/守护线程，app.state 手动挂载）。"""
    from fastapi.testclient import TestClient

    from api.app import create_app
    from core.paper_trading.runtime.scheduler import PaperScheduler

    data_dir = tmp_path / "market"
    data_dir.mkdir()
    app = create_app(db_path=chain_db, data_dir=data_dir)
    app.state.db_path = chain_db
    app.state.data_dir = data_dir
    app.state.paper_scheduler = PaperScheduler(
        db_path=chain_db, data_dir=data_dir)
    return TestClient(app)


def _promote_via_research_chain(db: Path) -> str:
    """研究链真实入口转正（入库）：假设→翻译 draft→pass 定证→
    supported 定证→谱系 draft→active（trigger=credential）。"""
    from core.analysis.hypothesis_service import (
        register_hypothesis, transition_hypothesis)
    from core.analysis.hypothesis_to_strategy import translate_hypothesis

    hid = register_hypothesis(
        db, conditions=_CONDS, statement="s", data_ranges=_RANGES)[
        "hypothesis_id"]
    sid = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
    rr.save_validation_with_log(
        db, record_id="rec_chain_pass", hypothesis_id=hid, strategy_id=sid,
        level="isolated_backtest", params="{}", result=None, verdict="pass",
        range_hash="rh_chain_pass")
    out = transition_hypothesis(
        db, hid, target_status="supported", evidence_ref="rec_chain_pass")
    assert out["promoted"] is True
    assert get_strategy(db, sid)["status"] == "active"
    return sid


class TestResearchToMountToEndChain:
    """第一批判定脚本 #2 段（PRD 批级链路验收场景的前段真实跑通）：
    入库→纸盘挂载→research_status 随动→终态回流。"""

    def test_research_mount_return_chain(self, chain_db, chain_client):
        """全链：转正入库→REST 挂盘（迁移+事件）→任务查询 research_
        status=verified（序②事件在场即随动）→终态回调回流 active→
        事件史三件（credential/mount/task_end）完整可溯。"""
        from sqlalchemy import text

        from core.paper_trading.runtime.scheduler import PaperScheduler
        from core.persistence.orm.engine import engine_for

        sid = _promote_via_research_chain(chain_db)

        # 挂盘（#2 段「入库→纸盘挂载」）：201+pending+active→paper 迁移
        resp = chain_client.post("/api/paper-trading/tasks",
                                 json={"strategy_id": sid})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert get_strategy(chain_db, sid)["status"] == "paper"
        # REQ-1-06 迁移后随动：转正事件在场→挂盘任务查询面 verified
        got = chain_client.get(
            f"/api/paper-trading/tasks/{body['task_id']}").json()
        assert got["research_status"] == "verified"
        # 快照列=创建时点审计事实（非 verified 的独立事实源）
        stored = paper_repo.get_task(chain_db, body["task_id"])
        assert stored["research_status_snapshot"] == "verified"

        # 任务终态（合法迁移链两跳）→调度器回调回流（G2 链式面）
        paper_repo.update_task(chain_db, body["task_id"],
                               status="running", started_at="2030-01-01")
        paper_repo.update_task(chain_db, body["task_id"],
                               status="completed", stopped_at="2030-01-01")
        scheduler = PaperScheduler(chain_db, data_dir=chain_db.parent)
        scheduler._executor_concluded(body["task_id"], "completed")
        assert get_strategy(chain_db, sid)["status"] == "active"

        # 事件史完整（身份链可溯：转正→上盘→回流）
        with engine_for(chain_db).connect() as conn:
            events = conn.execute(text(
                "SELECT from_status, to_status, trigger FROM"
                " strategy_transition_event WHERE strategy_id=:i"
                " ORDER BY created_at"), {"i": sid}).all()
        assert events == [
            ("draft", "active", "credential"),
            ("active", "paper", "mount"),
            ("paper", "active", "task_end")]
        # 终态后查询面仍 verified（事件史 append-only 不随回落降）
        got2 = chain_client.get(
            f"/api/paper-trading/tasks/{body['task_id']}").json()
        assert got2["research_status"] == "verified"
