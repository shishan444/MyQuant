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

    def test_update_metadata_name_only(self, env):
        """name 单独更新分支（闸门盲区消解——body 组装三参数各走一遭：
        本用例 name、既有用例 tags+notes/notes）。"""
        save_strategy(env["db"], strategy_id="st_p3",
                      dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="旧名",
                      tags="keep3")
        fn = self._mcp_fn(env, "update_strategy_metadata")
        out = fn(strategy_id="st_p3", name="新名")
        assert "error" not in out
        g = env["client"].get("/api/strategies/st_p3").json()
        assert g["name"] == "新名" and g["tags"] == "keep3"
