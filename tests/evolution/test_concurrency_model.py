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


def _preset_task(db: Path, task_id: str, *, max_generations: int = 2,
                 continuous: int = 0) -> None:
    """预置 pending 任务并压缩代数（缩短端到端时长；continuous 可开
    连续进化——CARD-04 种群锚用）。"""
    save_task(db, task_id=task_id, target_score=80.0, template="profit_first",
              symbol="BTCUSDT", timeframe="4h", initial_dna=make_dna())
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE evolution_task SET max_generations = ?, continuous = ? "
        "WHERE task_id = ?", (max_generations, continuous, task_id))
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
                # CARD-06 行为锚：resume 语义=paused→pending 重新排队
                # （完整重跑非断点续跑——重拾重跑已由本用例后半验证）
                assert r2.json()["status"] == "pending"

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


class TestQueueStopGuard:
    """CARD-02 排队任务 stop 后复活锚（review4_cards.md CARD-02）。

    修复双层：_execute_task 接手写 expected_statuses=("pending",) 守护
    （主修——stop 先写则放弃执行）+_tick 剔除登记时 fut.cancel()（增强
    ——排队未开跑可取消）。锚按「stop 先写、任务后开跑」状态面构造
    （现 while 守卫下自然排队时序不可达，防御对象是任意进入该状态的
    路径——两层各锚一面）。
    """

    @pytest.fixture()
    def app_env(self, tmp_path):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from api.app import create_app
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv

        db = tmp_path / "qs.db"
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

    def _install_gate_stub(self, runner, gate: threading.Event):
        from unittest.mock import patch

        def fake_pop(pop, *a, **kw):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if gate.is_set():
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

    def test_queued_task_stop_never_starts_and_next_fills(self, app_env):
        """主锚：双 worker 占满+第 3 任务真实排队（手工 submit——池
        排队语义真实）→stop（200+stopped）→tick 剔除+cancel 生效→
        释放槽后第 3 任务永不开跑（stopped 保持+零 history）+第 4 任务
        正常补位。task_started 推送在 _execute_task 接手写之后——
        status stopped 保持+零 history 即证明未执行（推送未发）。"""
        db, client, runner = (app_env["db"], app_env["client"],
                              app_env["runner"])
        gate = threading.Event()
        patches = self._install_gate_stub(runner, gate)
        try:
            _preset_task(db, "t1")
            _preset_task(db, "t2")
            _wait_for(lambda: get_task(db, "t1")["status"] == "running"
                      and get_task(db, "t2")["status"] == "running",
                      msg="t1/t2 未占满双 worker")

            # 第 3 任务入池排队（DB pending+future not done——worker
            # 全忙，submit 后真实排队）
            _preset_task(db, "t3")
            t3_row = dict(get_task(db, "t3"))
            fut3 = runner._pool.submit(runner._run_task, t3_row)
            with runner._lock:
                runner._futures["t3"] = fut3
            assert not fut3.done() and not fut3.cancelled(), (
                "排队态确认（worker 全忙——future 在池队列）")
            assert get_task(db, "t3")["status"] == "pending", (
                "排队期 DB 应保持 pending（_run_task 未执行）")

            # stop 排队中的 t3：守护含 pending→200+stopped
            r = client.post("/api/evolution/tasks/t3/stop")
            assert r.status_code == 200
            assert r.json()["status"] == "stopped"
            # tick 校验段剔除登记+cancel（排队态 cancel 应生效）
            tick_base = runner._tick_count
            _wait_for(lambda: runner._tick_count >= tick_base + 3,
                      msg="等待剔除+cancel 窗口")
            _wait_for(fut3.cancelled, timeout=5,
                      msg="排队 future 应被 cancel（增强层生效）")

            # 释放槽：t1/t2 自然完成；第 4 任务补位
            gate.set()
            _preset_task(db, "t4")
            _wait_for(lambda: get_task(db, "t4")["status"] == "completed",
                      timeout=60, msg="第 4 任务未正常补位完成")

            # 主断言：t3 永不开跑——stopped 保持+零 history 落库
            row = get_task(db, "t3")
            assert row["status"] == "stopped", (
                f"排队期被 stop 的任务不得复活: {row['status']}")
            assert row["stop_reason"] == "user_stop"
            conn = sqlite3.connect(db)
            n_hist = conn.execute(
                "SELECT COUNT(*) FROM evolution_history "
                "WHERE task_id = 't3'").fetchone()[0]
            conn.close()
            assert n_hist == 0, "零 history=任务从未开跑（未复活）"
            for tid in ("t1", "t2"):
                assert get_task(db, tid)["status"] == "completed"
        finally:
            gate.set()
            for p in patches:
                p.stop()

    def test_takeover_guard_stopped_before_start(self, tmp_path, caplog):
        """守护直锚（主修独立面）：stop 先写 stopped、任务后开跑的序
        ——直调 _run_task，接手写守护拒绝覆写，任务放弃执行+warning
        日志+controller 无残留。"""
        import logging
        from unittest.mock import patch
        from api.runner import EvolutionRunner, get_active_controllers
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv

        db = tmp_path / "guard.db"
        init_db_ext(db)
        runner = EvolutionRunner(db_path=db, data_dir=tmp_path,
                                 poll_interval=0.05)
        _preset_task(db, "g1", max_generations=2)
        # 模拟排队期 stop 先写（stop 通道守护含 pending→可写 stopped）
        from core.persistence.db import update_task
        assert update_task(db, "g1", status="stopped",
                           stop_reason="user_stop",
                           expected_statuses=("running", "paused", "pending"))
        t_row = dict(get_task(db, "g1"))

        enhanced = compute_all_indicators(make_ohlcv(120, "4h"))
        with patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=enhanced), \
             patch("api.runner_progress._push_ws"), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [
                              _stub_score(d) for d in pop]), \
             patch.object(runner, "_evaluate_dna",
                          lambda ind, *a, **kw: {
                              "score": _stub_score(ind),
                              "fitness": _stub_score(ind),
                              "qualified": False, "raw_metrics": {},
                              "dimension_scores": {},
                              "total_trades": 0}), \
             caplog.at_level(logging.WARNING, logger="RUNNER"):
            runner._run_task(t_row)

        row = get_task(db, "g1")
        assert row["status"] == "stopped", (
            f"接手写守护拒绝覆写终态: {row['status']}")
        conn = sqlite3.connect(db)
        n_hist = conn.execute(
            "SELECT COUNT(*) FROM evolution_history "
            "WHERE task_id = 'g1'").fetchone()[0]
        conn.close()
        assert n_hist == 0, "放弃执行——零 history"
        assert any("接手前已被外部终态化" in rec.message
                   for rec in caplog.records), "守护 warning 留痕"
        assert "g1" not in get_active_controllers(), "controller 无残留"


