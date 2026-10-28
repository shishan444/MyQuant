"""受限并发模型行为验证（research-tooling-data-quality 卡 B——tester 锚 C3-A）。

真实任务并发端到端（非挂起桩占槽）：受控评估替身沿 happy path 形制
（test_evolution_runner_happy_path 先例——调度链/状态机/落库/
TaskController 全真实，仅评估计算为确定性替身；评估属引擎内部计算，
不在并发行为被测面内）。覆盖：

- A1/A2 主路径：双任务真实并发运行+第 3 任务 pending 排队（占槽计数
  与状态迁移断言——specs Scenario「并行占槽」）
- A3/A4：MCP 停止五段链（MCP 工具→REST 端点→service→TaskController
  直达信号→任务终态）+停止后槽释放与新任务启动
- S3：runner-status REST 端点并发三键披露（v2 数值一致性）
- A5：SQLite 并发写安全闸门（实施证据的测试面固化——4 线程并发写
  快照表零冲突，对齐实施闸门口径）

回退串行分支（A2/A5 回退案）：闸门验证通过走主路径，代码无回退开关
——回退分支无实施面，行为锚推导待执行（启用回退时补验 FIFO 排队）。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db import get_task, save_task
from core.persistence.db_ext import init_db_ext
from tests.helpers.data_factory import make_dna


def _wait_for(predicate, timeout: float = 15.0, interval: float = 0.05,
              msg: str = "条件未在时限内满足"):
    """有界条件等待：轮询外部线程到达目标状态（deadline 失败即红，
    不靠任意 sleep 碰运气——等待条件与超时语义显式）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"等待超时: {msg}")


def _stub_score(dna) -> float:
    """确定性 score（沿 happy path 形制——驱动编排流程用）。"""
    sl = float(dna.risk_genes.stop_loss)
    return round(100.0 * max(0.0, 1.0 - abs(sl - 0.075) / 0.075), 4)


def _preset_task(db: Path, task_id: str, *, max_generations: int = 2) -> None:
    """预置 pending 任务并压缩代数/关连续进化（缩短端到端时长）。"""
    save_task(db, task_id=task_id, target_score=80.0, template="profit_first",
              symbol="BTCUSDT", timeframe="4h", initial_dna=make_dna())
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE evolution_task SET max_generations = ?, continuous = 0 "
        "WHERE task_id = ?", (max_generations, task_id))
    conn.commit()
    conn.close()


