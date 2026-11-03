"""be-plugin-pool-authority 端点测试（D10 池权威）。

正向（四池逐字对账）/零参幂等/无副作用+单源变异（POOLS 增项响应跟随）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

EXPECTED_FIRST_ITEMS = ["ATR 自适应", "EWMA 追踪线", "卡尔曼追踪线",
                        "卡尔曼方向信号", "小波去噪", "series 结构线（限顺耦合）",
                        "方向门控", "FundingPressure", "BB 通道"]


@pytest.fixture()
def client(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext
    from api.app import create_app
    db = tmp_path / "q.db"
    init_db_ext(db)
    app = create_app(db_path=db, data_dir=tmp_path / "data")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestPluginPools:
    def test_four_pools_verbatim(self, client):
        r = client.get("/api/research/plugins/pools")
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "engineering_authority"
        pools = body["pools"]
        assert [p["name"] for p in pools] == ["默认池", "候选池", "黑名单", "观察池"]
        # 逐字对账（首池全 9 项——移植零差异锚）
        assert pools[0]["items"] == EXPECTED_FIRST_ITEMS
        assert pools[2]["items"][0] == "trail 2%"  # 黑名单首项

    def test_idempotent_zero_param(self, client):
        r1 = client.get("/api/research/plugins/pools")
        r2 = client.get("/api/research/plugins/pools")
        assert r1.json() == r2.json()  # 纯 GET 幂等

    def test_no_side_effect(self, client, tmp_path):
        """无副作用：库文件 mtime 不因请求变化。"""
        import time
        db_files = list(tmp_path.glob("*.db"))
        before = [f.stat().st_mtime_ns for f in db_files]
        client.get("/api/research/plugins/pools")
        time.sleep(0.01)
        after = [f.stat().st_mtime_ns for f in db_files]
        assert before == after

    def test_single_source_mutation_follows(self, client, monkeypatch):
        """单源变异：POOLS 增项响应跟随（权威在 core 模块非端点抄录）。"""
        import core.strategy.pools as pools_mod
        original = pools_mod.POOLS
        mutated = [dict(p) for p in original]
        mutated.append({"name": "测试池", "note": "变异项", "items": ["x"]})
        monkeypatch.setattr(pools_mod, "POOLS", mutated)
        r = client.get("/api/research/plugins/pools")
        names = [p["name"] for p in r.json()["pools"]]
        assert "测试池" in names  # 响应跟随单源
        assert len(names) == 5
        monkeypatch.setattr(pools_mod, "POOLS", original)
