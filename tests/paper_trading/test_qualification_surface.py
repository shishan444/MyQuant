"""qualification 查询面与报告面 API 端到端（paper-trading-revamp 组 B4/C）。

PaperTaskResponse.qualification_progress 三字段形制+两维查询期现算
（与 pt_equity 快照面独立对照）；滚动报告第四键 qualification
（既有三顶层键 performance/deviation/evidence 不变）。基建沿
test_api_paper.py 形制（TestClient 不进 with 块——不起 lifespan；
app.state 手动挂载）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna
from tests.paper_trading.test_paper_executor import _Harness
from tests.paper_trading.test_qualification_integration import _SHORT_DNA_KW


@pytest.fixture
def paper_api(tmp_path: Path):
    """(client, db, data_dir)：REST 面（不起 lifespan/调度器）。"""
    from fastapi.testclient import TestClient

    from api.app import create_app
    from core.persistence.db_ext import init_db_ext

    db = tmp_path / "paper_api.db"
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    init_db_ext(db)
    app = create_app(db_path=db, data_dir=data_dir)
    app.state.db_path = db
    app.state.data_dir = data_dir
    yield TestClient(app), db, data_dir


@pytest.fixture
def completed_api(tmp_path: Path):
    """(client, h)：_Harness 真跑至 completed（做空高频组合——120 根
    7 次全平+全程 equity>initial_cash），TestClient 挂同库同数据目录。"""
    from fastapi.testclient import TestClient

    from api.app import create_app

    h = _Harness(tmp_path, dna=make_dna(**_SHORT_DNA_KW), window_bars=120)
    ex = h.make_executor()
    ex.prepare()
    paper.update_task(h.db, h.task_id, status="running",
                      started_at="2026-11-01T00:00:00+00:00")
    ex.process_frames(h.enhanced, 0)
    assert h.task()["status"] == "completed"
    app = create_app(db_path=h.db, data_dir=h.registry.data_dir)
    app.state.db_path = h.db
    app.state.data_dir = h.registry.data_dir
    yield TestClient(app), h


class TestQualificationProgress:
    """GET /tasks/{id}.qualification_progress（两维查询期现算+first_met_at 直读）。"""

    def test_pending_progress_all_false_null(self, paper_api):
        """pending 初启（task 行 balance 组 NULL → equity=None 分支）：
        两维 False + first_met_at None（三字段键集恰合）。"""
        client, db, _ = paper_api
        from core.persistence.repository.strategy import save_strategy

        save_strategy(db, strategy_id="st-1",
                      dna_json=make_dna().to_json(), symbol="BTCUSDT",
                      timeframe="4h", status="active",
                      gene_signature="sig_st-1")
        resp = client.post("/api/paper-trading/tasks",
                           json={"strategy_id": "st-1"})
        assert resp.status_code == 201, resp.text
        tid = resp.json()["task_id"]

        got = client.get(f"/api/paper-trading/tasks/{tid}").json()[
            "qualification_progress"]
        assert got == {"pnl_positive": False, "trades_enough": False,
                       "first_met_at": None}

    def test_completed_progress_two_dims_match_snapshot(self, completed_api):
        """completed 后两维=查询期现算，与 pt_equity 末行快照独立对照
        （equity 公式同源：balance+margin+unrealized_pnl）。"""
        client, h = completed_api
        task = h.task()
        body = client.get(
            f"/api/paper-trading/tasks/{h.task_id}").json()
        got = body["qualification_progress"]
        assert set(got) == {"pnl_positive", "trades_enough", "first_met_at"}

        # 独立对照：末行 pt_equity（与 _latest_equity_of 公式同源）
        last_eq = paper.list_equity_snapshots(
            h.db, h.task_id, limit=10**6)[-1]["equity"]
        assert got["pnl_positive"] is (last_eq > float(task["initial_cash"]))
        assert got["trades_enough"] is (int(task["total_trades"]) >= 5)
        # 写入路径（first_met_at=首个满足 bar_time）由
        # test_qualification_integration.TestFirstMetWiring 承证
        assert "first_met_at" in got


class TestReportFourthKey:
    """GET /tasks/{id}/report 第四键 qualification（三顶层键不变）。"""

    def test_rolling_report_qualification_section(self, completed_api):
        client, h = completed_api
        resp = client.get(f"/api/paper-trading/tasks/{h.task_id}/report")
        assert resp.status_code == 200, resp.text
        # 滚动报告四顶层键在响应顶层（report/final 的 FinalReportResponse
        # 才有 report 包装层）
        report = resp.json()
        # 既有三顶层键不变（Scenario「报告版本兼容」）
        for key in ("performance", "deviation", "evidence"):
            assert key in report
        section = report["qualification"]
        assert {"judged_at", "pnl_positive", "trades_enough",
                "trigger_consistency", "qualified"} <= set(section)
        assert isinstance(section["qualified"], bool)
        assert section["judged_at"]
        # 三态形制：NULL≠false≠true（对照重放不可执行→None）
        passed = section["trigger_consistency"]["passed"]
        assert passed is None or isinstance(passed, bool)
        # 凭证签发接线已拆除（retire-legacy Q2=A）：达标不迁移，段内
        # 无 transition 键（防迁移通道静默复活）
        assert "transition" not in section

    def test_running_report_guard_skips_replay(self, tmp_path: Path):
        """running 滚动报告终态守卫（CARD-05 行为锚）：跳过对照重放——
        passed=NULL（running_not_judged 占位）+judged_at=None（未判）+
        两维仍现算+qualified=False（宁缺勿滥）+transition 恰零触碰。"""
        from fastapi.testclient import TestClient

        from api.app import create_app

        h = _Harness(tmp_path, dna=make_dna(**_SHORT_DNA_KW), window_bars=120)
        ex = h.make_executor()
        ex.prepare()
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-11-01T00:00:00+00:00")
        ex.process_frames(h.enhanced.iloc[:80], 0)  # 80/120——窗口未到
        assert h.task()["status"] == "running"

        app = create_app(db_path=h.db, data_dir=h.registry.data_dir)
        app.state.db_path = h.db
        app.state.data_dir = h.registry.data_dir
        client = TestClient(app)
        resp = client.get(f"/api/paper-trading/tasks/{h.task_id}/report")
        assert resp.status_code == 200, resp.text
        section = resp.json()["qualification"]

        assert section["judged_at"] is None  # 未判
        tc = section["trigger_consistency"]
        assert tc["passed"] is None  # NULL≠false≠true 三态占位
        assert tc["denominator"] == 0
        assert tc["detail"]["reason"] == "running_not_judged"
        assert isinstance(section["pnl_positive"], bool)  # 两维仍现算
        assert isinstance(section["trades_enough"], bool)
        assert section["qualified"] is False  # 宁缺勿滥
        assert "transition" not in section  # 接线拆除：无 transition 键
