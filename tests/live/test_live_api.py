
import pytest

pytestmark = [pytest.mark.integration]  # B10.1 标记激活
"""第8步 实盘 API 单元测试（独立 FastAPI app + dependency_overrides，不连实盘）。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_db_path
from api.routes.live_trading import router as live_router
from core.persistence.db_ext import init_db_ext  # B3c.1：live 建表并入 init 链


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
        json={"symbol": symbol, "timeframe": "1h", "dna_json": "{}", "direction": direction},
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