class TestConfigEffectChain:
    """CARD-03 config 生效链锚（review4_cards.md CARD-03）。

    修复三处：project_root 无条件求值（显式传参路径 lifespan 不再
    NameError——config 真实生效）+except 收窄留痕+runner 侧 int 类型
    容错。锚用 spy/monkeypatch 注入可区分值（卡面明示：2 不可区分
    缺省，3 才证生效）。容错场景按值层形制构造（修复设计与卡面现象
    节均为「config 值类型错」——探针实证文档层裸 str 在 app.py or
    链 AttributeError 属修复外场景，已另报）。
    """

    def _make_app(self, tmp_path):
        from api.app import create_app
        db = tmp_path / "cfg.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        return create_app(db_path=db, data_dir=d)

    def test_explicit_params_config_takes_effect(self, tmp_path, monkeypatch):
        """生效链锚（R-04）：显式传参路径下 spy 注入可区分值 3——
        config.yaml 被打开（safe_load 被调）且值进入 runner
        （max_workers==3——修复前 NameError 静默吞掉恒 2）。"""
        import yaml
        from fastapi.testclient import TestClient

        calls = {"n": 0}

        def spy_load(f):
            calls["n"] += 1
            return {"evolution": {"max_concurrent_tasks": 3}}

        monkeypatch.setattr(yaml, "safe_load", spy_load)
        app = self._make_app(tmp_path)
        with TestClient(app):
            runner = app.state.evolution_runner
            assert calls["n"] >= 1, (
                "显式传参路径 config.yaml 须真实被打开（R-04 修复面）")
            assert runner.max_workers == 3, (
                "可区分值 3 须进入 runner（2 不可区分缺省——锚有效性关键）")

    @pytest.mark.parametrize("bad_value", [
        pytest.param("two", id="值层字符串-ValueError 路径"),
        pytest.param(None, id="值层空值-TypeError 路径"),
    ])
    def test_bad_type_falls_back_without_crash(
            self, tmp_path, monkeypatch, bad_value):
        """容错锚（R-05）：config 值类型错（"two"/None）不启动崩溃，
        回退缺省 2+warning 留痕（runner 侧容错——修复设计 3）。捕获用
        自备 handler（root 捕获顺序敏感——见 read_failure 用例注）。"""
        import logging
        import yaml
        from fastapi.testclient import TestClient

        class _ListHandler(logging.Handler):
            def __init__(self):
                super().__init__(level=logging.WARNING)
                self.records = []

            def emit(self, record):
                self.records.append(record)

        handler = _ListHandler()
        runner_logger = logging.getLogger("RUNNER")
        runner_logger.addHandler(handler)
        try:
            monkeypatch.setattr(
                yaml, "safe_load",
                lambda f: {"evolution": {
                    "max_concurrent_tasks": bad_value}})
            app = self._make_app(tmp_path)
            with TestClient(app) as client:
                assert client.get(
                    "/api/evolution/runner-status").status_code == 200, (
                    "容错后服务正常起（不启动崩溃）")
                runner = app.state.evolution_runner
                assert runner.max_workers == 2, "类型错回退缺省 2"
        finally:
            runner_logger.removeHandler(handler)
        assert any("类型非法" in rec.getMessage()
                   and "回退缺省 2" in rec.getMessage()
                   for rec in handler.records), "容错 warning 须留痕"

    def test_read_failure_degrades_with_warning(self, tmp_path, monkeypatch):
        """except 收窄面：config.yaml 读取失败（OSError）降级缺省 2+
        warning 留痕（修复前 except Exception 静默吞掉零痕迹）。
        捕获用直挂「app」logger 的自备 handler——被测初始化链会清换
        root handlers，caplog 的 root 捕获时序顺序敏感不可靠。"""
        import logging
        from fastapi.testclient import TestClient

        real_open = open  # patch 前持真实引用（透传用——防自递归）

        def no_open(path, *a, **kw):
            if str(path).endswith("config.yaml"):
                raise OSError("构造读取失败")
            return real_open(path, *a, **kw)

        class _ListHandler(logging.Handler):
            def __init__(self):
                super().__init__(level=logging.WARNING)
                self.records = []

            def emit(self, record):
                self.records.append(record)

        handler = _ListHandler()
        app_logger = logging.getLogger("app")
        app_logger.addHandler(handler)
        try:
            monkeypatch.setattr("builtins.open", no_open)
            app = self._make_app(tmp_path)
            with TestClient(app) as client:
                assert client.get(
                    "/api/evolution/runner-status").status_code == 200
                assert app.state.evolution_runner.max_workers == 2
        finally:
            app_logger.removeHandler(handler)
        assert any("config.yaml 读取失败" in rec.getMessage()
                   and "用缺省 2" in rec.getMessage()
                   for rec in handler.records), "读取失败 warning 须留痕"

    def test_document_layer_scalar_falls_back(self, tmp_path, monkeypatch):
        """文档层裸标量锚（CARD-03 附带项——team-lead 2026-10-28 裁决
        并入）：safe_load 返回非 dict 文档（如 config.yaml 内容就一个
        two）——不启动崩溃+缺省 2+留痕（修复前 `_cfg.get` AttributeError
        致 lifespan 崩）。"""
        import logging
        import yaml
        from fastapi.testclient import TestClient

        class _ListHandler(logging.Handler):
            def __init__(self):
                super().__init__(level=logging.WARNING)
                self.records = []

            def emit(self, record):
                self.records.append(record)

        handler = _ListHandler()
        app_logger = logging.getLogger("app")
        app_logger.addHandler(handler)
        try:
            monkeypatch.setattr(yaml, "safe_load", lambda f: "two")
            app = self._make_app(tmp_path)
            with TestClient(app) as client:
                assert client.get(
                    "/api/evolution/runner-status").status_code == 200, (
                    "文档层类型错须降级起服（不启动崩溃）")
                assert app.state.evolution_runner.max_workers == 2, (
                    "文档层类型错回退缺省 2")
        finally:
            app_logger.removeHandler(handler)
        assert any("文档层非映射" in rec.getMessage()
                   and "用缺省 2" in rec.getMessage()
                   for rec in handler.records), "文档层容错 warning 须留痕"




