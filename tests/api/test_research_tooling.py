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


# ── C3-C 补齐组（tester 建网：confirm false 态三键/收紧参数/单删归档
#    回归/导出数量对称——v2 字段级细则 C1/C2/C3/C4）──────────────────


class TestBatchContractExtras:
    @pytest.fixture()
    def seeded(self, tmp_path):
        from fastapi.testclient import TestClient
        from api.app import create_app
        db = tmp_path / "q2.db"
        init_db_ext(db)
        # taskC 五产物：代数升/分数降交错；taskD 一产物（旁观者）
        for i, (sid, gen, sc) in enumerate([
                ("st_c1", 1, 5.0), ("st_c2", 2, 4.0), ("st_c3", 3, 3.0),
                ("st_c4", 4, 2.0), ("st_c5", 5, 1.0)]):
            d = make_dna()
            for g in d.signal_genes:
                g.params = {"period": 14 + i}
            save_strategy(db, strategy_id=sid, dna_json=d.to_json(),
                          symbol="BTCUSDT", timeframe="4h", name=sid,
                          source="evolution", source_task_id="taskC",
                          generation=gen, best_score=sc)
        d = make_dna()
        for g in d.signal_genes:
            g.params = {"period": 99}
        save_strategy(db, strategy_id="st_d1", dna_json=d.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="st_d1",
                      source="evolution", source_task_id="taskD")
        import pandas as pd
        import numpy as np
        dd = tmp_path / "market"
        dd.mkdir()
        n = 30
        close = 100 + np.cumsum(np.random.default_rng(2).normal(0, 1, n))
        pd.DataFrame({"open": close, "high": close, "low": close,
                      "close": close, "volume": np.full(n, 1.0)},
                     index=pd.date_range("2024-01-01", periods=n,
                                         freq="4h", tz="UTC",
                                         name="timestamp")
                     ).to_parquet(dd / "BTCUSDT_4h.parquet")
        app = create_app(db_path=db, data_dir=dd)
        with TestClient(app) as c:
            yield {"client": c, "db": db}

    def test_confirm_false_three_keys(self, seeded):
        """C1 补：REST 面 confirm=false 显式态→422+三键全在场
        （matched_count/criteria/hint——预览数使研究者知道将删多少）。"""
        c = seeded["client"]
        r = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "taskC", "confirm": False})
        assert r.status_code == 422
        d = r.json()["detail"]["detail"]
        assert d["matched_count"] == 5
        assert d["criteria"]["source_task_id"] == "taskC"
        assert "confirm=true" in d["hint"], "hint 须含确认指引"
        # 零删除（禁止变化面）
        g = c.get("/api/strategies", params={"source_task_id": "taskC"})
        assert g.json()["total"] == 5

    def test_tightening_filters_preview_matches_execution(self, seeded):
        """C2 补：收紧参数（参数名即严格语义 _gt/_lt）——422 预览数=
        收紧命中数；confirm 后 deleted=同集；不满足收紧的同任务行
        与旁观任务行逐行零变化。"""
        c = seeded["client"]
        # generation_gt=3 → 命中 gen 4/5（st_c4/st_c5）
        r1 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "taskC", "generation_gt": 3})
        assert r1.status_code == 422
        assert r1.json()["detail"]["detail"]["matched_count"] == 2
        assert r1.json()["detail"]["detail"]["criteria"][
            "generation_gt"] == 3
        r2 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "taskC", "generation_gt": 3,
            "confirm": True})
        assert r2.json()["deleted_count"] == 2
        # best_score_lt=3.5 → 命中 st_c3（3.0<3.5；5.0/4.0 不动）
        r3 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "taskC", "best_score_lt": 3.5,
            "confirm": True})
        assert r3.json()["deleted_count"] == 1
        # 逐行核对：taskC 恰剩 st_c1/st_c2；taskD 旁观者零变化
        g = c.get("/api/strategies", params={"source_task_id": "taskC"})
        assert {x["strategy_id"] for x in g.json()["items"]} == \
            {"st_c1", "st_c2"}
        g2 = c.get("/api/strategies", params={"source_task_id": "taskD"})
        assert {x["strategy_id"] for x in g2.json()["items"]} == {"st_d1"}

    def test_single_delete_archive_semantics_unchanged(self, seeded):
        """C3 补：单策略 DELETE 归档语义回归锚——非 draft 删除=归档
        （行保留+status=archived），与批量物理 DELETE 语义分立。"""
        c = seeded["client"]
        r = c.delete("/api/strategies/st_c1")
        assert r.status_code == 204
        g = c.get("/api/strategies/st_c1")
        assert g.status_code == 200, "单删后行保留（归档非物理删）"
        assert g.json()["status"] == "archived"

    def test_export_count_symmetry_before_delete(self, seeded):
        """C4 可确定半：删除前圈定导出（数量摘要）与删除预览数对称
        ——研究者先导出看数量再对称确认。（分数分布摘要：实现面无
        承载，已列偏差上报，裁决后补验。）"""
        c = seeded["client"]
        listed = c.get("/api/strategies",
                       params={"source_task_id": "taskC"}).json()
        assert listed["total"] == 5, "圈定导出数量摘要在场（total）"
        r = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "taskC"})
        preview = r.json()["detail"]["detail"]["matched_count"]
        assert preview == listed["total"], "预览数与导出数量对称"