class TestRealConcurrencyPool:
    """真实任务双并发（A1+A2 主路径+S1+S3 数值一致性）。"""

    @pytest.fixture()
    def app_env(self, tmp_path):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from api.app import create_app
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv

        db = tmp_path / "conc.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        app = create_app(db_path=db, data_dir=d)
        enhanced = compute_all_indicators(make_ohlcv(120, "4h"))
        with patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=enhanced), \
             patch("api.runner_progress._push_ws"), \
             TestClient(app) as client:
            runner = app.state.evolution_runner
            assert runner.is_alive(), "lifespan 应已启动调度线程"
            yield {"client": client, "db": db, "runner": runner}

    def _install_eval_stubs(self, runner, gate: threading.Event):
        """受控评估替身：种群批评估挂起在 gate（占槽观察窗）——
        释放后正常返回确定性分数。"""
        from unittest.mock import patch

        def fake_pop(pop, *a, **kw):
            gate.wait(timeout=15)
            return [_stub_score(d) for d in pop]

        def fake_dna(ind, *a, **kw):
            score = _stub_score(ind)
            return {"score": score, "fitness": score, "qualified": False,
                    "raw_metrics": {}, "dimension_scores": {},
                    "total_trades": 0}

        p1 = patch.object(runner, "_evaluate_population", fake_pop)
        p2 = patch.object(runner, "_evaluate_dna", fake_dna)
        p1.start()
        p2.start()
        self._patches = [p1, p2]

    def teardown_method(self):
        for p in getattr(self, "_patches", []):
            p.stop()

    def test_two_real_tasks_concurrent_third_waits(self, app_env):
        """双任务真实并发占满槽：同刻 running+active_count=2，第 3 任务
        保持 pending（不超并发不饥饿）；释放后全部自然终态。"""
        db, client, runner = app_env["db"], app_env["client"], app_env["runner"]
        gate = threading.Event()
        self._install_eval_stubs(runner, gate)
        for tid in ("t1", "t2", "t3"):
            _preset_task(db, tid)

        # 两任务提交进池并发运行（挂起在首代评估——占槽观察窗）
        _wait_for(lambda: get_task(db, "t1")["status"] == "running"
                  and get_task(db, "t2")["status"] == "running",
                  msg="t1/t2 未进入并发运行")
        t3 = get_task(db, "t3")
        assert t3["status"] == "pending", "槽满时第 3 任务必须排队不启动"

        s = runner.get_status()
        assert s["max_workers"] == 2
        assert s["active_count"] == 2, "两任务在飞占满槽（真实并发）"
        assert s["queued_count"] == 0  # t3 尚未提交入池（DB pending≠池排队）
        assert set(s["active_task_ids"]) == {"t1", "t2"}

        # REST 披露面（S3）：并发三键在场且数值与 DB 一致
        r = client.get("/api/evolution/runner-status")
        assert r.status_code == 200
        body = r.json()
        assert body["max_workers"] == 2
        assert body["active_count"] == 2
        assert body["queued_count"] == 0

        # 释放：两任务走完各自代际自然终态→槽释放→t3 被拾取
        gate.set()
        _wait_for(lambda: get_task(db, "t3")["status"] == "completed",
                  timeout=30, msg="t3 未在槽释放后被拾取并完成")
        for tid in ("t1", "t2"):
            row = get_task(db, tid)
            assert row["status"] == "completed", (
                f"{tid} 终态={row['status']} stop={row.get('stop_reason')}")
        _wait_for(lambda: runner.get_status()["active_count"] == 0,
                  msg="终态后 active_count 应回 0")

    def test_mcp_stop_five_segment_chain_releases_slot(self, app_env):
        """停止五段链+槽释放（A3+A4+S2+S9）：MCP stop→REST→service→
        TaskController 直达信号→挂起任务当代检查点停止；槽释放后
        pending 任务被启动；MCP 返回终态摘要。"""
        from mcpserver import tools as tools_mod

        db, client, runner = app_env["db"], app_env["client"], app_env["runner"]

        class _HttpApi:
            def __init__(self, c):
                self._c = c

            def get(self, url, params=None):
                r = self._c.get(url, params=params)
                if r.is_error:
                    from mcpserver.api_client import _raise_api_error
                    _raise_api_error(r)
                return r.json()

            def post(self, url, body):
                r = self._c.post(url, json=body)
                if r.is_error:
                    from mcpserver.api_client import _raise_api_error
                    _raise_api_error(r)
                return r.json()

        class _FakeMCP:
            def __init__(self):
                self.registry = {}

            def tool(self):
                def deco(fn):
                    self.registry[fn.__name__] = fn
                    return fn
                return deco

        fake = _FakeMCP()
        tools_mod.register_research_tools(fake, _HttpApi(client))
        stop_fn = fake.registry["stop_evolution_task"]

        # 占槽替身：挂起直到任一控制器收到停止请求（直达信号唤醒——
        # t1 的停止即经此通道）或测试侧 gate 释放（stop 消费完后其余
        # 任务正常走完，有界轮询防悬挂）
        from api.runner import get_active_controllers
        gate = threading.Event()

        def fake_pop(pop, *a, **kw):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if gate.is_set() or any(
                        c.stop_requested
                        for c in get_active_controllers().values()):
                    break
                time.sleep(0.02)
            return [_stub_score(d) for d in pop]

        def fake_dna(ind, *a, **kw):
            score = _stub_score(ind)
            return {"score": score, "fitness": score, "qualified": False,
                    "raw_metrics": {}, "dimension_scores": {},
                    "total_trades": 0}

        from unittest.mock import patch
        with patch.object(runner, "_evaluate_population", fake_pop), \
                patch.object(runner, "_evaluate_dna", fake_dna):
            for tid in ("t1", "t2", "t3"):
                _preset_task(db, tid)
            _wait_for(lambda: get_task(db, "t1")["status"] == "running"
                      and get_task(db, "t2")["status"] == "running",
                      msg="t1/t2 未进入并发运行")
            assert get_task(db, "t3")["status"] == "pending"

            # 段①MCP 工具→②REST 端点→③service→④TaskController 直达
            out = stop_fn(task_id="t1")
            gate.set()  # 释放其余任务（stop 信号已被 t1 消费验证）
            assert "error" not in out, out
            assert out["status"] == "stopped", "MCP 返回终态摘要（S9）"

            # 段⑤终态：停止生效+槽释放+t3 被拾取启动
            row = get_task(db, "t1")
            assert row["status"] == "stopped"
            assert row["stop_reason"] == "user_stop"
            _wait_for(lambda: get_task(db, "t3")["status"] == "completed",
                      timeout=30, msg="槽释放后 t3 未被启动并完成")
            t2 = get_task(db, "t2")
            assert t2["status"] == "completed", (
                f"t2 不受 t1 停止影响应自然完成: {t2['status']}")


