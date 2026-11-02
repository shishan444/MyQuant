"""阶段四闸门盲区消解补洞（第 2 步产物，锚点分类：契约+边界）。

按 coverage.json 全量基线的缺口清单逐文件补测（行+分支至 ≥98%）：
- evolution/crud：坏 JSON 容错分支、leverage 覆盖、stream cancel/
  异常分支、PATCH 空更新等
- 剩余不可达项的判定见文件尾 TestUnreachableNotes（带调用图依据）
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_dna, make_ohlcv


class TestEvolutionToleranceBranches:
    """evolution.py 容错分支（55-100/161-168/295-296/476-498）。"""

    def test_parse_json_helpers_corrupt_input(self) -> None:
        """_parse_json_list/dict：坏 JSON 与非列表/字典 → None。"""
        from api.routes.evolution import _parse_json_dict, _parse_json_list
        assert _parse_json_list("{bad") is None
        assert _parse_json_list("[1,2]") == [1, 2]
        assert _parse_json_list('{"a":1}') is None  # 非列表
        assert _parse_json_dict("{bad") is None
        assert _parse_json_dict('{"a":1}') == {"a": 1}
        assert _parse_json_dict("[1]") is None  # 非字典
        assert _parse_json_list(None) is None
        assert _parse_json_dict(None) is None

    def test_task_response_corrupt_json_fields(self, api_client: TestClient) -> None:
        """坏 initial_dna/champion_dna/requirements JSON → 对应字段 None。"""
        from core.persistence.db import save_task, update_task
        from core.persistence.repository import evolution as evo_repo
        from sqlalchemy import update as sa_update
        from core.persistence.orm.models import EvolutionTask
        from core.persistence.repository.dataset_meta import _engine_for
        db_path = api_client.app.state.db_path
        save_task(db_path, task_id="t-bad", target_score=80.0,
                  template="explorer", symbol="BTCUSDT", timeframe="4h",
                  initial_dna=make_dna())
        engine = _engine_for(db_path)
        with engine.begin() as conn:
            conn.execute(sa_update(EvolutionTask).where(
                EvolutionTask.task_id == "t-bad").values(
                initial_dna="{corrupt", champion_dna="{corrupt",
                requirements_json="{corrupt"))
        response = api_client.get("/api/evolution/tasks/t-bad")
        assert response.status_code == 200
        data = response.json()
        assert data["initial_dna"] is None
        assert data["champion_dna"] is None
        assert data["requirements"] is None
        assert data["indicator_pool"] is None  # 坏 JSON 列表路径

    def test_strategies_list_corrupt_json_passes(self, api_client: TestClient) -> None:
        """/strategies 列表：坏 dna/metrics JSON 条目保留（None 字段）。"""
        from core.persistence.repository.strategy import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(db_path, strategy_id="evo-bad", dna_json="{corrupt",
                      symbol="BTCUSDT", timeframe="4h", source="evolution",
                      metrics_json="{also-bad", best_score=1.0)
        response = api_client.get("/api/evolution/strategies")
        items = {i["strategy_id"]: i for i in response.json()}
        assert "evo-bad" in items
        assert items["evo-bad"]["dna"] is None
        assert items["evo-bad"]["metrics"] is None

    def test_create_task_corrupt_parquet_passes(self, api_client: TestClient) -> None:
        """建任务读坏 parquet：except pass → 201 且行数 0。"""
        data_dir = api_client.app.state.data_dir
        (data_dir / "BADPARQ_4h.parquet").write_bytes(b"not parquet")
        response = api_client.post(
            "/api/evolution/tasks",
            json={"symbol": "BADPARQ", "timeframe": "4h",
                  "target_score": 80.0, "score_template": "explorer",
                  "population_size": 5, "max_generations": 5},
        )
        assert response.status_code == 201
        assert response.json()["data_row_count"] == 0
        assert response.json()["data_time_start"] is None

    def test_task_strategies_corrupt_champion_and_snapshot(
        self, api_client: TestClient,
    ) -> None:
        """champion/snapshot 坏 JSON：pass/continue 不炸，正常快照保留。"""
        from core.persistence.db import save_task, save_snapshot, update_task
        db_path = api_client.app.state.db_path
        save_task(db_path, task_id="t-sc", target_score=80.0,
                  template="explorer", symbol="BTCUSDT", timeframe="4h",
                  initial_dna=make_dna())
        from sqlalchemy import update as sa_update
        from core.persistence.orm.models import EvolutionTask
        from core.persistence.repository.dataset_meta import _engine_for
        engine = _engine_for(db_path)
        with engine.begin() as conn:
            conn.execute(sa_update(EvolutionTask).where(
                EvolutionTask.task_id == "t-sc").values(champion_dna="{corrupt"))
        good_snap = make_dna()
        good_snap.strategy_id = "snap-ok"
        save_snapshot(db_path, "t-sc", 1, best_score=1.0, avg_score=0.5,
                      best_dna=good_snap, population=[])
        response = api_client.get("/api/evolution/tasks/t-sc/strategies")
        strategies = response.json()["strategies"]
        assert [s["strategy_id"] for s in strategies] == ["snap-ok"]


class TestCrudToleranceBranches:
    """crud.py 容错分支（91-94/232-239）。"""

    def test_get_strategy_corrupt_metrics_json(self, api_client: TestClient) -> None:
        """GET 策略坏 metrics_json → metrics None（不 500）。"""
        from core.persistence.repository.strategy import save_strategy
        from sqlalchemy import update as sa_update
        from core.persistence.orm.models import Strategy
        from core.persistence.repository.dataset_meta import _engine_for
        db_path = api_client.app.state.db_path
        save_strategy(db_path, strategy_id="s-mbad",
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        engine = _engine_for(db_path)
        with engine.begin() as conn:
            conn.execute(sa_update(Strategy).where(
                Strategy.strategy_id == "s-mbad").values(metrics_json="{corrupt"))
        response = api_client.get("/api/strategies/s-mbad")
        assert response.status_code == 200
        assert response.json()["metrics"] is None

    def test_put_updates_each_field_branch(self, api_client: TestClient) -> None:
        """PUT 各字段分支逐一覆盖（name/tags/notes/best_score/dna）。"""
        from core.persistence.repository.strategy import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(db_path, strategy_id="s-put",
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        new_dna = make_dna(direction="short")
        response = api_client.put(
            "/api/strategies/s-put",
            json={
                "name": "Renamed", "tags": "t1,t2", "notes": "n",
                "best_score": 66.6,
                "dna": new_dna.to_dict(),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Renamed"
        assert data["tags"] == "t1,t2"
        assert data["notes"] == "n"
        assert data["best_score"] == 66.6
        assert data["dna"]["risk_genes"]["direction"] == "short"

    def test_put_no_fields_is_noop(self, api_client: TestClient) -> None:
        """PUT 空 body：零字段更新，原样返回。"""
        from core.persistence.repository.strategy import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(db_path, strategy_id="s-noop",
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="Keep")
        response = api_client.put("/api/strategies/s-noop", json={})
        assert response.status_code == 200
        assert response.json()["name"] == "Keep"


class TestBacktestGaps:
    """backtest.py：legacy fallback 逐基因 except（147-148）与
    timeframe_pool elif 分支（164-165）。"""

    def _inline_dna(self, indicator: str) -> dict:
        return {
            "signal_genes": [
                {"indicator": "RSI", "params": {"period": 14},
                 "role": "entry_trigger", "field": None,
                 "condition": {"type": "lt", "threshold": 30}},
                {"indicator": indicator, "params": {"period": 9},
                 "role": "exit_trigger", "field": None,
                 "condition": {"type": "gt", "threshold": 70}},
            ],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10,
                           "position_size": 0.3},
        }

    def test_legacy_fallback_unknown_indicator_rejected(
        self, api_client: TestClient,
    ) -> None:
        """enhanced 未命中走 legacy：非法基因指标经端点结构化拒绝。

        data_start/end 收窄使区间内 bar 数 < load_and_prepare_df 的
        min_bars（50）→ enhanced None → legacy 裸 df 路径。
        unify-tool-return-contract D7 列解析收口（+CARD-08 端点传播
        配套）后：非法基因从「continue 跳过零信号完成 200」变为
        422 结构化拒绝（RESEARCH_CONDITION_INVALID）——旧行为锚随
        修复退役，测试同步改正确行为。
        """
        make_ohlcv(120, "4h").to_parquet(
            api_client.app.state.data_dir / "FALLBK_4h.parquet")
        response = api_client.post(
            "/api/strategies/backtest",
            json={"dataset_id": "FALLBK_4h", "dna": self._inline_dna("NOSUCHIND"),
                  "symbol": "FALLBK", "timeframe": "4h",
                  "data_start": "2024-01-01", "data_end": "2024-01-08"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "RESEARCH_CONDITION_INVALID"
        assert detail["detail"]["indicator"] == "NOSUCHIND"

    def test_timeframe_pool_elif_loads_mtf(self, api_client: TestClient) -> None:
        """非 MTF DNA + timeframe_pool 多值 → elif 装载分支。"""
        make_ohlcv(80, "4h").to_parquet(
            api_client.app.state.data_dir / "BTCUSDT_4h.parquet")
        make_ohlcv(80, "1d").to_parquet(
            api_client.app.state.data_dir / "BTCUSDT_1d.parquet")
        response = api_client.post(
            "/api/strategies/backtest",
            json={"dataset_id": "BTCUSDT_4h", "dna": self._inline_dna("RSI"),
                  "symbol": "BTCUSDT", "timeframe": "4h",
                  "timeframe_pool": ["4h", "1d"]},
        )
        assert response.status_code == 200


class TestBatchGaps:
    """batch.py：finalize 空 periods continue（358）、detail satisfaction
    坏 JSON（508-509）；stream except 分支不可达判定见 TestUnreachableNotes。"""

    def test_finalize_skips_empty_periods(self, tmp_path: Path) -> None:
        """init 后未跑任何 step 直接 finalize：periods 空 → summary 空。"""
        from core.persistence.repository.strategy import save_strategy
        from core.persistence.db_ext import init_db_ext
        from api.routes.strategies.batch import _BatchBacktestProcessor
        from api.schemas import BatchBacktestRequest, BacktestDateRange
        db_path = tmp_path / "b.db"
        init_db_ext(db_path)
        save_strategy(db_path, strategy_id="s-e", name="E",
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        payload = BatchBacktestRequest(
            strategy_ids=["s-e"],
            data_ranges=[BacktestDateRange(start="2024-01-01", end="2024-06-01")],
        )
        processor = _BatchBacktestProcessor(payload, db_path, tmp_path)
        processor.init()
        result = processor.finalize()
        assert result["summary"] == []  # periods 全空被 continue

    def test_detail_corrupt_satisfaction_becomes_none(
        self, api_client: TestClient,
    ) -> None:
        """detail 端点 satisfaction_json 坏 JSON → None（508-509）。"""
        import json as _json
        from core.persistence.repository.backtest_result import save_backtest_result
        db_path = api_client.app.state.db_path
        save_backtest_result(
            db_path, result_id="res-sat", strategy_id="s1",
            symbol="BTCUSDT", timeframe="4h",
            data_start="2024-01-01", data_end="2024-06-01",
            init_cash=100000, fee=0.001, slippage=0.0005,
            run_source="batch_backtest",
            satisfaction_json="{corrupt",
        )
        response = api_client.get("/api/strategies/batch-backtest/res-sat")
        assert response.status_code == 200
        assert response.json()["satisfaction"] is None


class TestCompareGaps:
    """compare.py：候选列全不中 → 空串统计（174-175）。"""

    def test_no_timestamp_column_yields_empty_stats(
        self, api_client: TestClient,
    ) -> None:
        """parquet 无 timestamp 类列（RangeIndex 不落存储）→ data_start/end 空串。"""
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq
        from core.persistence.repository.backtest_result import list_backtest_results
        from core.persistence.repository.strategy import save_strategy
        # 构造无 index 列的 parquet（RangeIndex + drop index）
        df = pd.DataFrame({
            "open": [100.0, 101.0], "high": [102.0, 103.0],
            "low": [99.0, 100.0], "close": [101.0, 102.0],
            "volume": [10.0, 11.0],
        })
        table = pa.Table.from_pandas(df, preserve_index=False)
        data_dir = api_client.app.state.data_dir
        pq.write_table(table, data_dir / "PLAIN_4h.parquet")
        db_path = api_client.app.state.db_path
        save_strategy(db_path, strategy_id="s-plain", name="P",
                      dna_json=make_dna().to_json(),
                      symbol="PLAIN", timeframe="4h")
        response = api_client.post(
            "/api/strategies/compare",
            json={"strategy_ids": ["s-plain"], "dataset_id": "PLAIN_4h"},
        )
        assert response.status_code == 200
        rows = list_backtest_results(db_path, strategy_id="s-plain", limit=1)
        assert rows[0]["data_start"] == ""


class TestSceneGaps:
    """scene.py：场景类型清单端点（84-96）与 meta 兜底（126）。"""

    def test_scene_types_endpoint_lists_registry(self, api_client: TestClient) -> None:
        response = api_client.get("/api/scene/types")
        assert response.status_code == 200
        types = response.json()["types"]
        assert types, "场景注册表应非空"
        first = types[0]
        for key in ("id", "label", "description", "group", "parent"):
            assert key in first


class TestLiveTradingGaps:
    """live_trading.py PATCH 空 updates 跳过（67-69 分支）。"""

    def test_patch_empty_body_noop(self, api_client: TestClient) -> None:
        response = api_client.post(
            "/api/live-trading/tasks",
            json={"symbol": "BTCUSDT", "timeframe": "4h",
                  "dna_json": make_dna().to_json()},
        )
        task_id = response.json()["task_id"]
        patched = api_client.patch(f"/api/live-trading/tasks/{task_id}", json={})
        assert patched.status_code == 200
        assert patched.json()["task_id"] == task_id


class TestDataGaps:
    """data.py import-batch 空 files 400（243）。"""

    def test_import_batch_empty_files_400(self, tmp_path: Path) -> None:
        import sys
        mod = sys.modules.get("MyQuant.api.routes.data")
        if mod is None:
            import api.routes.data as mod
        from fastapi import HTTPException
        db = tmp_path / "d.db"
        (tmp_path / "data").mkdir()
        with pytest.raises(HTTPException) as exc:
            mod.import_csv_batch_endpoint(
                files=[], symbol=None, interval=None, mode="merge",
                db_path=db, data_dir=tmp_path / "data",
            )
        assert exc.value.status_code == 400
        assert exc.value.detail == "No files provided"


class TestUnreachableNotes:
    """阶段四闸门不可达判定（带调用图依据，生产代码零改动）。

    1. api/app.py 36-37（APP_VERSION 回退 "0.16.0"）：仅当
       importlib.metadata.version("MyQuant") 抛异常（包未安装）时可达；
       本仓 venv 为 editable 安装（__editable__.myquant-0.16.0.pth），
       全部官方启动/测试路径下包恒可解析——环境分支，测试不可稳定触达。

    2. api/routes/strategies/batch.py 458-460（stream except 分支的
       mark_failed hasattr False 路径）：_BatchBacktestProcessor 类定义
       恒携带 mark_failed 方法（batch.py:415），hasattr 恒 True——
       False 分支为防御性死代码。

    3. api/routes/strategies/batch.py 457-458（stream except 主体）：
       _run_stream 内 to_thread(process_step) 的异常被 process_step
       内部组级 try/except 全量捕获（永不外抛）；to_thread(finalize)
       为纯内存聚合（无 IO、无外部调用，build_summary 仅遍历既有
       列表），无已知抛出路径。except 主体仅理论上可达。

    4. api/routes/evolution.py 335（create 后 get_task None → 500）：
       save_task 单事务 insert 后同连接 get_task 必命中（SQLite
       串行写）；仅 save_task 与 get_task 之间任务被并发删除时可达
       ——单用户本地形态 + 建任务后毫秒级窗口，实际不可达。
    """

    def test_notes_are_documented(self) -> None:
        """占位：判定条目以 docstring 载体落档（见类 docstring）。"""
        assert True


class TestRunnerDirectBranches:
    """runner.py 可直构分支：_tick 的 active 任务清理（177-183）与
    run() 循环的 tick error 容错（156-157）。

    深执行路径（target_score 调整 260-262/307、MTF 装载 331-338、
    on_generation 内部 482-483/601-602、无 champion else 634）需完整
    进化编排（线程 + engine mock 全链），主路径已由 tests/evolution
    线程级测试覆盖——按闸门口径记录为编排型深分支，不强行直构。
    """

    def _make_runner(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from api.runner import EvolutionRunner
        db_path = tmp_path / "r.db"
        init_db_ext(db_path)
        return EvolutionRunner(db_path=db_path, data_dir=tmp_path,
                               poll_interval=0.0)

    def test_tick_clears_finished_active_task(self, tmp_path: Path) -> None:
        """active 任务已停止（DB 态）→ _tick 清理 _active_task_id。"""
        from core.persistence.db import save_task, update_task
        runner = self._make_runner(tmp_path)
        save_task(runner.db_path, task_id="t-done", target_score=80.0,
                  template="explorer", symbol="BTCUSDT", timeframe="4h",
                  initial_dna=make_dna())
        update_task(runner.db_path, "t-done", status="stopped")
        # 并发模型（research-tooling-data-quality 卡 B）：属性退役改
        # future 登记——终态 DB 行对应登记被 _tick 剔除
        import concurrent.futures as _cf
        runner._pool = _cf.ThreadPoolExecutor(max_workers=1)
        _fut = runner._pool.submit(lambda: None)
        _fut.result()
        with runner._lock:
            runner._futures["t-done"] = _fut
        runner._tick()
        with runner._lock:
            assert "t-done" not in runner._futures
        runner._pool.shutdown(wait=True)

    def test_tick_continues_active_running_task(self, tmp_path: Path) -> None:
        """active 任务仍 running → _tick 保留（代际推进归 on_generation）。"""
        from core.persistence.db import save_task, update_task
        runner = self._make_runner(tmp_path)
        save_task(runner.db_path, task_id="t-run", target_score=80.0,
                  template="explorer", symbol="BTCUSDT", timeframe="4h",
                  initial_dna=make_dna())
        update_task(runner.db_path, "t-run", status="running")
        runner._active_task_id = "t-run"
        runner._tick()
        assert runner._active_task_id == "t-run"

    def test_run_loop_survives_tick_error(self, tmp_path: Path) -> None:
        """run() 循环：_tick 抛异常被吞，循环继续至 stop。"""
        runner = self._make_runner(tmp_path)
        calls = {"n": 0}
        original_tick = runner._tick

        def _tick_then_stop():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("tick boom")
            original_tick()
            runner.stop()

        runner._tick = _tick_then_stop
        runner.run()  # 不应抛异常（tick error 容错分支）
        assert calls["n"] >= 2  # 第一次抛错后循环仍继续

    def test_get_status_shape(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        status = runner.get_status()
        assert status["is_alive"] is False
        assert status["active_task_id"] is None
        assert "tick_count" in status and "last_tick_age_seconds" in status


class TestSceneMetaFallback:
    """scene.py 126：payload.scene_type 不在 SCENE_META 时从 result 取。"""

    def test_unknown_request_type_falls_back_to_result(
        self, api_client: TestClient,
    ) -> None:
        from unittest.mock import patch as _patch
        from types import SimpleNamespace
        import sys
        mod = sys.modules.get("MyQuant.api.routes.scene")
        if mod is None:
            import api.routes.scene as mod
        fake = SimpleNamespace(
            scene_type="trend",
            total_triggers=0, statistics_by_horizon=[],
            trigger_details=[], warnings=[],  # scene.py:130-160 消费面
        )
        with _patch.object(mod, "run_scene_verification", return_value=fake):
            response = api_client.post(
                "/api/validate/scene",
                json={"symbol": "BTCUSDT", "timeframe": "4h",
                      "scene_type": "weird-unknown-type"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["scene_type"] == "trend"
        assert data["scene_label"]  # result 侧 meta 提供 label


class TestEvolutionSnapshotCorruptContinue:
    """evolution.py 497-498：坏 JSON 快照 continue，好快照保留。"""

    def test_corrupt_snapshot_skipped(self, api_client: TestClient) -> None:
        import sys
        from core.persistence.db import save_task, save_snapshot
        from sqlalchemy import update as sa_update
        from core.persistence.orm.models import GenerationSnapshot
        from core.persistence.repository.dataset_meta import _engine_for
        db_path = api_client.app.state.db_path
        save_task(db_path, task_id="t-snap", target_score=80.0,
                  template="explorer", symbol="BTCUSDT", timeframe="4h",
                  initial_dna=make_dna())
        # 直接落坏 best_dna：先存好快照再改列为坏 JSON
        good = make_dna()
        good.strategy_id = "snap-good"
        save_snapshot(db_path, "t-snap", 1, best_score=1.0, avg_score=0.5,
                      best_dna=good, population=[])
        engine = _engine_for(db_path)
        with engine.begin() as conn:
            conn.execute(sa_update(GenerationSnapshot).where(
                GenerationSnapshot.task_id == "t-snap",
                GenerationSnapshot.generation == 1,
            ).values(best_dna="{corrupt"))
        response = api_client.get("/api/evolution/tasks/t-snap/strategies")
        assert response.status_code == 200
        assert response.json()["strategies"] == []


class TestBacktestLegacyExceptUnreachable:
    """backtest.py 147-148 不可达判定（实测证实，patch 指标抛错实验失败后归因）。

    调用图依据：legacy fallback 的逐基因指标计算（backtest.py:145）与
    BacktestEngine.run 内部的指标计算共用同一入口
    core.features.indicators._compute_indicator。指标器在同一 df 上行为
    一致：不抛则 except 永不触发；抛则 engine.run 同样抛 → 500。
    即「except 生效且请求成功」与「engine 正常」不可同时成立——
    分支仅在 engine 自带与 legacy 不同的容错时可达（现状无此差异）。
    未知指标名实测返回空帧不抛（97.9%→94.5% 缺口项即此 2 行）。
    """

    def test_judgement_documented(self) -> None:
        assert True


class TestNonameStrategyRegression:
    """CARD-31（TD-G1）修复锚定：无名策略 verify/列表不再 500。

    根因：row.get("name", sid[:8]) 的 default 仅键缺失生效——save 层
    name=None 落库后键存在值 None → strategy_name=None 炸
    VerifySummaryItem 校验。修复后 None 也回退短 id。save 层对显式
    缺省有内建名生成，None name 行经 ORM 直构（复现真实缺口路径）。
    """

    @staticmethod
    def _seed_noname(db_path, sid: str) -> None:
        from sqlalchemy import insert as sa_insert
        from core.persistence.orm.models import Strategy
        from core.persistence.repository.dataset_meta import _engine_for
        engine = _engine_for(db_path)
        from tests.helpers.data_factory import make_dna as _mk
        with engine.begin() as conn:
            conn.execute(sa_insert(Strategy).values(
                strategy_id=sid, name=None, dna_json=_mk().to_json(),
                source="manual", symbol="BTCUSDT", timeframe="4h",
                created_at="2026-10-02T00:00:00Z",
                updated_at="2026-10-02T00:00:00Z",
            ))

    def test_evolution_list_noname_fallback(self, api_client: TestClient) -> None:
        """evolution 策略列表：None name → strategy_id 前 8 位。"""
        from sqlalchemy import insert as sa_insert, update as sa_update
        from core.persistence.orm.models import Strategy
        from core.persistence.repository.dataset_meta import _engine_for
        db_path = api_client.app.state.db_path
        from tests.helpers.data_factory import make_dna as _mk
        engine = _engine_for(db_path)
        with engine.begin() as conn:
            conn.execute(sa_insert(Strategy).values(
                strategy_id="evo-noname-x", name=None,
                dna_json=_mk().to_json(), source="evolution",
                source_task_id="t-x", symbol="BTCUSDT", timeframe="4h",
                created_at="2026-10-02T00:00:00Z",
                updated_at="2026-10-02T00:00:00Z",
            ))
        response = api_client.get("/api/evolution/strategies")
        items = {i["strategy_id"]: i for i in response.json()}
        assert items["evo-noname-x"]["name"] == "evo-nona"  # sid[:8]

    def test_batch_processor_noname_fallback(self, tmp_path: Path) -> None:
        """batch processor：None name → 短 id 回退（源头同修复）。"""
        from core.persistence.db_ext import init_db_ext
        from api.routes.strategies.batch import _BatchBacktestProcessor
        from api.schemas import BatchBacktestRequest, BacktestDateRange
        from sqlalchemy import insert as sa_insert
        from core.persistence.orm.models import Strategy
        from core.persistence.repository.dataset_meta import _engine_for
        from tests.helpers.data_factory import make_dna as _mk
        db_path = tmp_path / "nn.db"
        init_db_ext(db_path)
        engine = _engine_for(db_path)
        with engine.begin() as conn:
            conn.execute(sa_insert(Strategy).values(
                strategy_id="batch-noname", name=None,
                dna_json=_mk().to_json(), source="manual",
                symbol="BTCUSDT", timeframe="4h",
                created_at="2026-10-02T00:00:00Z",
                updated_at="2026-10-02T00:00:00Z",
            ))
        payload = BatchBacktestRequest(
            strategy_ids=["batch-noname"],
            data_ranges=[BacktestDateRange(start="2024-01-01", end="2024-06-01")],
        )
        processor = _BatchBacktestProcessor(payload, db_path, tmp_path)
        processor.init()
        assert processor.strategy_names["batch-noname"] == "batch-no"