# ── C3-D4/S11 披露面（六工具 docstring 明示语义与错误面+能力自述
#    新工具条目在场——「docstring 未披露=AI 视角不存在」纪律）──────


class TestDisclosureAndCapability:
    @pytest.fixture()
    def registry(self, tmp_path):
        from fastapi.testclient import TestClient
        from api.app import create_app
        from mcpserver import tools as tools_mod

        class _HttpApi:
            def __init__(self, c):
                self._c = c

            def get(self, url, params=None):
                return self._c.get(url, params=params).json()

            def post(self, url, body):
                return self._c.post(url, json=body).json()

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

        db = tmp_path / "q3.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        app = create_app(db_path=db, data_dir=d)
        with TestClient(app) as c:
            fake = _FakeMCP()
            tools_mod.register_research_tools(fake, _HttpApi(c))
            yield fake.registry

    def test_six_tools_docstring_disclosure(self, registry):
        """D4：六工具 docstring 明示语义与错误面（batch_delete 不可逆
        语义点名）——tool_safe 经 functools.wraps 保留原文。"""
        expect = {
            "stop_evolution_task": ("404", "409"),
            "pause_evolution_task": ("404", "409"),
            "resume_evolution_task": ("404", "400"),
            "get_evolution_strategies": ("404", "top10", "champion"),
            "update_strategy_metadata": ("404", "字符串形制直通"),
            "batch_delete_strategies": ("不可逆", "matched_count",
                                        "幂等"),
        }
        for name, tokens in expect.items():
            doc = registry[name].__doc__ or ""
            for tok in tokens:
                assert tok in doc, f"{name} docstring 未披露 {tok!r}"

    def test_capability_doc_includes_new_tools(self, registry):
        """S11：能力自述工具清单含六新工具条目（运行时从注册集生成）
        ——与 mcp-server 域工具清单一致由 _expected_tools 三处集合
        断言承载（D1）。"""
        from mcpserver.tools import TOOL_TIERS
        from mcpserver.capability import build_capability_doc

        six = {"stop_evolution_task", "pause_evolution_task",
               "resume_evolution_task", "get_evolution_strategies",
               "update_strategy_metadata", "batch_delete_strategies"}
        assert six <= TOOL_TIERS["research"], "六新工具须入 research 档"
        doc = build_capability_doc(sorted(TOOL_TIERS["research"]))
        for n in sorted(six):
            assert f"- {n}" in doc, f"能力自述缺新工具条目 {n}"


# ── CARD-05 batch 端点四合一锚（review4_cards.md CARD-05——score_
#    summary 收口 C4/挂盘预检/pt_task 引用/同事务/审计/结构下沉）────