class TestRunnerStatusEndpoint:
    """S3：runner-status REST 披露（无任务空载态三键）。"""

    def test_status_endpoint_three_keys(self, tmp_path):
        from fastapi.testclient import TestClient
        from api.app import create_app

        db = tmp_path / "s.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        app = create_app(db_path=db, data_dir=d)
        with TestClient(app) as client:
            r = client.get("/api/evolution/runner-status")
            assert r.status_code == 200
            body = r.json()
            assert body["max_workers"] == 2, (
                "config evolution.max_concurrent_tasks=2 配置生效断言")
            assert isinstance(body["active_count"], int)
            assert isinstance(body["queued_count"], int)


class TestPauseSemantics:
    """CARD-01 pause 族行为锚（review4_cards.md CARD-01 五锚）。

    修复链：TaskController pause 信号面+pause_task 信号先行+_tick 排除集
    并集（_futures ∪ _active_controllers）+_run_task 静默终结分支+
    on_generation 代末检查点。评估替身沿 TestRealConcurrencyPool 形制。
    """

    @pytest.fixture()
    def app_env(self, tmp_path):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from api.app import create_app
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv

        db = tmp_path / "pause.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        app = create_app(db_path=db, data_dir=d)
        enhanced = compute_all_indicators(make_ohlcv(120, "4h"))
        with patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=enhanced), \
             patch("api.runner_progress._push_ws"), \
             TestClient(app) as client:
            runner = app.state.evolution_runner
            yield {"client": client, "db": db, "runner": runner}

    def _install_gate_stub(self, runner, gate: threading.Event,
                           wake_on_stop: bool = False):
        """评估替身：种群批评估挂起在 gate（或 stop 信号）——窗口
        构造用（有界轮询防悬挂）。"""
        from unittest.mock import patch
        from api.runner import get_active_controllers

        def fake_pop(pop, *a, **kw):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if gate.is_set():
                    break
                if wake_on_stop and any(
                        c.stop_requested
                        for c in get_active_controllers().values()):
                    break
                time.sleep(0.02)
            return [_stub_score(d) for d in pop]

        def fake_dna(ind, *a, **kw):
            score = _stub_score(ind)
            return {"score": score, "fitness": score, "qualified": False,
                    "raw_metrics": {}, "dimension_scores": {},
                    "total_trades": 0}

        p1 = patch.object(runner, "_evaluate_population", fake_pop)
        p2 = patch.object(runner, "_evaluate_dna", fake_dna)
        p1.start()
        p2.start()
        return [p1, p2]

    def test_pause_resume_no_double_run_full_chain(self, app_env, caplog):
        """锚 1+2+3 合链：pause→登记剔除→resume→窗口内不重拾（排除集
        含 controller）→释放后当代完整落库+静默终结→重拾重跑（旧流
        已死，同 task_id 双执行流不存在）。"""
        import logging

        db, client, runner = (app_env["db"], app_env["client"],
                              app_env["runner"])
        gate = threading.Event()
        patches = self._install_gate_stub(runner, gate)
        try:
            with caplog.at_level(logging.INFO, logger="RUNNER"):
                _preset_task(db, "t1", max_generations=3)
                _wait_for(
                    lambda: get_task(db, "t1")["status"] == "running",
                    msg="t1 未启动")
                old_fut = runner._futures.get("t1")
                assert old_fut is not None and not old_fut.done()

                # pause：信号先行+守护写 paused
                r = client.post("/api/evolution/tasks/t1/pause")
                assert r.status_code == 200
                assert r.json()["status"] == "paused"
                tick_base = runner._tick_count
                # paused 态经历多轮 tick——校验段剔除 _futures 登记
                # （reviewer 实证链的前半：排除集此后仅剩 controller 面）
                _wait_for(lambda: runner._tick_count >= tick_base + 4,
                          msg="等待登记剔除窗口")
                with runner._lock:
                    assert "t1" not in runner._futures, (
                        "paused 态登记应被校验段剔除")

                # resume：paused→pending
                r2 = client.post("/api/evolution/tasks/t1/resume")
                assert r2.status_code == 200

                # 锚 3 前半：旧线程未终结期间（controller 在注册表），
                # 多轮 tick 后仍不被拾取——双跑窗口归零
                tick_base2 = runner._tick_count
                _wait_for(lambda: runner._tick_count >= tick_base2 + 4,
                          msg="观察窗口")
                row = get_task(db, "t1")
                assert row["status"] == "pending", (
                    "旧线程未终结期间 resume 任务不得被重拾")
                from api.runner import get_active_controllers
                assert "t1" in get_active_controllers(), (
                    "旧线程仍在（controller 未 pop）")
                assert not old_fut.done(), "旧线程仍在跑（挂起中）"

                # 释放：旧线程当代完成→代末检查点 pause 退出
                gate.set()
                _wait_for(lambda: old_fut.done(), timeout=30,
                          msg="旧线程未在时限内终结")
                # 锚 2：当代完整落库（末代 history/snapshot 记录在场）
                conn = sqlite3.connect(db)
                n_hist = conn.execute(
                    "SELECT COUNT(*) FROM evolution_history "
                    "WHERE task_id = 't1'").fetchone()[0]
                n_snap = conn.execute(
                    "SELECT COUNT(*) FROM generation_snapshot "
                    "WHERE task_id = 't1'").fetchone()[0]
                conn.close()
                assert n_hist >= 1 and n_snap >= 1, (
                    "pause 退出前当代记录必须完整落库")
                assert any("当代落库后暂停退出" in rec.message
                           for rec in caplog.records), (
                    "静默终结分支日志在场")
                assert "t1" not in get_active_controllers(), (
                    "线程终结后 controller pop（两集合收敛）")

                # 锚 3 后半+锚 1：旧流终结后被重拾。重拾后评估替身直通
                # 三代毫秒级跑完——running 瞬态不可等，以 completed 为
                # 重拾已发生的证据（pause 分支不写状态，第一轮退出时
                # DB=pending——completed 只能来自第二轮完整重跑）
                _wait_for(
                    lambda: get_task(db, "t1")["status"] == "completed",
                    timeout=30, msg="旧线程终结后未重拾重跑")
                assert old_fut.done(), "重拾发生时旧执行流已终结（锚 1）"
                submit_logs = [rec for rec in caplog.records
                               if "已提交线程池" in rec.message]
                assert len(submit_logs) >= 2, (
                    "两轮提交各留一次日志（重拾证据）")
        finally:
            gate.set()
            for p in patches:
                p.stop()

    def test_pause_then_stop_stop_wins(self, app_env):
        """锚 4：pause 后立即 stop——终态 stopped（非 paused），stop
        优先级不变（代内 check_stop 先于代末 check_pause）。"""
        db, client, runner = (app_env["db"], app_env["client"],
                              app_env["runner"])
        gate = threading.Event()
        patches = self._install_gate_stub(runner, gate,
                                          wake_on_stop=True)
        try:
            _preset_task(db, "t1", max_generations=3)
            _wait_for(lambda: get_task(db, "t1")["status"] == "running",
                      msg="t1 未启动")
            assert client.post(
                "/api/evolution/tasks/t1/pause").status_code == 200
            r = client.post("/api/evolution/tasks/t1/stop")
            assert r.status_code == 200, (
                "stop 守护含 paused——pause 后 stop 合法")
            gate.set()  # stop 信号已置位（替身 wake_on_stop 已可自醒）
            _wait_for(
                lambda: get_task(db, "t1")["status"] == "stopped",
                timeout=30, msg="未在时限内停止")
            row = get_task(db, "t1")
            assert row["status"] == "stopped", "stop 优先：终态非 paused"
            assert row["stop_reason"] == "user_stop"
        finally:
            gate.set()
            for p in patches:
                p.stop()

    def test_runner_chinese_log_tokens(self, tmp_path, caplog):
        """锚 5（R-08）：三处调度日志中文 token 在场——启动/轮询异常/
        提交线程池。"""
        import logging
        from unittest.mock import patch
        from api.runner import EvolutionRunner
        from core.persistence.repository import evolution as repo

        db = tmp_path / "logt.db"
        init_db_ext(db)
        runner = EvolutionRunner(db_path=db, data_dir=tmp_path,
                                 poll_interval=0.05)
        orig = repo.find_pending_task_excluding
        state = {"errs": 0}

        def flaky(*a, **kw):
            if state["errs"] < 1:
                state["errs"] += 1
                raise RuntimeError("构造轮询异常")
            return orig(*a, **kw)

        with caplog.at_level(logging.INFO, logger="RUNNER"), \
                patch("core.persistence.repository.evolution."
                      "find_pending_task_excluding", flaky), \
                patch.object(runner, "_evaluate_population",
                             lambda pop, *a, **kw: [
                                 _stub_score(d) for d in pop]), \
                patch.object(runner, "_evaluate_dna",
                             lambda ind, *a, **kw: {
                                 "score": _stub_score(ind),
                                 "fitness": _stub_score(ind),
                                 "qualified": False, "raw_metrics": {},
                                 "dimension_scores": {},
                                 "total_trades": 0}):
            _preset_task(db, "t1", max_generations=1)
            runner.start()
            _wait_for(lambda: get_task(db, "t1")["status"]
                      in ("completed", "stopped"), timeout=30,
                      msg="任务未终态")
            runner.stop()
            runner.join(timeout=10)

        msgs = " | ".join(rec.message for rec in caplog.records)
        assert "进化调度线程已启动" in msgs
        assert "轮询周期执行异常" in msgs, "构造的 tick 异常应有中文日志"
        assert "已提交线程池" in msgs


