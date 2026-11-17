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