def _seed_scored(db, task_id: str, scores):
    """构造已知分数集产物（各异 period 防 gene_signature 去重吞行；
    None=best_score NULL 行）。"""
    for i, sc in enumerate(scores):
        d = make_dna()
        for g in d.signal_genes:
            g.params = {"period": 200 + i}
        save_strategy(db, strategy_id=f"st_{task_id}_{i}",
                      dna_json=d.to_json(), symbol="BTCUSDT",
                      timeframe="4h", name=f"st_{task_id}_{i}",
                      source="evolution", source_task_id=task_id,
                      best_score=sc)


class TestBatchHardening:
    @pytest.fixture()
    def env(self, tmp_path):
        from fastapi.testclient import TestClient
        from api.app import create_app
        import pandas as pd
        import numpy as np

        db = tmp_path / "bh.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        n = 30
        close = 100 + np.cumsum(np.random.default_rng(5).normal(0, 1, n))
        pd.DataFrame({"open": close, "high": close, "low": close,
                      "close": close, "volume": np.full(n, 1.0)},
                     index=pd.date_range("2024-01-01", periods=n,
                                         freq="4h", tz="UTC",
                                         name="timestamp")
                     ).to_parquet(d / "BTCUSDT_4h.parquet")
        app = create_app(db_path=db, data_dir=d)
        with TestClient(app) as c:
            yield {"client": c, "db": db}

    def test_score_summary_known_values(self, env):
        """score_summary 锚（C4 收口）：[10,20,30,40,50] inclusive 五数
        逐值+NULL 行 null_count 披露。"""
        c, db = env["client"], env["db"]
        _seed_scored(db, "scA", [10.0, 20.0, 30.0, 40.0, 50.0, None])
        r = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "scA"})
        assert r.status_code == 422
        s = r.json()["detail"]["detail"]["score_summary"]
        assert s["count"] == 5, "count=非 NULL 参与分布数"
        assert s["null_count"] == 1, "NULL 行单独披露"
        assert s["min"] == 10.0 and s["max"] == 50.0
        assert s["p25"] == 20.0, f"inclusive p25 应 20: {s['p25']}"
        assert s["median"] == 30.0
        assert s["p75"] == 40.0, f"inclusive p75 应 40: {s['p75']}"

    def test_score_summary_degenerate_forms(self, env):
        """退化形：空集五数 None；收紧后空集同形；1 行五数全同值。"""
        c, db = env["client"], env["db"]
        _seed_scored(db, "scB", [7.7])
        r0 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "scB",
            "generation_gt": 99})  # 收紧后空集
        s0 = r0.json()["detail"]["detail"]["score_summary"]
        assert s0 == {"count": 0, "min": None, "p25": None,
                      "median": None, "p75": None, "max": None,
                      "null_count": 0}
        r1 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "scB"})
        s1 = r1.json()["detail"]["detail"]["score_summary"]
        assert s1 == {"count": 1, "min": 7.7, "p25": 7.7, "median": 7.7,
                      "p75": 7.7, "max": 7.7, "null_count": 0}, (
            "1 行退化五数全同值")

    def test_protected_status_rejected_then_released(self, env):
        """预检锚：删除域含 paper 态行——预览（confirm=false）即 422
        保护拒绝+confirm=true 同拒+受保护行逐行在场+状态迁出后放行。"""
        c, db = env["client"], env["db"]
        _seed_scored(db, "ptA", [5.0, 6.0])
        conn = sqlite3.connect(db)
        conn.execute("UPDATE strategy SET status = 'paper' "
                     "WHERE strategy_id = 'st_ptA_0'")
        conn.commit()
        conn.close()
        for confirm in (False, True):
            r = c.request("POST", "/api/strategies/batch", json={
                "source_task_id": "ptA", "confirm": confirm})
            assert r.status_code == 422, f"confirm={confirm} 须保护拒绝"
            assert r.json()["detail"]["code"] == \
                "BATCH_DELETE_PAPER_REFERENCED"
            assert r.json()["detail"]["detail"]["protected_count"] == 1
        g = c.get("/api/strategies",
                  params={"source_task_id": "ptA"})
        assert {x["strategy_id"] for x in g.json()["items"]} == \
            {"st_ptA_0", "st_ptA_1"}, "受保护行零删除（逐行在场）"
        # 处置：状态迁出保护集（paper→active 经直改模拟撤盘/归档后）
        conn = sqlite3.connect(db)
        conn.execute("UPDATE strategy SET status = 'active' "
                     "WHERE strategy_id = 'st_ptA_0'")
        conn.commit()
        conn.close()
        r2 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "ptA", "confirm": True})
        assert r2.status_code == 200
        assert r2.json()["deleted_count"] == 2, "迁出保护集后放行"

    def test_pt_task_reference_guard(self, env):
        """pt_task 引用锚：非终态（pending）纸盘任务引用删除域内策略
        →422；终态（completed）引用→放行。"""
        c, db = env["client"], env["db"]
        _seed_scored(db, "ptB", [3.0])
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO pt_task (task_id, status, strategy_id, "
            "dna_snapshot_json, symbol, timeframe, "
            "research_status_snapshot, created_at, updated_at) VALUES "
            "('pt-1', 'pending', 'st_ptB_0', '{}', 'BTCUSDT', '4h', "
            "'{}', '2026-01-01T00:00:00', '2026-01-01T00:00:00')")
        conn.commit()
        conn.close()
        r = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "ptB", "confirm": True})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "BATCH_DELETE_PAPER_REFERENCED"
        assert r.json()["detail"]["detail"]["pt_active_count"] == 1
        # 任务终态后放行
        conn = sqlite3.connect(db)
        conn.execute("UPDATE pt_task SET status = 'completed' "
                     "WHERE task_id = 'pt-1'")
        conn.commit()
        conn.close()
        r2 = c.request("POST", "/api/strategies/batch", json={
            "source_task_id": "ptB", "confirm": True})
        assert r2.status_code == 200 and r2.json()["deleted_count"] == 1

    def test_same_transaction_audit_and_structure(self, env):
        """同事务锚（源码+行为）+审计锚（自备 handler 直挂 crud
        logger）+结构锚（路由无 SQL 直写）。"""
        import inspect
        import logging
        from core.persistence.repository import strategy as repo
        from api.routes.strategies import crud

        # 结构锚：路由 batch 函数体无 SQL 直写（下沉断言）
        route_src = inspect.getsource(crud.batch_delete_strategies)
        for banned in ("sa_delete", "sa_select", "select(",
                       "delete(Strategy"):
            assert banned not in route_src, f"路由不得直写 {banned!r}"
        # 同事务源码锚：count 与 delete 同一 begin 块（块序：begin 在
        # count 之前、delete 在 count 之后——单事务先查后删）
        repo_src = inspect.getsource(
            repo.batch_delete_by_source_task)
        assert repo_src.count("engine.begin()") == 1
        assert repo_src.index("engine.begin()") < repo_src.index(
            "func.count()")
        assert repo_src.index("func.count()") < repo_src.index(
            "delete(Strategy)")

        # 行为锚+审计锚：执行请求 matched_before==deleted_count（同事务
        # 一致性）+warning 五要素 token
        c, db = env["client"], env["db"]
        _seed_scored(db, "auA", [1.0, 2.0, 3.0])

        class _ListHandler(logging.Handler):
            def __init__(self):
                super().__init__(level=logging.WARNING)
                self.records = []

            def emit(self, record):
                self.records.append(record)

        handler = _ListHandler()
        crud_logger = logging.getLogger("api.routes.strategies.crud")
        crud_logger.addHandler(handler)
        try:
            r = c.request("POST", "/api/strategies/batch", json={
                "source_task_id": "auA", "confirm": True})
            body = r.json()
        finally:
            crud_logger.removeHandler(handler)
        assert body["deleted_count"] == 3
        assert body["matched_before"] == body["deleted_count"], (
            "同事务：执行请求内 matched_before==deleted_count")
        audit = " | ".join(rec.getMessage() for rec in handler.records)
        assert "批量删除执行" in audit
        assert "auA" in audit and "deleted_count=3" in audit