class TestPopulationOffsetFix:
    """CARD-08 种群覆盖修复锚（review4_cards.md CARD-08——P0）。

    修复：global_gen_offset 累加从循环体尾移至体首——种群 k 落
    [S_{k-1}+1, S_k] 连续无覆盖（修复前种群 2 与种群 1 同 gen 槽，
    INSERT OR REPLACE 覆盖致种群 1 数据永久丢失）。
    """

    @pytest.fixture()
    def app_env(self, tmp_path):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from api.app import create_app
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv

        db = tmp_path / "off.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        app = create_app(db_path=db, data_dir=d)
        enhanced = compute_all_indicators(make_ohlcv(120, "4h"))
        with patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=enhanced), \
             TestClient(app) as client:
            runner = app.state.evolution_runner
            yield {"client": client, "db": db, "runner": runner}

    def _install_slow_stub(self, runner, per_call: float = 0.01):
        """慢速替身：受控节奏延迟制造切换观察窗（current_generation
        回爬须轮询捕获——种群切换毫秒级会漏点）；分数单调递减
        （counter 驱动——种群 1 的代恒为全局最高分，champion 补强锚用）。"""
        from unittest.mock import patch

        counter = {"n": 0}

        def slow_pop(pop, *a, **kw):
            counter["n"] += 1
            time.sleep(per_call)
            return [100.0 - counter["n"] * 0.5] * len(pop)

        def fake_dna(ind, *a, **kw):
            score = _stub_score(ind)
            return {"score": score, "fitness": score, "qualified": False,
                    "raw_metrics": {}, "dimension_scores": {},
                    "total_trades": 0}

        p1 = patch.object(runner, "_evaluate_population", slow_pop)
        p2 = patch.object(runner, "_evaluate_dna", fake_dna)
        p1.start()
        p2.start()
        return [p1, p2]

    def test_core_gen_continuity_and_no_regression(self, app_env):
        """核心锚+current_generation 锚：≥2 种群后 history gen 从 1
        连续至 N1+N2 无空洞+pop=1 行在场+snapshot 行数==代数；
        current_generation 全程单调不减（修复前种群 2 首代从 N1
        回爬至 1——轮询序列抓回退点）。"""
        db, client = app_env["db"], app_env["client"]
        runner = app_env["runner"]
        patches = self._install_slow_stub(runner, per_call=0.008)
        try:
            _preset_task(db, "oc1", max_generations=3, continuous=1)
            seen_cg = []
            _wait_for(
                lambda: 2 in self._pop_pops(db, "oc1"),
                timeout=30, msg="未跑入第 2 种群")
            # 持续观察 current_generation（覆盖种群切换窗）
            for _ in range(30):
                row = get_task(db, "oc1")
                seen_cg.append(
                    row["current_generation"]
                    if row["current_generation"] is not None else 0)
                time.sleep(0.005)
            assert client.post(
                "/api/evolution/tasks/oc1/stop").status_code == 200
            _wait_for(lambda: get_task(db, "oc1")["status"] == "stopped",
                      timeout=30, msg="未停止")

            # 核心锚：gen 从 1 连续无空洞+pop=1 在场+snapshot==history
            conn = sqlite3.connect(db)
            gens = [r[0] for r in conn.execute(
                "SELECT generation FROM evolution_history "
                "WHERE task_id = 'oc1' ORDER BY generation")]
            snaps = [r[0] for r in conn.execute(
                "SELECT generation FROM generation_snapshot "
                "WHERE task_id = 'oc1' ORDER BY generation")]
            pops = self._pop_pops(db, "oc1")
            conn.close()
            assert gens and gens[0] == 1, "gen 序列须从 1 起（种群 1 存活）"
            assert gens == list(range(1, len(gens) + 1)), (
                f"gen 须连续无空洞（覆盖即断链）: {gens[:8]}...")
            assert 1 in pops, "pop=1 行在场（种群 1 存活断言）"
            assert snaps == gens, "snapshot 行集==history 行集（同槽双表）"
            assert 2 in pops, "须跑 ≥2 种群"
            # current_generation 锚：单调不减（回爬即断言红）
            assert seen_cg == sorted(seen_cg), (
                f"current_generation 出现回爬: {seen_cg}")
        finally:
            try:
                client.post("/api/evolution/tasks/oc1/stop")
            except Exception:
                pass
            for p in patches:
                p.stop()

    @staticmethod
    def _pop_pops(db, task_id):
        import re
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT top3_summary FROM evolution_history "
            "WHERE task_id = ? ORDER BY generation",
            (task_id,)).fetchall()
        conn.close()
        return [int(re.search(r"pop=(\d+)", s[0]).group(1))
                for s in rows if s[0]]

    def test_push_payload_and_champion_top1(self, app_env):
        """推送锚+champion 快照补强锚：种群 2 population_started 的
        total_generations_so_far==N1（修复前恒 0）；递减分数下全局
        最高分在种群 1 首代——运行中快照 top1 即该代（修复前 gen1
        被种群 2 覆盖，top1=种群 1 次高代——红绿分界）。"""
        from unittest.mock import patch

        db, client = app_env["db"], app_env["client"]
        runner = app_env["runner"]
        pushes = []

        def spy_push(task_id, payload):
            pushes.append(dict(payload))

        patches = self._install_slow_stub(runner, per_call=0.004)
        p3 = patch("api.runner._push_ws", spy_push)
        p3.start()
        try:
            _preset_task(db, "oc2", max_generations=3, continuous=1)
            _wait_for(lambda: 2 in self._pop_pops(db, "oc2"),
                      timeout=30, msg="未跑入第 2 种群")
            # champion 补强锚：种群 2 已跑起（覆盖窗已过）——运行中
            # 快照 top1 应=种群 1 首代（gen=1，全局最高分 99.5）
            r = client.get("/api/evolution/tasks/oc2/strategies")
            body = r.json()
            snaps = [s for s in body["strategies"]
                     if s["source"] == "snapshot"]
            assert snaps, "运行中快照通道可提取"
            assert snaps[0]["generation"] == 1, (
                f"top1 须为种群 1 首代 gen=1（修复前被种群 2 覆盖）: "
                f"top1.gen={snaps[0]['generation']}")
            assert snaps[0]["score"] == pytest.approx(99.5)
            assert client.post(
                "/api/evolution/tasks/oc2/stop").status_code == 200
            _wait_for(lambda: get_task(db, "oc2")["status"] == "stopped",
                      timeout=30, msg="未停止")
            # 推送锚：首个 population_started 的 total_generations_so_far
            # ==N1（max_generations=3）
            starts = [p for p in pushes
                      if p.get("type") == "population_started"]
            assert starts, "population_started 推送在场"
            assert starts[0]["total_generations_so_far"] == 3, (
                f"种群 2 开始时须含上一轮代数（修复前恒 0）: "
                f"{starts[0]['total_generations_so_far']}")
        finally:
            try:
                client.post("/api/evolution/tasks/oc2/stop")
            except Exception:
                pass
            p3.stop()
            for p in patches:
                p.stop()


