
import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活
"""第8步 实盘 API 单元测试（独立 FastAPI app + dependency_overrides，不连实盘）。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_db_path
from api.routes.live_trading import router as live_router
from core.persistence.db_ext import init_db_ext  # B3c.1：live 建表并入 init 链
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)

# 最小合法 live DNA（review-042 CARD-12 适配）：创建点起经 validate_dna+
# validate_trading_compatibility 校验，空 "{}" 不再可入——本文件焦点在
# CRUD/状态机，DNA 用 pct 档 entry+exit 最小形（live 链兼容）
_VALID_DNA_JSON = StrategyDNA(
    signal_genes=[
        SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                   condition={"type": "lt", "threshold": 30}),
        SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                   condition={"type": "gt", "threshold": 70}),
    ],
    logic_genes=LogicGenes(),
    execution_genes=ExecutionGenes(timeframe="1h"),
).to_json()


def _app(db_path):
    app = FastAPI()
    app.include_router(live_router)
    app.dependency_overrides[get_db_path] = lambda: db_path
    return app


def _client(tmp_path):
    db = tmp_path / "live.db"
    init_db_ext(db)
    return TestClient(_app(db))


def _create(client, symbol="BTCUSDT", direction="mixed"):
    r = client.post(
        "/api/live-trading/tasks",
        json={"symbol": symbol, "timeframe": "1h", "dna_json": _VALID_DNA_JSON, "direction": direction},
    )
    assert r.status_code == 201
    return r.json()["task_id"]


class TestCrud:
    def test_create(self, tmp_path):
        c = _client(tmp_path)
        tid = _create(c)
        assert tid.startswith("lt_")

    def test_get(self, tmp_path):
        c = _client(tmp_path)
        tid = _create(c)
        r = c.get(f"/api/live-trading/tasks/{tid}")
        assert r.status_code == 200
        assert r.json()["symbol"] == "BTCUSDT"
        assert r.json()["status"] == "pending"

    def test_list(self, tmp_path):
        c = _client(tmp_path)
        _create(c)
        _create(c, symbol="ETHUSDT")
        r = c.get("/api/live-trading/tasks")
        assert r.json()["total"] == 2

    def test_list_filter_status(self, tmp_path):
        c = _client(tmp_path)
        tid = _create(c)
        c.patch(f"/api/live-trading/tasks/{tid}", json={"status": "running"})
        r = c.get("/api/live-trading/tasks", params={"status": "running"})
        assert r.json()["total"] == 1
        r = c.get("/api/live-trading/tasks", params={"status": "pending"})
        assert r.json()["total"] == 0

    def test_patch(self, tmp_path):
        c = _client(tmp_path)
        tid = _create(c)
        r = c.patch(f"/api/live-trading/tasks/{tid}", json={"status": "running", "config_json": '{"r":1}'})
        assert r.json()["status"] == "running"
        assert r.json()["config_json"] == '{"r":1}'

    def test_delete(self, tmp_path):
        c = _client(tmp_path)
        tid = _create(c)
        assert c.delete(f"/api/live-trading/tasks/{tid}").status_code == 200
        assert c.get(f"/api/live-trading/tasks/{tid}").status_code == 404


class TestNotFound:
    def test_get_404(self, tmp_path):
        c = _client(tmp_path)
        assert c.get("/api/live-trading/tasks/nope").status_code == 404

    def test_patch_404(self, tmp_path):
        c = _client(tmp_path)
        assert c.patch("/api/live-trading/tasks/nope", json={"status": "running"}).status_code == 404

    def test_delete_404(self, tmp_path):
        c = _client(tmp_path)
        assert c.delete("/api/live-trading/tasks/nope").status_code == 404


class TestPatchStatusValueDomain:
    """CARD-03 值域锚（入站层）：status 值域 Literal 四值——非法串原可
    入库（列表过滤与状态判定消费方失真），入站即拒 422。"""

    @pytest.mark.parametrize("status", ["pending", "running", "stopped", "error"])
    def test_合法四值可写(self, tmp_path, status):
        c = _client(tmp_path)
        tid = _create(c)
        r = c.patch(f"/api/live-trading/tasks/{tid}", json={"status": status})
        assert r.status_code == 200
        assert r.json()["status"] == status

    @pytest.mark.parametrize("bad", ["hacked", "RUNNING", " stopped", ""])
    def test_非法串422且任务不变(self, tmp_path, bad):
        c = _client(tmp_path)
        tid = _create(c)
        r = c.patch(f"/api/live-trading/tasks/{tid}", json={"status": bad})
        assert r.status_code == 422, f"{bad!r} 不得入站"
        assert c.get(f"/api/live-trading/tasks/{tid}").json()["status"] == "pending", \
            "被拒请求不得产生部分写入"

    def test_不传status时其他键直通(self, tmp_path):
        c = _client(tmp_path)
        tid = _create(c)
        r = c.patch(f"/api/live-trading/tasks/{tid}",
                    json={"config_json": '{"k":1}', "stop_reason": "manual"})
        assert r.status_code == 200
        assert r.json()["status"] == "pending", "缺省 None 直通不误伤 status"
