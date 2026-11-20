"""回放执行段异常终态兜底测试（fix-replay-task-lifecycle 任务 2.5——
spec replay-engine「回放任务行终态完整性」三 Scenario）。

三件：单路执行中异常→行 error 终态+ResearchError 语义 5xx 上抛（REST
结构化响应非裸栈）；exit_sweep 单组异常双落（组 failed+该组行 error，
其余组不受影响）；终态幂等（先行终态后异常兜底不重复迁移不覆盖）。
异常注入点=PaperTaskExecutor.process_frames（分段驱动段唯一执行面，
monkeypatch 类方法——与生产驱动路径同面）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.errors import ResearchError
from core.persistence.repository import paper
from core.replay import ReplayRunner
from tests.replay._env import ReplayEnv


def _bomb_process_frames(monkeypatch, *, once: bool = False):
    """注入 process_frames 异常（返回轰炸命中 task_id 清单——once=
    True 只炸首个任务，其余组照常）。

    炸点在执行段任一 bar（分段驱动循环内）——行已建已 running，异常
    冒出 process_frames 后由 runner 兜底接手。
    """
    from core.paper_trading.runtime import executor as exec_mod

    original = exec_mod.PaperTaskExecutor.process_frames
    bombed: list[str] = []

    def bomb(self, *args, **kwargs):
        if not once or not bombed:
            bombed.append(self.task_id)
            raise RuntimeError("injected segment failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        exec_mod.PaperTaskExecutor, "process_frames", bomb)
    return bombed


class TestSegmentErrorTerminal:
    """Scenario「执行中异常落 error 终态」：行 error+error_detail+
    ResearchError 语义上抛（status=500——REST 全局 handler 结构化出站）。"""

    def test_midway_exception_lands_error_terminal(self, tmp_path,
                                                   monkeypatch):
        bombed = _bomb_process_frames(monkeypatch)
        env = ReplayEnv(tmp_path, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)

        with pytest.raises(ResearchError) as ei:
            rr.run(env.db, env.strategy_id, start, end, task_id="et01")

        assert ei.value.code == "REPLAY_EXECUTION_FAILED"
        assert ei.value.status == 500
        assert ei.value.detail == {"task_id": "et01"}
        # 行落 error 终态（error_detail 含异常摘要）——不残 running
        task = paper.get_task(env.db, "et01")
        assert task["status"] == "error"
        assert "RuntimeError" in task["error_detail"]
        assert "injected segment failure" in task["error_detail"]
        assert bombed == ["et01"]  # 构造前提自证：异常确注入执行段

    def test_rest_returns_structured_5xx_not_bare_500(self, tmp_path,
                                                      monkeypatch):
        """REST 面：执行段异常经全局 handler 结构化出站（code/message
        三段体），非裸 500 栈穿透。"""
        _bomb_process_frames(monkeypatch)
        env = ReplayEnv(tmp_path, n_bars=600)
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(db_path=env.db, data_dir=env.data_dir)
        app.state.db_path = env.db
        app.state.data_dir = env.data_dir
        client = TestClient(app, raise_server_exceptions=False)
        start, end = env.range(head_bars=300)

        resp = client.post("/api/research/backtest-replay", json={
            "strategy_id": env.strategy_id,
            "start": start, "end": end})

        assert resp.status_code == 500
        body = resp.json()["detail"]
        assert body["code"] == "REPLAY_EXECUTION_FAILED"
        assert "RuntimeError" in body["message"]
        assert body["detail"] == {"task_id": body["detail"]["task_id"]}
        # 行不残 running（REST 报错与行终态同批语义）
        rows = paper.list_tasks(env.db, status="running", limit=10)
        assert rows == []


class TestExitSweepGroupErrorDoubleLanding:
    """Scenario「exit_sweep 单组异常双落」：组结果标 failed（既有
    fail 语义不变）且该组 pt_task 行落 error 终态；其余组不受影响。"""

    def test_single_group_failure_row_error_others_ok(self, tmp_path,
                                                      monkeypatch):
        bombed = _bomb_process_frames(monkeypatch, once=True)
        from core.analysis.templates.exit_sweep import exit_sweep

        env = ReplayEnv(tmp_path, n_bars=600)
        start, end = env.range(head_bars=300)
        request_range = {"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": start, "end": end}
        grid = {"modes": [{"mode": "pct",
                           "stops": [0.03, 0.05],
                           "take_profits": [0.10, 0.20]}]}

        payload = exit_sweep(
            env.db, env.data_dir, request_range,
            condition={"subject": {"indicator": "RSI",
                                   "params": {"period": 14},
                                   "field": "rsi"},
                       "action": "lt", "target": 55},
            direction="long", grid=grid)

        # 双落其一：组结果标 failed（既有语义——failure_code 取
        # ResearchError.code）；网格 4 组 1 failed/3 ok（信封形制：
        # 组表在内层 payload["payload"]——与 test_exit_sweep 同取法）
        inner = payload["payload"]
        failed_groups = inner.get("failed_groups", [])
        assert failed_groups, "单组异常后 failed_groups 空——fail 语义丢失"
        assert failed_groups[0]["status"] == "failed"
        assert failed_groups[0]["failure_code"] == "REPLAY_EXECUTION_FAILED"
        ok_groups = [r for r in inner["results"]
                     if r.get("status") == "ok"]
        assert len(ok_groups) == 3, "未受注入影响的组数不对（应 3 ok/1 failed）"
        # 双落其二（组 row 不带 task_id——库内对账）：该组 pt_task 行
        # error 终态（error_detail 在案），其余 3 组各有 completed 行；
        # 组行 origin 全 replay（建行传参面——exit_sweep 经 run_from_dna
        # 汇聚自动继承）
        assert bombed, "异常未注入到任何组（构造前提失效）"
        replay_rows = paper.list_tasks(
            env.db, limit=100, origin="replay")
        assert len(replay_rows) == 4, "网格 4 组应各建一行"
        by_status: dict[str, list] = {}
        for row in replay_rows:
            by_status.setdefault(row["status"], []).append(row)
        assert len(by_status.get("error", [])) == 1
        bombed_row = by_status["error"][0]
        assert bombed_row["task_id"] == bombed[0]
        assert "injected segment failure" in bombed_row["error_detail"]
        assert len(by_status.get("completed", [])) == 3


class TestTerminalIdempotence:
    """Scenario「终态幂等」：异常兜底写终态时行已因先行原因终态——
    不重复迁移、不覆盖既有终态与 error_detail。"""

    def test_exception_after_conclusion_keeps_first_terminal(
            self, tmp_path, monkeypatch):
        from core.paper_trading.runtime import executor as exec_mod

        original = exec_mod.PaperTaskExecutor.process_frames

        def conclude_then_bomb(self, *args, **kwargs):
            """先正常驱动（终态信号由执行核心置位——window_bars 到点
            _concluded_status=completed+finalize），终态落定后的后续
            调用抛异常（模拟终态后清理路径异常冒进兜底层）。"""
            result = original(self, *args, **kwargs)
            if self._concluded_status is not None:
                raise RuntimeError("post-terminal cleanup failure")
            return result

        monkeypatch.setattr(
            exec_mod.PaperTaskExecutor, "process_frames", conclude_then_bomb)
        env = ReplayEnv(tmp_path, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)

        with pytest.raises(ResearchError):
            rr.run(env.db, env.strategy_id, start, end, task_id="et03",
                   window_bars=5)

        # 先行终态不被覆盖：status 保持 completed、error_detail 保持
        # 终态语义（completed 无 error_detail——兜底未覆写）
        task = paper.get_task(env.db, "et03")
        assert task["status"] == "completed"
        assert task["error_detail"] is None
        assert task["stopped_at"] is not None  # 终态时点字段在位（finalize 写）


class TestOriginColumnPropagation:
    """建行传参面（任务 2.1）：回放建行 origin=replay；纸盘 REST 建行
    缺省 paper（同库对照——域标记分流实证）。"""

    def test_replay_row_origin_replay(self, tmp_path):
        env = ReplayEnv(tmp_path, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="et04")

        assert paper.get_task(env.db, res.task_id)["origin"] == "replay"

    def test_paper_create_defaults_to_paper(self, tmp_path):
        env = ReplayEnv(tmp_path, n_bars=600)
        tid = paper.create_task(
            env.db, strategy_id=env.strategy_id,
            dna_snapshot_json=env.dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified")

        assert paper.get_task(env.db, tid)["origin"] == "paper"
