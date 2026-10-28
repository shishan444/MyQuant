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
