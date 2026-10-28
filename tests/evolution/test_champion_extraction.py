"""champion 运行中提取行为验证（research-tooling-data-quality 卡 C——
tester 锚 C3-B）。

推导依据：spec delta「冠军运行中可提取（经既有快照通道）」——运行中
快照 top10（best_score 排序、champion 所在代必居 top1、完整可执行
DNA）+终态 champion_dna 权威兜底两态连续；快照写失败降级可观察；
MUST NOT 新增运行中 champion_dna 写入路径（B5 禁改面）。

真实链路沿 happy path 形制（调度/落库/引擎全真实，评估为确定性替身）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db import get_task
from core.persistence.db_ext import init_db_ext
from tests.helpers.data_factory import make_dna


def _wait_for(predicate, timeout: float = 15.0, interval: float = 0.05,
              msg: str = "条件未在时限内满足"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"等待超时: {msg}")


def _stub_score(dna) -> float:
    sl = float(dna.risk_genes.stop_loss)
    return round(100.0 * max(0.0, 1.0 - abs(sl - 0.075) / 0.075), 4)


def _mk_task_row(task_id: str, status: str) -> str:
    """evolution_task 最小合法行（NOT NULL 无默认列全供给）。"""
    return (
        "INSERT INTO evolution_task (task_id, status, target_score, "
        "score_template, symbol, timeframe, initial_dna, "
        "max_generations, population_size, created_at, updated_at) "
        f"VALUES ('{task_id}', '{status}', 5, 'default', 'BTCUSDT', "
        f"'4h', '{{}}', 10, 5, "
        "'2026-01-01T00:00:00', '2026-01-01T00:00:00')")


class TestSnapshotChannelOrdering:
    """B1 结构断言：top10 排序语义+top1=最高分代+完整可执行 DNA。"""

    @pytest.fixture()
    def env(self, tmp_path):
        from fastapi.testclient import TestClient
        from api.app import create_app

        db = tmp_path / "champ.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        app = create_app(db_path=db, data_dir=d)
        with TestClient(app) as client:
            # 预停调度线程：本组用例直插 DB 行，防 tick 拾取竞态
            runner = app.state.evolution_runner
            runner.stop()
            runner.join(timeout=5.0)
            yield {"client": client, "db": db}

    def test_snapshot_top10_ordering_full_dna(self, env):
        """12 代快照：返回 ≤10 条按 best_score 降序，top1=最高分代
        （gen7），每条 dna 可解析为 StrategyDNA（非仅字符串在场）。"""
        from core.strategy.dna import StrategyDNA

        db, client = env["db"], env["client"]
        conn = sqlite3.connect(db)
        conn.execute(_mk_task_row("run-1", "running"))
        # 12 代分数交错，唯一最高分在 gen7=9.9
        scores = {g: 1.0 + (g % 5) * 0.5 for g in range(1, 13)}
        scores[7] = 9.9
        for g, sc in scores.items():
            conn.execute(
                "INSERT INTO generation_snapshot (task_id, generation, "
                "best_score, avg_score, best_dna, population_json, "
                "created_at) VALUES ('run-1', ?, ?, ?, ?, '{}', "
                "'2026-01-01T01:00:00')",
                (g, sc, sc, make_dna().to_json()))
        conn.commit()
        conn.close()

        r = client.get("/api/evolution/tasks/run-1/strategies")
        assert r.status_code == 200
        body = r.json()
        snaps = [s for s in body["strategies"] if s["source"] == "snapshot"]
        assert len(snaps) == 10, "12 代快照限 top10"
        assert not [s for s in body["strategies"]
                    if s["source"] == "champion"], "运行中无终态条目"
        got_scores = [s["score"] for s in snaps]
        assert got_scores == sorted(got_scores, reverse=True), (
            "必须按 best_score 降序")
        assert snaps[0]["generation"] == 7 and snaps[0]["score"] == 9.9, (
            "top1=当前最高分代（champion 所在代快照必居首位）")
        for s in snaps:
            dna = StrategyDNA.from_dict(s["dna"])
            assert dna.strategy_id, "完整可执行 DNA（可解析非仅字符串）"

    def test_early_task_structured_empty(self, env):
        """B3b 早期结构化空态：无快照无冠军→空列表非崩溃非错值。"""
        conn = sqlite3.connect(env["db"])
        conn.execute(_mk_task_row("early-1", "pending"))
        conn.commit()
        conn.close()
        r = env["client"].get("/api/evolution/tasks/early-1/strategies")
        assert r.status_code == 200
        assert r.json() == {"task_id": "early-1", "strategies": []}

    def test_terminal_champion_entry_fallback(self, env):
        """B2 终态保持：completed 后 champion_dna 权威兜底在库——
        champion 条目在场（dna 可解析+strategy_id 即权威冠军）与快照
        通道共存。注：champion 条目 score 取 champion_score 列——表
        无该列恒 0（存量端点行为观察，已报 team-lead；契约未承诺该值，
        此处不虚构断言）。"""
        from core.strategy.dna import StrategyDNA

        db, client = env["db"], env["client"]
        champ = make_dna()
        conn = sqlite3.connect(db)
        conn.execute(_mk_task_row("done-1", "completed"))
        conn.execute(
            "UPDATE evolution_task SET champion_dna = ? "
            "WHERE task_id = 'done-1'", (champ.to_json(),))
        # 一条快照（两通道共存）
        conn.execute(
            "INSERT INTO generation_snapshot (task_id, generation, "
            "best_score, avg_score, best_dna, population_json, created_at) "
            "VALUES ('done-1', 1, 3.0, 3.0, ?, '{}', "
            "'2026-01-01T01:00:00')", (make_dna().to_json(),))
        conn.commit()
        conn.close()

        r = client.get("/api/evolution/tasks/done-1/strategies")
        body = r.json()
        champs = [s for s in body["strategies"] if s["source"] == "champion"]
        assert len(champs) == 1, "终态权威冠军条目在场（兜底通道）"
        dna = StrategyDNA.from_dict(champs[0]["dna"])
        assert dna.strategy_id == champ.strategy_id, (
            "champion_dna 字段为权威冠军（兜底在库）")
        assert [s for s in body["strategies"]
                if s["source"] == "snapshot"], "快照通道共存"


class TestSnapshotFailureDegrade:
    """B3 降级：某代 save_snapshot 写失败→该代缺失+error 日志+进化
    不中断+后续代正常（真实 runner 链）。"""

    def test_write_failure_degrades_not_interrupts(self, tmp_path, caplog):
        from unittest.mock import patch
        from api.runner import EvolutionRunner
        from core.features.indicators import compute_all_indicators
        from core.persistence.repository.evolution import save_snapshot
        from tests.helpers.data_factory import make_ohlcv

        db = tmp_path / "deg.db"
        init_db_ext(db)
        runner = EvolutionRunner(db_path=db, data_dir=tmp_path,
                                 poll_interval=0.05)

        from core.persistence.db import save_task
        save_task(db, task_id="deg-1", target_score=80.0,
                  template="profit_first", symbol="BTCUSDT",
                  timeframe="4h", initial_dna=make_dna())
        conn = sqlite3.connect(db)
        conn.execute("UPDATE evolution_task SET max_generations = 3, "
                     "continuous = 0 WHERE task_id = 'deg-1'")
        conn.commit()
        conn.close()
        task_row = dict(get_task(db, "deg-1"))

        calls = {"n": 0}
        orig = save_snapshot

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return orig(*a, **kw)

        enhanced = compute_all_indicators(make_ohlcv(120, "4h"))
        with patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=enhanced), \
             patch("api.runner_progress._push_ws"), \
             patch("api.runner.save_snapshot", flaky), \
             patch.object(runner, "_evaluate_population",
                          lambda pop, *a, **kw: [_stub_score(d)
                                                 for d in pop]), \
             patch.object(runner, "_evaluate_dna",
                          lambda ind, *a, **kw: {
                              "score": _stub_score(ind),
                              "fitness": _stub_score(ind),
                              "qualified": False, "raw_metrics": {},
                              "dimension_scores": {}, "total_trades": 0}), \
             caplog.at_level("ERROR"):
            runner._run_task(task_row)

        row = get_task(db, "deg-1")
        assert row["status"] == "completed", (
            f"快照写失败不得中断进化: {row['status']}/"
            f"{row.get('stop_reason')}")
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM generation_snapshot "
                         "WHERE task_id = 'deg-1'").fetchone()[0]
        conn.close()
        assert calls["n"] >= 3, f"快照逐代被调用 {calls['n']} 次"
        assert n == calls["n"] - 1, "恰缺失失败那一代，后续代正常落库"
        assert any("本代快照缺失" in rec.message for rec in caplog.records), (
            "降级须留 error 日志（可观察）")


class TestTwoChannelConsistency:
    """B4 一致性锚：同任务运行中快照 top1 与终态 champion_dna 两通道
    提取结果一致（单一事实源行为级）+B5 禁改面（运行中 champion_dna
    列不写入）。"""

    def _run_task_with_hook(self, tmp_path, hold_gate: threading.Event,
                            hold_signal: threading.Event, max_gens: int = 4):
        """真实任务（分数逐代递增），第 2 代快照落库后置 hold_signal
        并挂起等待 hold_gate（运行中观察窗）。返回 (db, app, client)。"""
        import logging
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from api.app import create_app
        from api.runner import EvolutionRunner
        from core.features.indicators import compute_all_indicators
        from core.persistence.db import save_task
        from core.persistence.repository.evolution import save_snapshot
        from tests.helpers.data_factory import make_ohlcv

        db = tmp_path / "two.db"
        init_db_ext(db)
        d = tmp_path / "market"
        d.mkdir()
        app = create_app(db_path=db, data_dir=d)
        client = TestClient(app)
        client.__enter__()  # 手动进 lifespan（装配 db_path/runner）
        runner = app.state.evolution_runner
        runner.stop()
        runner.join(timeout=5.0)  # 防调度线程拾取（本组直调 _run_task）

        save_task(db, task_id="two-1", target_score=80.0,
                  template="profit_first", symbol="BTCUSDT",
                  timeframe="4h", initial_dna=make_dna())
        conn = sqlite3.connect(db)
        conn.execute("UPDATE evolution_task SET max_generations = ?, "
                     "continuous = 0 WHERE task_id = 'two-1'",
                     (max_gens,))
        conn.commit()
        conn.close()
        task_row = dict(get_task(db, "two-1"))

        counter = {"n": 0}
        snaps = {"n": 0}
        orig = save_snapshot

        def hooked(*a, **kw):
            result = orig(*a, **kw)
            snaps["n"] += 1
            if snaps["n"] == 2:
                hold_signal.set()
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if hold_gate.is_set():
                        break
                    time.sleep(0.02)
            return result

        def rising_pop(pop, *a, **kw):
            counter["n"] += 1
            return [10.0 + counter["n"]] * len(pop)

        enhanced = compute_all_indicators(make_ohlcv(120, "4h"))
        patches = [
            patch("core.data.mtf_loader.load_and_prepare_df",
                  return_value=enhanced),
            patch("api.runner_progress._push_ws"),
            patch("api.runner.save_snapshot", hooked),
            patch.object(runner, "_evaluate_population", rising_pop),
            patch.object(runner, "_evaluate_dna",
                         lambda ind, *a, **kw: {
                             "score": 1.0, "fitness": 1.0,
                             "qualified": False, "raw_metrics": {},
                             "dimension_scores": {}, "total_trades": 0}),
        ]
        for p in patches:
            p.start()

        def _task():
            logging.captureWarnings(False)
            runner._run_task(task_row)

        th = threading.Thread(target=_task)
        th.start()
        return db, client, th, patches

    def test_running_top1_matches_terminal_champion(self, tmp_path):
        """B4：运行中快照通道提取的 top1 与终态 champion_dna 一致
        （递增分数下冠军=末代——两通道对同一冠军的记录逐字段相等）。"""
        hold_gate, hold_signal = threading.Event(), threading.Event()
        db, client, th, patches = self._run_task_with_hook(
            tmp_path, hold_gate, hold_signal)

        try:
            _wait_for(hold_signal.is_set, msg="第二代号快照未落库")
            # 运行中通道提取（此刻任务在跑、未终态）
            row = get_task(db, "two-1")
            assert row["status"] == "running", "观察窗内任务应运行中"
            mid = client.get("/api/evolution/tasks/two-1/strategies").json()
            mid_snaps = [s for s in mid["strategies"]
                         if s["source"] == "snapshot"]
            assert mid_snaps, "运行中经快照通道可提取"
            assert not [s for s in mid["strategies"]
                        if s["source"] == "champion"], (
                "运行中终态通道必须为空（B5 两态语义）")
            mid_top1 = mid_snaps[0]

            hold_gate.set()
            th.join(timeout=60)
            assert not th.is_alive(), "任务线程未在时限内结束"

            # 终态两通道一致：champion 条目 dna == 快照表 top1（best_score
            # 排序首位）best_dna——对照面直读 DB（端点对同 strategy_id
            # 快照条目去重，响应内 snaps[0] 非冠军代）
            final_row = get_task(db, "two-1")
            assert final_row["status"] == "completed"
            conn = sqlite3.connect(db)
            top1_dna_json, top1_score = conn.execute(
                "SELECT best_dna, best_score FROM generation_snapshot "
                "WHERE task_id = 'two-1' "
                "ORDER BY best_score DESC LIMIT 1").fetchone()
            conn.close()
            final = client.get(
                "/api/evolution/tasks/two-1/strategies").json()
            champs = [s for s in final["strategies"]
                      if s["source"] == "champion"]
            snaps = [s for s in final["strategies"]
                     if s["source"] == "snapshot"]
            assert len(champs) == 1, "终态权威冠军条目在场"
            # 快照清单可为空：精英保留下历代 population[0] 同一个体时
            # 全部快照与 champion 同 identity 被去重（一致性更强形态）
            assert champs[0]["dna"] == json.loads(top1_dna_json), (
                "champion DNA==快照 top1 DNA（单一事实源两态连续）")
            champ_sid = champs[0]["strategy_id"]
            assert champ_sid not in {s["strategy_id"] for s in snaps}, (
                "与冠军同 identity 的快照条目应去重（语义连续承载）")
            # 运行中观察窗取到的 top1 信息不因终态丢失（连续可提取）
            final_ids = {s["strategy_id"] for s in final["strategies"]}
            assert mid_top1["strategy_id"] in final_ids or \
                mid_top1["score"] <= top1_score, (
                "运行中提取的冠军信息不因终态丢失")
        finally:
            hold_gate.set()
            th.join(timeout=60)
            for p in patches:
                p.stop()
            client.__exit__(None, None, None)

    def test_running_champion_dna_column_not_written(self, tmp_path):
        """B5 禁改面行为级：运行中 tasks.champion_dna 列不写入（终态
        才写）+update_champion_full 源码不含 champion_dna 列（禁改面
        静态半锚）。"""
        import inspect
        from core.persistence.repository import evolution as repo

        # 静态半锚：MUST NOT 新增运行中 champion_dna 写入路径
        src = inspect.getsource(repo.update_champion_full)
        assert "champion_dna" not in src, (
            "update_champion_full 不得扩展 champion_dna 写入（卡 C 禁改面）")

        hold_gate, hold_signal = threading.Event(), threading.Event()
        db, client, th, patches = self._run_task_with_hook(
            tmp_path, hold_gate, hold_signal)
        try:
            _wait_for(hold_signal.is_set, msg="第二代号快照未落库")
            conn = sqlite3.connect(db)
            dna_val, status = conn.execute(
                "SELECT champion_dna, status FROM evolution_task "
                "WHERE task_id = 'two-1'").fetchone()
            conn.close()
            assert status == "running"
            assert dna_val is None, (
                "运行中 champion_dna 列必须保持不写入（两态语义）")

            hold_gate.set()
            th.join(timeout=60)
            conn = sqlite3.connect(db)
            final_dna = conn.execute(
                "SELECT champion_dna FROM evolution_task "
                "WHERE task_id = 'two-1'").fetchone()[0]
            conn.close()
            assert final_dna, "终态后 champion_dna 权威写入"
        finally:
            hold_gate.set()
            th.join(timeout=60)
            for p in patches:
                p.stop()
            client.__exit__(None, None, None)
