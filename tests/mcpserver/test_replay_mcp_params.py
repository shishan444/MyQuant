"""T4：MCP run_replay_backtest 注册面对账（fix-replay-gap-alignment 2.4）。

缺陷 D：strategy_id 标注 Optional 却无默认值——FastMCP 对无默认值
参数声明 required:true，AI 只传 strategy_ids 列表被参数校验层拦截
（E4 探针实测 required=['strategy_id','start','end']），与 docstring
「并发组可独立发起」直接矛盾。修复=补 =None 缺省（声明/schema 校验
/docstring 三面一致）。

红基线：E4 探针代证（修复前 strategy_id 在 required）。
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = [pytest.mark.unit]

from api.schemas import ReplayBacktestRequest
from tests.replay._env import ReplayEnv


def _tool_schema() -> dict:
    """进程内 build_server 提取 run_replay_backtest.input_schema。"""
    from mcpserver.server import build_server

    mcp, _api = build_server(profile="research")
    tools = asyncio.run(mcp.list_tools())
    target = next(t for t in tools if t.name == "run_replay_backtest")
    return target.input_schema


class TestT4aRequiredSetAlignment:
    """T4-a：required 集与 REST 基准（ReplayBacktestRequest）一致。"""

    def test_required_set_matches_rest_baseline(self):
        schema = _tool_schema()
        required = set(schema.get("required", []))
        assert "strategy_id" not in required, (
            f"单发参数不该必填（E4 现状缺陷——实测 required={sorted(required)}）")
        assert "strategy_ids" not in required, "并发组参数不该必填"
        assert {"start", "end"} <= required, "区间参数保持必填"
        # properties 集与 REST schema 字段面对齐（REST 基准字段 ⊆ 工具
        # 声明面；工具面无 REST 外私增字段）
        props = set(schema.get("properties", {}))
        rest_fields = set(ReplayBacktestRequest.model_fields)
        assert rest_fields <= props, (
            f"REST 字段缺失于工具声明：{sorted(rest_fields - props)}")
        assert props <= rest_fields, (
            f"工具声明私增字段：{sorted(props - rest_fields)}")


class TestT4bOptionalityConsistency:
    """T4-b：两入口可选性逐参数一致+docstring 披露级断言。"""

    def test_optionality_per_param_consistent(self):
        schema = _tool_schema()
        required = set(schema.get("required", []))
        mismatches = []
        for name, field in ReplayBacktestRequest.model_fields.items():
            rest_optional = field.is_required() is False
            mcp_optional = name not in required
            if rest_optional != mcp_optional:
                mismatches.append(
                    f"{name}: REST 可选={rest_optional} MCP 可选="
                    f"{mcp_optional}")
        assert not mismatches, f"两入口可选性漂移：{mismatches}"

    def test_docstring_discloses_independent_batch_form(self):
        """docstring 关键语在场（披露级——并发组独立发起声明）。"""
        import inspect

        from mcpserver.server import build_server
        import mcpserver.tools as tools_mod

        src = inspect.getsource(tools_mod)
        # 工具 docstring 经装饰器包装——源文本断言（披露级锚）
        assert "至少其一" in src, "两形态至少其一声明在场"
        assert "并发组可独立发起" in src, "独立发起声明在场"


class TestT4cRestSemantics:
    """T4-c：皆缺=422 拒绝维持；仅 strategy_ids=受理返回（锚 3 功能面
    ——results[i].strategy_id 与请求 strategy_ids[i] 逐位对位，序断言
    禁 set 比较——as_completed 式乱序收集必红）。"""

    def _client(self, env):
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(db_path=env.db, data_dir=env.data_dir)
        app.state.db_path = env.db
        app.state.data_dir = env.data_dir
        return TestClient(app, raise_server_exceptions=False)

    def test_both_missing_rejected_422(self, tmp_path):
        env = ReplayEnv(tmp_path, n_bars=600)
        client = self._client(env)
        start, end = env.range(head_bars=200)
        resp = client.post("/api/research/backtest-replay", json={
            "start": start, "end": end})
        assert resp.status_code == 422, (
            f"两者全无应 422 拒绝（实测 {resp.status_code}）")

    def test_strategy_ids_only_accepted_results_ordered(self, tmp_path):
        from core.persistence.repository.strategy import save_strategy
        from tests.helpers.data_factory import make_dna

        env = ReplayEnv(tmp_path, n_bars=600)
        sids = [env.strategy_id]
        for i in range(1, 3):
            sid = f"st_t4c_{i}"
            save_strategy(env.db, strategy_id=sid,
                          dna_json=make_dna(
                              symbol="BTCUSDT", leverage=1 + i).to_json(),
                          symbol="BTCUSDT", timeframe="4h", status="draft")
            sids.append(sid)
        client = self._client(env)
        start, end = env.range(head_bars=100)
        resp = client.post("/api/research/backtest-replay", json={
            "strategy_ids": sids, "start": start, "end": end})
        assert resp.status_code == 200, (
            f"仅 strategy_ids 应受理（实测 {resp.status_code}: "
            f"{resp.text[:200]}）")
        body = resp.json()
        results = body["results"]
        assert [r["strategy_id"] for r in results] == sids, (
            "results 序=strategy_ids 序（逐位对位——乱序收集必红）")