class TestSQLiteWriteSafetyGate:
    """A5 写安全闸门（实施证据测试面固化——口径对齐实施闸门：4 线程
    并发写零冲突）。经 orm engine_for 真实连接（WAL+busy_timeout 配置
    面即被测——core/persistence/orm/engine.py 统一 PRAGMA）。"""

    def test_concurrent_snapshot_writes_zero_conflict(self, tmp_path):
        from core.persistence.repository.evolution import (
            save_snapshot, update_current_generation,
        )

        db = tmp_path / "gate.db"
        init_db_ext(db)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO evolution_task (task_id, status, target_score, "
            "score_template, symbol, timeframe, initial_dna, "
            "max_generations, population_size, created_at, updated_at) "
            "VALUES ('gate-1', 'running', 5, 'default', 'BTCUSDT', '4h', "
            "'{}', 10, 5, '2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        conn.commit()
        conn.close()

        n_threads, per_thread = 4, 200  # 800 行——对齐实施闸门口径
        errors: list = []

        def writer(tid: int):
            try:
                for i in range(per_thread):
                    gen = tid * per_thread + i
                    save_snapshot(
                        db, "gate-1", gen, best_score=float(gen),
                        avg_score=float(gen), best_dna=make_dna(),
                        population=[])
                    update_current_generation(db, "gate-1", gen)
            except Exception as exc:  # noqa: BLE001 记录即断言材料
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t,))
                   for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not any(t.is_alive() for t in threads), "写线程未在时限内结束"
        assert errors == [], f"并发写出现冲突/异常: {errors}"

        conn = sqlite3.connect(db)
        n = conn.execute(
            "SELECT COUNT(*) FROM generation_snapshot "
            "WHERE task_id = 'gate-1'").fetchone()[0]
        cur = conn.execute(
            "SELECT current_generation FROM evolution_task "
            "WHERE task_id = 'gate-1'").fetchone()[0]
        conn.close()
        assert n == n_threads * per_thread, f"快照行数 {n} ≠ 800（丢写）"
        assert cur is not None and cur >= 0, "任务行并发更新后不可空"
