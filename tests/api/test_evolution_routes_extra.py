"""Evolution 路由缺口特征网（修复期安全网，现状记录语义）。

补 api/routes/evolution.py 未覆盖分支（test_api.py 已覆盖 create/list/get/
history/pause/stop 主路径）：
- POST /tasks：无 K 线数据 400、timeframe_pool 自动推导 exec TF、默认 DNA 生成、
  requirements 透传
- GET /runner-status：无 runner 与有 runner 两分支
- GET /strategies 与 /tasks/{id}/discovered-strategies：evolution 来源策略列表与
  min_score 过滤
- GET /tasks/{id}/strategies：champion + snapshot 合成与去重
- pause/resume/stop 的非法迁移（409/400）与 404
- sort_timeframes 排序语义
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_dna


def _create_payload(**overrides) -> dict:
    body = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "target_score": 80.0,
        "score_template": "explorer",
        "population_size": 15,
        "max_generations": 200,
        "initial_dna": None,
    }
    body.update(overrides)
    return body


def _make_task_row(db_path: Path, task_id: str, status: str = "pending") -> None:
    """DB 直构任务行（绕开 POST，避免后台 runner 捡走 pending 任务）。"""
    from core.persistence.db import save_task, update_task
    save_task(
        db_path, task_id=task_id, target_score=80.0, template="explorer",
        symbol="BTCUSDT", timeframe="4h", initial_dna=make_dna(),
    )
    if status != "pending":
        update_task(db_path, task_id, status=status)


class TestCreateTaskDataValidation:
    def test_create_reports_data_range_metadata(
        self, api_client: TestClient,
    ) -> None:
        """CARD-24 后等价锚定：行数与时间范围仍从 parquet 元数据回显。

        conftest 数据集为有名 timestamp DatetimeIndex 单行——
        走候选序首列，num_rows 零解码 + 单列 min/max。
        """
        response = api_client.post(
            "/api/evolution/tasks", json=_create_payload(),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["data_row_count"] == 1
        assert data["data_time_start"] is not None
        assert data["data_time_end"] is not None
        assert "2024-01-01" in data["data_time_start"]

    def test_missing_kline_data_400(self, api_client: TestClient) -> None:
        """无该 symbol/timeframe 的 K 线数据 → 400（中文 detail）。"""
        response = api_client.post(
            "/api/evolution/tasks", json=_create_payload(symbol="NOSUCHCOIN"),
        )
        assert response.status_code == 400
        assert "K线数据" in response.json()["detail"]

    def test_timeframe_pool_overrides_exec_tf_to_shortest(
        self, api_client: TestClient,
    ) -> None:
        """timeframe_pool 多周期时 exec TF 被覆盖为最短档（4h < 1d）。"""
        response = api_client.post(
            "/api/evolution/tasks",
            json=_create_payload(timeframe="1d", timeframe_pool=["1d", "4h"]),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["timeframe"] == "4h"
        assert data["timeframe_pool"] == ["1d", "4h"]

    def test_default_dna_generated_with_mtf_layers(
        self, api_client: TestClient,
    ) -> None:
        """无 initial_dna + MTF pool：自动生成带 layers 的种子 DNA。"""
        response = api_client.post(
            "/api/evolution/tasks",
            json=_create_payload(timeframe_pool=["1d", "4h"]),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["initial_dna"] is not None
        assert data["initial_dna"]["layers"], "MTF pool 应生成多层数 DNA"
        # 任务级 leverage/direction 强制覆写进种子 DNA
        assert data["initial_dna"]["risk_genes"]["leverage"] == 1
        assert data["initial_dna"]["risk_genes"]["direction"] == "long"

    def test_requirements_persisted(self, api_client: TestClient) -> None:
        """requirements 配置透传入库并在响应回显。"""
        response = api_client.post(
            "/api/evolution/tasks",
            json=_create_payload(
                requirements={"objective": "annual_return", "min_annual_return": 0.2},
            ),
        )
        assert response.status_code == 201
        req = response.json()["requirements"]
        assert req is not None
        assert req["objective"] == "annual_return"

    def test_task_level_constraints_override_seed_dna(
        self, api_client: TestClient,
    ) -> None:
        """任务级 leverage/direction 覆盖种子 DNA 上的同名基因。"""
        response = api_client.post(
            "/api/evolution/tasks",
            json=_create_payload(leverage=5, direction="short"),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["initial_dna"]["risk_genes"]["leverage"] == 5
        assert data["initial_dna"]["risk_genes"]["direction"] == "short"


class TestRunnerStatus:
    def test_runner_status_without_runner(self) -> None:
        """app.state 无 runner 时回退 is_alive=False（直接函数调用）。"""
        from api.routes.evolution import runner_status
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(spec=[])),
        )
        request.app.state = SimpleNamespace()  # 无 evolution_runner 属性
        result = runner_status(request)
        assert result == {"is_alive": False, "error": "runner not initialized"}

    def test_runner_status_with_live_runner(self, api_client: TestClient) -> None:
        """TestClient 装配的 runner 存活时返回健康快照。"""
        response = api_client.get("/api/evolution/runner-status")
        assert response.status_code == 200
        data = response.json()
        assert data["is_alive"] is True
        assert "tick_count" in data
        assert "last_tick_age_seconds" in data


class TestDiscoveredStrategies:
    def _seed_evolution_strategy(self, db_path: Path) -> None:
        from core.persistence.repository.strategy import save_strategy
        save_strategy(
            db_path, strategy_id="evo-s1",
            dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="EvoOne",
            source="evolution", source_task_id="task-a", best_score=90.0,
        )
        save_strategy(
            db_path, strategy_id="man-s1",
            dna_json=make_dna(direction="short").to_json(),
            symbol="BTCUSDT", timeframe="4h", name="ManualOne",
            source="manual", best_score=99.0,
        )

    def test_global_list_filters_evolution_source(
        self, api_client: TestClient,
    ) -> None:
        """/api/evolution/strategies 只列 source=evolution 策略。"""
        self._seed_evolution_strategy(api_client.app.state.db_path)
        response = api_client.get("/api/evolution/strategies")
        assert response.status_code == 200
        items = response.json()
        ids = [i["strategy_id"] for i in items]
        assert "evo-s1" in ids
        assert "man-s1" not in ids
        evo = next(i for i in items if i["strategy_id"] == "evo-s1")
        assert evo["score"] == 90.0
        assert evo["source"] == "evolution"
        assert evo["dna"] is not None

    def test_global_list_min_score_filter(self, api_client: TestClient) -> None:
        """min_score 过滤低于阈值的策略。"""
        self._seed_evolution_strategy(api_client.app.state.db_path)
        response = api_client.get(
            "/api/evolution/strategies", params={"min_score": 95.0},
        )
        assert [i["strategy_id"] for i in response.json()] == []

    def test_task_discovered_strategies_scoped_to_task(
        self, api_client: TestClient,
    ) -> None:
        """/tasks/{id}/discovered-strategies 按 source_task_id 过滤。"""
        self._seed_evolution_strategy(api_client.app.state.db_path)
        db_path = api_client.app.state.db_path
        _make_task_row(db_path, "task-a")
        response = api_client.get("/api/evolution/tasks/task-a/discovered-strategies")
        assert response.status_code == 200
        ids = [i["strategy_id"] for i in response.json()]
        assert ids == ["evo-s1"]

    def test_task_strategies_champion_and_snapshot(
        self, api_client: TestClient,
    ) -> None:
        """/tasks/{id}/strategies 合成 champion 与快照策略并按 id 去重。"""
        from core.persistence.db import save_snapshot, update_task
        db_path = api_client.app.state.db_path
        _make_task_row(db_path, "task-b", status="running")

        champ_dna = make_dna()
        champ_dna.strategy_id = "champ-1"
        update_task(db_path, "task-b", champion_dna=champ_dna)

        # 同 id 快照应被 champion 去重；异 id 快照独立出现
        snap_same = make_dna(direction="short")
        snap_same.strategy_id = "champ-1"
        save_snapshot(db_path, "task-b", 1, best_score=1.0, avg_score=0.5,
                      best_dna=snap_same, population=[])
        snap_other = make_dna(indicator="EMA")
        snap_other.strategy_id = "snap-2"
        save_snapshot(db_path, "task-b", 2, best_score=2.0, avg_score=1.0,
                      best_dna=snap_other, population=[])

        response = api_client.get("/api/evolution/tasks/task-b/strategies")
        assert response.status_code == 200
        strategies = response.json()["strategies"]
        ids = [s["strategy_id"] for s in strategies]
        assert ids.count("champ-1") == 1  # champion 去重
        assert "snap-2" in ids
        by_id = {s["strategy_id"]: s for s in strategies}
        assert by_id["champ-1"]["source"] == "champion"
        assert by_id["snap-2"]["source"] == "snapshot"

    def test_task_strategies_no_champion_empty(
        self, api_client: TestClient,
    ) -> None:
        """无 champion 且无快照时 strategies 为空列表。"""
        db_path = api_client.app.state.db_path
        _make_task_row(db_path, "task-c")
        response = api_client.get("/api/evolution/tasks/task-c/strategies")
        assert response.status_code == 200
        assert response.json()["strategies"] == []

    def test_task_strategies_unknown_task_404(self, api_client: TestClient) -> None:
        response = api_client.get("/api/evolution/tasks/ghost/strategies")
        assert response.status_code == 404


class TestLifecycleGuards:
    def test_pause_pending_task_409(self, api_client: TestClient) -> None:
        """pause 前态守护 {running}：pending 任务 pause → 409。"""
        db_path = api_client.app.state.db_path
        _make_task_row(db_path, "task-p", status="pending")
        response = api_client.post("/api/evolution/tasks/task-p/pause")
        assert response.status_code == 409

    def test_resume_pending_task_400(self, api_client: TestClient) -> None:
        """resume 前态守护 {paused}：pending 任务 resume → 400。"""
        db_path = api_client.app.state.db_path
        _make_task_row(db_path, "task-r", status="pending")
        response = api_client.post("/api/evolution/tasks/task-r/resume")
        assert response.status_code == 400

    def test_stop_completed_task_409(self, api_client: TestClient) -> None:
        """stop 对已完结任务 → 409（Task already finished）。"""
        db_path = api_client.app.state.db_path
        _make_task_row(db_path, "task-done", status="completed")
        response = api_client.post("/api/evolution/tasks/task-done/stop")
        assert response.status_code == 409

    def test_resume_unknown_task_404(self, api_client: TestClient) -> None:
        response = api_client.post("/api/evolution/tasks/ghost/resume")
        assert response.status_code == 404

    def test_pause_paused_task_via_running_flow(self, api_client: TestClient) -> None:
        """running → pause → paused 的合法流 + 二次 pause 409。"""
        db_path = api_client.app.state.db_path
        _make_task_row(db_path, "task-run", status="running")
        first = api_client.post("/api/evolution/tasks/task-run/pause")
        assert first.status_code == 200
        assert first.json()["status"] == "paused"
        second = api_client.post("/api/evolution/tasks/task-run/pause")
        assert second.status_code == 409


class TestSortTimeframes:
    def test_sort_longest_first(self) -> None:
        """sort_timeframes 按时长降序（最长在前）。"""
        from api.routes.evolution import sort_timeframes
        assert sort_timeframes(["4h", "1d", "1h", "15m"]) == ["1d", "4h", "1h", "15m"]

    def test_sort_unknown_tf_falls_to_zero(self) -> None:
        """未知 timeframe 秒数为 0，排最后。"""
        from api.routes.evolution import sort_timeframes
        assert sort_timeframes(["4h", "weird"]) == ["4h", "weird"]


class TestTaskPersistenceInconsistency:
    """DB 写读不一致防御：创建后读取不到 → 500；历史查无任务 → 404。

    注意 mock 身份：api_client fixture 经 MyQuant.api.app.create_app 装配，
    路由闭包引用的是 MyQuant 前缀身份的模块属性——patch 裸 api.* 身份会
    静默扑空（表现为 mock 不生效、真实路径照跑），必须 patch.object 到
    sys.modules 中的同身份模块。
    """

    def test_create_task_row_missing_500(self, api_client, monkeypatch):
        import sys

        rte = sys.modules["MyQuant.api.routes.evolution"]
        monkeypatch.setattr(rte, "get_task", lambda *a, **kw: None)
        resp = api_client.post("/api/evolution/tasks", json=_create_payload())
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Failed to create task"

    def test_history_unknown_task_404(self, api_client):
        resp = api_client.get("/api/evolution/tasks/no-such-task/history")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Task not found"


class TestRowTransformCorruptJsonDegrades:
    """行转换层坏 JSON 容错：initial_dna / dna_json 解析失败 → None
    降级不炸（存量脏行防御）。"""

    def test_task_row_bad_initial_dna_degrades(self, db_path):
        from api.routes.evolution import _task_row_to_response
        from core.persistence.db import get_task, init_db

        init_db(db_path)
        _make_task_row(db_path, "t_bad_dna")
        row = get_task(db_path, "t_bad_dna")
        row["initial_dna"] = "{not valid json"
        resp = _task_row_to_response(row)
        assert resp.initial_dna is None

    def test_strategies_to_dicts_bad_dna_json_degrades(self):
        from api.routes.evolution import _strategies_to_dicts

        rows = [{"strategy_id": "s_badjson", "best_score": 1.0,
                 "dna_json": "not-json-at-all"}]
        out = _strategies_to_dicts(rows, None)
        assert len(out) == 1
        assert out[0]["dna"] is None
        assert out[0]["strategy_id"] == "s_badjson"


class TestCreateTaskParquetTsProbeBoundaries:
    """POST /tasks 数据探测边界：0 行 parquet 跳过时间探测回显、
    无时间列 parquet 跳过时间探测（行数照常回显）。"""

    @staticmethod
    def _client_with_parquet(db_path, tmp_path, df):
        import pandas as pd
        from MyQuant.api.app import create_app

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        df.to_parquet(data_dir / "BTCUSDT_4h.parquet")
        app = create_app(db_path=db_path, data_dir=data_dir)
        return TestClient(app)

    def test_zero_row_parquet_skips_ts_probe(self, db_path, tmp_path):
        import pandas as pd

        df = pd.DataFrame({
            "open": pd.Series([], dtype="float64"),
            "high": pd.Series([], dtype="float64"),
            "low": pd.Series([], dtype="float64"),
            "close": pd.Series([], dtype="float64"),
            "volume": pd.Series([], dtype="float64"),
        })
        with self._client_with_parquet(db_path, tmp_path, df) as c:
            resp = c.post("/api/evolution/tasks", json=_create_payload())
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["data_row_count"] == 0
        assert data["data_time_start"] is None
        assert data["data_time_end"] is None

    def test_no_ts_column_parquet_skips_ts_probe(self, db_path, tmp_path):
        import pandas as pd

        df = pd.DataFrame({
            "a": [1.0, 2.0, 3.0],
            "b": [4.0, 5.0, 6.0],
        })
        with self._client_with_parquet(db_path, tmp_path, df) as c:
            resp = c.post("/api/evolution/tasks", json=_create_payload())
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["data_row_count"] == 3
        assert data["data_time_start"] is None
        assert data["data_time_end"] is None


    def test_task_row_without_initial_dna_key(self, db_path):
        """row 无 initial_dna（键缺失）→ 跳过解析块直取 None。"""
        from api.routes.evolution import _task_row_to_response
        from core.persistence.db import get_task, init_db

        init_db(db_path)
        _make_task_row(db_path, "t_no_dna")
        row = get_task(db_path, "t_no_dna")
        row.pop("initial_dna", None)
        resp = _task_row_to_response(row)
        assert resp.initial_dna is None

    def test_strategies_to_dicts_without_dna_json_key(self):
        """策略行无 dna_json 键 → dna=None 直通（不进 try 块）。"""
        from api.routes.evolution import _strategies_to_dicts

        rows = [{"strategy_id": "s_nodna", "best_score": 1.0}]
        out = _strategies_to_dicts(rows, None)
        assert out[0]["dna"] is None
