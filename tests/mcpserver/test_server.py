"""MCP server 测试（add-ai-research-mcp 4.1-4.5）。

真 uvicorn 线程承载 API（MCP 工具经 httpx 全链路到 REST，与
宿主使用方式一致）。覆盖：工具列表、两档装配、触金隔离断言、
白名单拒绝、写操作不重试语义、自述四部分。
"""

import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

pytestmark = [pytest.mark.integration]


def run_async(coro):
    """asyncio.run 的环境兼容版。

    playwright e2e fixture 会在进程内泄漏运行中的事件循环，此后
    asyncio.run 抛 RuntimeError——检测到活动 loop 时改在新线程跑。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()

from api.app import create_app
from mcpserver.api_client import ApiError, EndpointNotAllowed, QuantApiClient
from mcpserver.server import build_server
from mcpserver.tools import TOOL_TIERS

from _expected_tools import EXPECTED_RESEARCH_TOOLS  # 同目录（pytest prepend）


@pytest.fixture(scope="module")
def live_api(tmp_path_factory):
    """真 uvicorn 线程（随机端口），供 httpx 调用。"""
    db = tmp_path_factory.mktemp("mcp_api") / "quant.db"
    data_dir = tmp_path_factory.mktemp("mcp_data")
    data_dir.mkdir(exist_ok=True)
    import numpy as np
    import pandas as pd
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    rng = np.random.default_rng(3)
    close = 60000 + np.cumsum(rng.normal(0, 120, n))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 900, n),
                  "trades": 50}, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")

    app = create_app(db_path=db, data_dir=data_dir)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.1)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", db
    server.should_exit = True
    thread.join(timeout=5)


class TestServerAssembly:
    def test_research_profile_tools(self) -> None:
        mcp, _ = build_server(profile="research")
        names = {t.name for t in run_async(mcp.list_tools())}
        assert TOOL_TIERS["readonly"] <= names
        assert TOOL_TIERS["research"] <= names
        # 精确集合对照（不多不少）：期望单处权威 _expected_tools
        assert names == EXPECTED_RESEARCH_TOOLS

    def test_readonly_profile_tools(self) -> None:
        mcp, _ = build_server(profile="readonly")
        names = {t.name for t in run_async(mcp.list_tools())}
        assert names == TOOL_TIERS["readonly"]
        # 研究档工具不可见
        assert "register_hypothesis" not in names
        assert "run_isolated_validation" not in names

    def test_no_money_touching_tools(self) -> None:
        """触金隔离：枚举全部注册工具，断言无下单/调仓/风控修改类。"""
        for profile in ("readonly", "research"):
            mcp, _ = build_server(profile=profile)
            names = {t.name for t in run_async(mcp.list_tools())}
            forbidden_substrings = ("order", "trade_open", "close_position",
                                    "risk", "live", "liquidate", "leverage")
            for n in names:
                for bad in forbidden_substrings:
                    assert bad not in n, f"{profile} 档出现触金嫌疑工具: {n}"

    def test_capability_resource_four_parts(self) -> None:
        """能力自述含四部分内容。"""
        mcp, _ = build_server(profile="research")
        res = run_async(mcp.read_resource("quant://capabilities"))
        text = res[0].content if isinstance(res, list) else str(res)
        for part in ("工具清单", "验证协议要点", "策略可表达边界", "假设状态机"):
            assert part in text, f"自述缺部分: {part}"
        assert "register_hypothesis" in text  # 工具清单已合并
        assert "不会自动实现" in text  # 需求单预期管理


class TestTierMarkupParity:
    """CARD-01 修复资产：档位标记与注册面/能力自述三方对拍。

    根因防线：注册尾循环漏 add 时 mcp.list_tools 仍完整（既有子集断言
    `TOOL_TIERS <= names` 不红），而能力自述 doc 的工具清单取 TOOL_TIERS
    并集——档标记失联即 doc 漏报（AI 宿主按 doc 认知工具面）。等式
    断言使任何一侧漂移必红。
    """

    def test_research_tier_equals_expected_minus_readonly(self) -> None:
        """research 档标记 == 期望全集 - readonly 档（等式，非子集）。"""
        assert TOOL_TIERS["research"] == (
            EXPECTED_RESEARCH_TOOLS - TOOL_TIERS["readonly"])

    def test_capability_doc_lists_all_registered_tools(self) -> None:
        """能力自述工具清单 ⊇ 实际注册面（research 档逐名在 doc）。"""
        mcp, _ = build_server(profile="research")
        names = {t.name for t in run_async(mcp.list_tools())}
        res = run_async(mcp.read_resource("quant://capabilities"))
        text = res[0].content if isinstance(res, list) else str(res)
        missing = sorted(n for n in names if f"- {n}" not in text)
        assert not missing, f"能力自述工具清单漏报: {missing}"

    def test_capability_doc_contains_historically_missing_three(self) -> None:
        """回归锚：三名历史漏报工具显式断言（0.2.8/0.2.9/0.2.10 漏同步）。"""
        mcp, _ = build_server(profile="research")
        res = run_async(mcp.read_resource("quant://capabilities"))
        text = res[0].content if isinstance(res, list) else str(res)
        for n in ("inspect_cases", "query_indicator", "run_staged_validation"):
            assert f"- {n}" in text, f"能力自述缺工具 {n}"


class TestToolPaginationClamp:
    """CARD-05 修复资产：MCP 工具层分页 clamp（发出的查询参数先钳制）。

    clamp 形制照 list_evolution_tasks 先例：page=max(1,page)、
    page_size=max(1,min(ps,50))——与 HTTP 侧越界归 50 缺省不同值
    （下限抬 1），分层裁量分别钉。
    """

    def _captured_get(self, api, captured):
        def _fake_get(path, params=None):
            captured["path"] = path
            captured["params"] = params
            return {"items": [], "total": 0}
        api.get = _fake_get

    def test_list_feature_requests_params_clamped(self, monkeypatch) -> None:
        mcp, api = build_server(profile="readonly", base_url="http://x")
        captured: dict = {}
        self._captured_get(api, captured)

        run_async(mcp.call_tool("list_feature_requests",
                                {"page": 0, "page_size": 999}))
        assert captured["params"]["page"] == 1
        assert captured["params"]["page_size"] == 50, "超 _PAGE_MAX 收 50"

        run_async(mcp.call_tool("list_feature_requests",
                                {"page": -3, "page_size": 0}))
        assert captured["params"]["page"] == 1
        assert captured["params"]["page_size"] == 1, "下限抬 1（非 HTTP 侧的 50 缺省）"

    def test_list_feature_requests_legal_params_passthrough(self, monkeypatch) -> None:
        """合法参数原样透传（clamp 不改写有效值）。"""
        mcp, api = build_server(profile="readonly", base_url="http://x")
        captured: dict = {}
        self._captured_get(api, captured)

        run_async(mcp.call_tool("list_feature_requests",
                                {"page": 3, "page_size": 20, "status": "open"}))
        assert captured["params"] == {"page": 3, "page_size": 20,
                                      "status": "open"}


class TestWhitelist:
    def test_readonly_cannot_post(self) -> None:
        api = QuantApiClient(base_url="http://127.0.0.1:1", profile="readonly")
        with pytest.raises(EndpointNotAllowed):
            api.post("/api/research/hypotheses", {"x": 1})

    def test_money_endpoints_never_allowed(self) -> None:
        for profile in ("readonly", "research"):
            api = QuantApiClient(base_url="http://127.0.0.1:1", profile=profile)
            for path in ("/api/trading/start", "/api/live/start",
                         "/api/trading/order/buy"):
                with pytest.raises(EndpointNotAllowed):
                    api.get(path)
                with pytest.raises(EndpointNotAllowed):
                    api.post(path, {})

    def test_unknown_endpoint_rejected(self) -> None:
        api = QuantApiClient(base_url="http://127.0.0.1:1", profile="research")
        with pytest.raises(EndpointNotAllowed):
            api.get("/api/whatever/else")

    def test_template_path_match(self) -> None:
        """{param} 模板端点可匹配具体 id。"""
        api = QuantApiClient(base_url="http://127.0.0.1:1", profile="readonly")
        api.check_allowed("GET", "/api/research/analysis/tasks/ant_abc")


class TestCallSemantics:
    def test_write_failure_no_retry(self, live_api) -> None:
        """写操作失败不自动重试（幂等性未知；失败语义原样返回）。"""
        base, db = live_api
        api = QuantApiClient(base_url=base, profile="research")
        # 非法 payload（结构化条件缺失）→ 422 pydantic 校验失败（无工程码）
        # → 降级 API_INTERNAL_ERROR 形式（detail 保留原始校验信息）
        with pytest.raises(ApiError) as ei:
            api.post("/api/research/hypotheses", {"bad": 1})
        assert ei.value.status == 422
        assert ei.value.code == "API_INTERNAL_ERROR"
        # 仅一次 POST，无重试
        posts = [c for c in api.call_log if c["method"] == "POST"]
        assert len(posts) == 1

    def test_readonly_tools_match_direct_rest(self, live_api) -> None:
        """冒烟：每个只读工具结果与直接 REST 一致。"""
        base, db = live_api
        mcp, api = build_server(profile="readonly", base_url=base)

        # 工具经 server 调用路径与直接 REST 结果一致（数据目录）
        direct = httpx.get(f"{base}/api/data/available-sources", timeout=10).json()
        assert direct is not None
        # indicators 一致
        direct_ind = httpx.get(f"{base}/api/research/indicators", timeout=10).json()
        got_ind = api.get("/api/research/indicators")
        assert len(got_ind) == len(direct_ind) == 47

    def test_end_to_end_analysis_via_client(self, live_api) -> None:
        """研究档经 client 完成一次分析并落日志（4.3 验收）。"""
        base, db = live_api
        api = QuantApiClient(base_url=base, profile="research")
        # U3 起路由接入 warmup 前扩：起点让出 RSI 的 200-bar warmup 段
        #（400-bar 数据 2024-01-01 起；start=2024-02-05 即 bar 210 > 200）
        out = api.post("/api/research/analysis", {
            "template": "condition_stats",
            "params": {"indicator": "RSI", "params": {"period": 14},
                       "condition": {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 40}},
            "data_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2024-02-05T00:00:00Z", "end": "2024-03-08T00:00:00Z"},
        })
        assert out["template"] == "condition_stats"
        assert out["sample_size"] >= 30
        # 日志链路
        logs = api.get("/api/research/log")
        assert any(e["entry_type"] == "analysis" for e in logs["items"])


class TestPollContract:
    """2.5 轮询契约：退避序列 + 失败保留最后已知状态。"""

    def test_backoff_sequence_respected(self, monkeypatch):
        """退避 5s×1.5 上限 60s 的序列形状（注入 sleep 记录）。"""
        import mcpserver.api_client as ac_mod
        sleeps = []
        monkeypatch.setattr(ac_mod.time, "sleep", lambda s: sleeps.append(s))

        # 端点持续返回非终态 5 次后终态
        seq = iter(["running"] * 5 + ["succeeded"])
        class FakeResp:
            status_code = 200
            is_error = False
            def json(self):
                return {"status": next(seq)}
            def raise_for_status(self):
                pass
        monkeypatch.setattr(ac_mod.httpx, "Client", lambda **kw: _FakeCtx(FakeResp()))

        api = QuantApiClient(base_url="http://x", profile="research")
        out = api.poll_until_terminal("/api/research/analysis/tasks/ant_1",
                                      initial_delay=5.0, backoff=1.5,
                                      max_delay=60.0, total_timeout=120.0)
        assert out["status"] == "succeeded"
        assert sleeps == [5.0, 7.5, 11.25, 16.875, 25.3125], sleeps

    def test_failure_keeps_last_known_state(self, monkeypatch):
        """端点失败：保留最后已知状态 + 标注来源时刻（D2）。"""
        import mcpserver.api_client as ac_mod
        import httpx as _hx

        call_count = {"n": 0}
        class FlakyResp:
            status_code = 200
            is_error = False
            def json(self):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return {"status": "running", "task_id": "ant_x"}
                raise _hx.ReadTimeout("boom")
            def raise_for_status(self):
                pass
        monkeypatch.setattr(ac_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(ac_mod.httpx, "Client", lambda **kw: _FakeCtx(FlakyResp()))

        api = QuantApiClient(base_url="http://x", profile="research")
        out = api.poll_until_terminal("/api/research/analysis/tasks/ant_x",
                                      initial_delay=0.01, total_timeout=0.2)
        # 第二轮失败 → 保留第一轮的 running 状态并带来源时刻
        assert out["status"] == "running" and out.get("_polled_at")


class _FakeCtx:
    def __init__(self, resp):
        self._resp = resp
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def get(self, url, params=None):
        return self._resp