class TestPopulationCountLocalization:
    """CARD-04 _population_count 局部化锚（review4_cards.md CARD-04）。

    修复：实例属性（线程池并发下跨任务交错污染落库 pop 序号）改任务内
    局部计数（global_gen_offset 同形制）。锚按新语义：各任务 pop 从 1
    起连续递增。反例构造：任务 A 先跑入第 2 种群再提交 B——修复前
    B 首条 pop 承接实例累计（跳变），修复后 B 独立从 1 起。
    """

    @pytest.fixture()
    def app_env(self, tmp_path):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from api.app import create_app
        from core.features.indicators import compute_all_indicators
        from tests.helpers.data_factory import make_ohlcv

        db = tmp_path / "popc.db"
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

    @staticmethod
    def _install_fast_stub(runner):
        """快速评估替身（直通无挂起——种群切换自然快速推进）。"""
        from unittest.mock import patch

        p1 = patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [
                              _stub_score(d) for d in pop])
        p2 = patch.object(runner, "_evaluate_dna",
                          lambda ind, *a, **kw: {
                              "score": _stub_score(ind),
                              "fitness": _stub_score(ind),
                              "qualified": False, "raw_metrics": {},
                              "dimension_scores": {}, "total_trades": 0})
        p1.start()
        p2.start()
        return [p1, p2]

    @staticmethod
    def _pop_series(db, task_id):
        """取该任务 history 的 pop 序号序列（按落库行序）。"""
        import re
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT top3_summary FROM evolution_history "
            "WHERE task_id = ? ORDER BY generation", (task_id,)).fetchall()
        conn.close()
        series = []
        for (summary,) in rows:
            m = re.search(r"pop=(\d+)", summary or "")
            assert m, f"top3_summary 缺 pop 段: {summary!r}"
            series.append(int(m.group(1)))
        return series

    def test_concurrent_pop_series_task_local(self, app_env):
        """双任务并发 pop 序号连续性：A 先入第 2 种群再提交 B——
        B 首条 pop==1（任务内独立计数）；并发窗口内各自序列从 1
        连续递增互不跳变（交错污染反例：B 首条承接 A 的累计值）。"""
        db, client, runner = (app_env["db"], app_env["client"],
                              app_env["runner"])
        patches = self._install_fast_stub(runner)
        try:
            # A 先独占跑：连续进化，等其进入第 2 种群（pop=2 条目在场）
            _preset_task(db, "pa", max_generations=2, continuous=1)
            _wait_for(lambda: 2 in self._pop_series(db, "pa"),
                      timeout=30, msg="任务 A 未跑入第 2 种群")

            # 此刻提交 B（A 已 ≥1 次种群切换）：修复前实例计数已污染，
            # B 首条 pop 将承接累计值（≥2 跳变）；修复后独立从 1 起
            _preset_task(db, "pb", max_generations=2, continuous=1)
            _wait_for(lambda: get_task(db, "pb")["status"] == "running",
                      timeout=30, msg="任务 B 未启动（与 A 并发）")
            _wait_for(lambda: 2 in self._pop_series(db, "pb"),
                      timeout=30, msg="任务 B 未跑入第 2 种群")

            for tid in ("pa", "pb"):
                series = self._pop_series(db, tid)
                distinct = sorted(set(series))
                assert len(distinct) >= 2, f"{tid} 须跑 ≥2 种群"
                # CARD-08 修复后升级口径（原按覆盖缺陷现实从 2 断言）：
                # 种群 1 存活——pop 序列各自从 1 连续递增
                assert distinct == list(range(1, len(distinct) + 1)), (
                    f"{tid} pop 序号须从 1 连续无跳变: {distinct[:10]}...")
            # 互不污染反例锚：B 的首行 pop——修复前承接 A 的实例累计
            # （巨大值）；CARD-04 修复后独立计数、CARD-08 修复后种群 1
            # 存活故首行即自身种群 1（pop=1）
            assert self._pop_series(db, "pb")[0] == 1, (
                "B 首行 pop==1（任务内独立计数+种群 1 存活——双修复面）")
        finally:
            # 防挂死护栏：任何路径退出前先停任务（连续进化不自然终止，
            # 残留 worker 会使池 shutdown 永等）
            for tid in ("pa", "pb"):
                try:
                    client.post(f"/api/evolution/tasks/{tid}/stop")
                except Exception:
                    pass
            for tid in ("pa", "pb"):
                try:
                    _wait_for(
                        lambda t=tid: get_task(db, t)["status"]
                        in ("stopped", "completed", "pending"),
                        timeout=10, msg=f"{tid} 收尾停止")
                except AssertionError:
                    pass
            for p in patches:
                p.stop()
