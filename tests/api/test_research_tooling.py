"""研究工具与数据质量核心验证（research-tooling-data-quality
3.2/3.3/3.4/3.5/3.6——tester 锚 C3-A/B/C/D 组核心）。

- 并发模型：占槽/上限 2/饥饿消除/状态机无交叉污染（主路径——闸门
  WAL+busy_timeout 并发写实证通过）
- MCP 六工具薄封装：停止三件透传错误码/champion 经快照通道/标注
  PUT/tags 字符串直通/批量删除 confirm 契约
- REST 批量：source_task_id 过滤+confirm 三态+幂等+非目标零误删
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from tests.helpers.data_factory import make_dna


# ── 3.3 并发模型（卡 B）────────────────────────────────────────────


class TestConcurrencyModel:
    def _mk_runner(self, tmp_path, max_workers=2):
        from api.runner import EvolutionRunner
        db = tmp_path / "q.db"
        init_db_ext(db)
        return EvolutionRunner(db_path=db, data_dir=tmp_path,
                               poll_interval=0.05, max_workers=max_workers)

    def test_status_three_keys(self, tmp_path):
        """⑤披露三键：max_workers/active_count/queued_count。"""
        r = self._mk_runner(tmp_path)
        s = r.get_status()
        assert s["max_workers"] == 2
        assert s["active_count"] == 0 and s["queued_count"] == 0

    def test_max_workers_cap(self, tmp_path):
        """上限 4 钳制：越界配置收顶。"""
        r = self._mk_runner(tmp_path, max_workers=10)
        assert r.max_workers == 4
        r2 = self._mk_runner(tmp_path, max_workers=0)
        assert r2.max_workers == 1

    def test_pool_submission_and_slots(self, tmp_path):
        """①占槽与上限：池内 2 任务占满——active_count=2 观察。"""
        r = self._mk_runner(tmp_path, max_workers=2)
        r._pool = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(max_workers=2)
        # 人为挂起两任务占满槽
        gate = threading.Event()
        def _hang(task_row):
            gate.wait(timeout=5)
        for tid in ("t1", "t2"):
            fut = r._pool.submit(_hang, {"task_id": tid})
            with r._lock:
                r._futures[tid] = fut
        try:
            s = r.get_status()
            assert s["active_count"] == 2, "两任务在飞占满槽"
            assert s["max_workers"] == 2
        finally:
            gate.set()
            r._pool.shutdown(wait=True)

    def test_tick_cleans_done_futures(self, tmp_path):
        """终态释放：done future 被 _tick 清理。"""
        import concurrent.futures as cf
        r = self._mk_runner(tmp_path)
        r._pool = cf.ThreadPoolExecutor(max_workers=2)
        fut = r._pool.submit(lambda: None)
        fut.result()
        with r._lock:
            r._futures["done-task"] = fut
        r._tick()
        with r._lock:
            assert "done-task" not in r._futures
        r._pool.shutdown(wait=True)


def _mk_task_row(task_id: str, status: str) -> str:
    """evolution_task 最小合法行（NOT NULL 无默认列全供给）。"""
    return (
        "INSERT INTO evolution_task (task_id, status, target_score, "
        "score_template, symbol, timeframe, initial_dna, "
        "max_generations, population_size, created_at, updated_at) "
        f"VALUES ('{task_id}', '{status}', 5, 'default', 'BTCUSDT', "
        f"'4h', '{{}}', 10, 5, "
        "'2026-01-01T00:00:00', '2026-01-01T00:00:00')")


# ── 3.2/3.4/3.6 MCP 工具（卡 A）──────────────────────────────────


class TestMCPTools:
    @pytest.fixture()
    def env(self, tmp_path):
        from fastapi.testclient import TestClient
        from api.app import create_app
        db = tmp_path / "q.db"
        init_db_ext(db)
        import pandas as pd
        import numpy as np
        d = tmp_path / "market"
        d.mkdir()
        n = 60
        close = 100 + np.cumsum(np.random.default_rng(3).normal(0, 1, n))
        pd.DataFrame({
            "open": close * 0.999, "high": close * 1.005,
            "low": close * 0.995, "close": close,
            "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp")
        ).to_parquet(d / "BTCUSDT_4h.parquet")
        app = create_app(db_path=db, data_dir=d)
        with TestClient(app) as c:
            yield {"client": c, "db": db}

    def _mcp_fn(self, env, name):
        """FakeMCP 收集器形制：注册器把工具收进命名空间后直取（沿
        tests/mcpserver 既有 _FakeMCP 先例——decorator 捕获闭包 fn）。"""
        from mcpserver import tools as tools_mod

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
            def put(self, url, body):
                r = self._c.put(url, json=body)
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
        tools_mod.register_research_tools(fake, _HttpApi(env["client"]))
        return fake.registry[name]

    def test_stop_task_not_found_404(self, env):
        """③错误通道：stop 不存在任务→结构化 {"error":...}（404 透传）。"""
        fn = self._mcp_fn(env, "stop_evolution_task")
        out = fn(task_id="no_such_task")
        assert "error" in out, out
        # ApiError 经 _raise_api_error：detail 非结构化 dict（FastAPI
        # 纯文本 detail）→ API_INTERNAL_ERROR 形制但 message 含 404
        assert "404" in str(out["error"].get("message", "")) or \
            out["error"].get("detail") == "Task not found", out

    def test_stop_terminal_409(self, env):
        """③状态不允许 409：completed 任务 stop→409 透传。"""
        c = env["client"]
        conn = sqlite3.connect(env["db"])
        conn.execute(_mk_task_row("et_done", "completed"))
        conn.commit(); conn.close()
        fn = self._mcp_fn(env, "stop_evolution_task")
        out = fn(task_id="et_done")
        assert "error" in out, out
        assert "409" in str(out["error"].get("message", "")), out

    def test_pause_not_running_409(self, env):
        fn = self._mcp_fn(env, "pause_evolution_task")
        out = fn(task_id="et_never")
        assert "error" in out, out

    def test_resume_not_paused_400(self, env):
        """③resume 端点既有码 400（与 stop/pause 的 409 分立）。"""
        c = env["client"]
        conn = sqlite3.connect(env["db"])
        conn.execute(_mk_task_row("et_run", "running"))
        conn.commit(); conn.close()
        fn = self._mcp_fn(env, "resume_evolution_task")
        out = fn(task_id="et_run")
        assert "error" in out, out
        assert "400" in str(out["error"].get("message", "")) or \
            "resume" in str(out["error"].get("message", "")).lower(), out

    def test_get_strategies_snapshot_channel(self, env):
        """④champion 经快照通道：running 任务快照 top10 可取（无 champion
        条目=运行中两态正确）。"""
        conn = sqlite3.connect(env["db"])
        conn.execute(_mk_task_row("et_run2", "running"))
        dna = make_dna().to_json()
        conn.execute(
            "INSERT INTO generation_snapshot (task_id, generation, "
            "best_score, avg_score, best_dna, population_json, "
            "created_at) VALUES ('et_run2', 1, 3.5, 3.5, ?, '{}', "
            "'2026-01-01T01:00:00')", (dna,))
        conn.commit(); conn.close()
        fn = self._mcp_fn(env, "get_evolution_strategies")
        out = fn(task_id="et_run2")
        assert out["task_id"] == "et_run2"
        snaps = [s for s in out["strategies"] if s["source"] == "snapshot"]
        assert len(snaps) == 1 and snaps[0]["score"] == 3.5
        # 运行中无 champion 条目（终态兜底通道空——两态正确）
        assert not [s for s in out["strategies"] if s["source"] == "champion"]

    def test_update_metadata_tags_string_passthrough(self, env):
        """⑥tags 字符串直通：REST 形态是什么 MCP 暴露什么。"""
        save_strategy(env["db"], strategy_id="st_meta",
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="s")
        fn = self._mcp_fn(env, "update_strategy_metadata")
        out = fn(strategy_id="st_meta", tags="alpha,beta",
                 notes="复验标注")
        assert "error" not in out
        g = env["client"].get("/api/strategies/st_meta").json()
        assert g["tags"] == "alpha,beta", "字符串形制保持不拆数组"
        assert g["notes"] == "复验标注"

    def test_update_metadata_partial(self, env):
        """可选参数缺省=不动：只改 notes，name/tags 保持。"""
        save_strategy(env["db"], strategy_id="st_p2",
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="原名",
                      tags="keep")
        fn = self._mcp_fn(env, "update_strategy_metadata")
        fn(strategy_id="st_p2", notes="仅注")
        g = env["client"].get("/api/strategies/st_p2").json()
        assert g["name"] == "原名" and g["tags"] == "keep"
        assert g["notes"] == "仅注"


# ── 3.5 REST 批量管理（卡 D）───────────────────────────────────────


class TestBatchManagement:
    @pytest.fixture()
    def seeded(self, tmp_path):
        from fastapi.testclient import TestClient
        from api.app import create_app
        db = tmp_path / "q.db"
        init_db_ext(db)
        # 任务 A 三产物+任务 B 两产物（非目标）
        # params.period 变体（进 gene_signature——entry threshold 不
        # 入签名，TD-14 同签名分流先例；用 period 保五产物互异）
        for i, (sid, task) in enumerate([
                ("st_a1", "taskA"), ("st_a2", "taskA"), ("st_a3", "taskA"),
                ("st_b1", "taskB"), ("st_b2", "taskB")]):
            d = make_dna()
            for g in d.signal_genes:
                g.params = {"period": 14 + i}
            save_strategy(db, strategy_id=sid,
                          dna_json=d.to_json(),
                          symbol="BTCUSDT", timeframe="4h",
                          name=sid, source="evolution",
                          source_task_id=task)
        import pandas as pd
        import numpy as np
        d = tmp_path / "market"; d.mkdir()
        n = 30
        close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 1, n))
        pd.DataFrame({"open": close, "high": close, "low": close,
                      "close": close, "volume": np.full(n, 1.0)},
                     index=pd.date_range("2024-01-01", periods=n,
                                         freq="4h", tz="UTC",
                                         name="timestamp")
                     ).to_parquet(d / "BTCUSDT_4h.parquet")
        app = create_app(db_path=db, data_dir=d)
        with TestClient(app) as c:
            yield {"client": c, "db": db}

    def test_source_task_id_filter(self, seeded):
        """①过滤参数：source_task_id=taskA → 恰 3 行。"""
        r = seeded["client"].get("/api/strategies",
                                 params={"source_task_id": "taskA"})
        items = r.json()["items"]
        assert len(items) == 3
        assert {x["strategy_id"] for x in items} == {"st_a1", "st_a2",
                                                     "st_a3"}

    def test_batch_delete_confirm_three_states(self, seeded):
        """②确认三态：无 confirm→422+预览数；confirm=true→删除+计数；
        幂等复调→deleted_count=0。"""
        c = seeded["client"]
        r1 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "taskA"})
        assert r1.status_code == 422
        d = r1.json()["detail"]["detail"]
        assert d["matched_count"] == 3
        assert d["criteria"]["source_task_id"] == "taskA"
        r2 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "taskA", "confirm": True})
        assert r2.status_code == 200
        assert r2.json()["deleted_count"] == 3
        # 幂等
        r3 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "taskA", "confirm": True})
        assert r3.json()["deleted_count"] == 0
        # ③非目标零误删（逐行核对）
        g = c.get("/api/strategies",
                  params={"source_task_id": "taskB"})
        assert {x["strategy_id"] for x in g.json()["items"]} == \
            {"st_b1", "st_b2"}

    def test_mcp_batch_delete_mirror(self, seeded):
        """④MCP 面同形制。"""
        from mcpserver import tools as tools_mod

        class _HttpApi:
            def __init__(self, c):
                self._c = c
            def get(self, url, params=None):
                return self._c.get(url, params=params).json()
            def post(self, url, body):
                r = self._c.post(url, json=body)
                if r.is_error:
                    from mcpserver.api_client import _raise_api_error
                    _raise_api_error(r)
                return r.json()
            def put(self, url, body):
                return self._c.put(url, json=body).json()

        class _FakeMCP:
            def __init__(self):
                self.registry = {}
            def tool(self):
                def deco(fn):
                    self.registry[fn.__name__] = fn
                    return fn
                return deco

        fake = _FakeMCP()
        tools_mod.register_research_tools(fake, _HttpApi(seeded["client"]))
        out = fake.registry["batch_delete_strategies"](
            source_task_id="taskB", confirm=False)
        assert out["error"]["code"] == "BATCH_DELETE_CONFIRM_REQUIRED"
        out2 = fake.registry["batch_delete_strategies"](
            source_task_id="taskB", confirm=True)
        assert out2["deleted_count"] == 2

    def test_source_task_id_required(self, seeded):
        """⑤source_task_id 必填（无圈定不提供批量删除）。"""
        r = seeded["client"].request("POST", "/api/strategies/batch",
                                     json={"confirm": True})
        assert r.status_code == 422
