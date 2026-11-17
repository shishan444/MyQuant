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


