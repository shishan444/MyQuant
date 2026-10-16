"""rebuild-paper-trading 5.3：纸盘工具族六件行为锚。

覆盖任务完成标准：六工具集合断言（沿 AST 对拍形制——test_contract_
parity 的提取器+TOOL_TIERS 档位归属）；mount 参数面与 REST POST
/api/paper-trading/tasks 完全对拍（无策略语义参数）；触金封禁集合
断言（新端点与 FORBIDDEN_PATTERNS 前缀零相交）；EndpointNotAllowed
路径核验（白名单两档分派+未登记面拒绝）；capability 自述含新工具与
挂载语义说明；工具行为（RecordingFakeApi 对拍——body 组装/路径分派/
非法 type 结构化拒绝）。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

pytestmark = [pytest.mark.integration]


def run_async(coro):
    """asyncio.run 环境兼容版（同 test_server 形制）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


PAPER_READONLY_TOOLS = frozenset({
    "list_paper_tasks", "get_paper_task", "get_paper_report",
    "get_paper_history"})
PAPER_MOUNT_TOOLS = frozenset({"mount_paper_task", "unmount_paper_task"})


def _register(profile: str):
    """按档位注册到捕获型 FakeMcp（conftest 共享基座）。"""
    from conftest import _FakeMcp, RecordingFakeApi

    mcp = _FakeMcp()
    api = RecordingFakeApi()
    from mcpserver.tools import (
        register_readonly_tools,
        register_research_tools,
    )

    register_readonly_tools(mcp, api)
    if profile == "research":
        register_research_tools(mcp, api)
    return mcp, api


class TestPaperToolSet:
    """六工具集合与档位归属（AST 对拍形制的集合断言面）。"""

    def test_six_tools_registered_and_tiered(self) -> None:
        from mcpserver.tools import TOOL_TIERS

        mcp, _ = _register("research")
        names = set(mcp.registered)
        assert PAPER_READONLY_TOOLS <= names
        assert PAPER_MOUNT_TOOLS <= names
        # 档位归属：只读四件进 readonly、挂载两件仅 research
        assert PAPER_READONLY_TOOLS <= TOOL_TIERS["readonly"]
        assert not (PAPER_MOUNT_TOOLS & TOOL_TIERS["readonly"])
        assert PAPER_MOUNT_TOOLS <= TOOL_TIERS["research"]

    def test_readonly_profile_excludes_mount(self) -> None:
        """readonly 档：只读四件可见、挂载两件不可见。"""
        mcp, _ = _register("readonly")
        names = set(mcp.registered)
        assert PAPER_READONLY_TOOLS <= names
        assert not (PAPER_MOUNT_TOOLS & names)

    def test_expected_tools_carry_six(self) -> None:
        """期望清单同步（_expected_tools 单处权威含六件——与 test_server
        全集断言联动）。"""
        from _expected_tools import EXPECTED_RESEARCH_TOOLS

        assert (PAPER_READONLY_TOOLS | PAPER_MOUNT_TOOLS) <= \
            EXPECTED_RESEARCH_TOOLS

    def test_tool_endpoints_in_whitelists(self) -> None:
        """AST 对拍（test_contract_parity 提取器）：六工具全部端点在
        档位白名单。"""
        from test_contract_parity import _extract_tool_calls
        from mcpserver.api_client import (
            READONLY_ENDPOINTS,
            RESEARCH_POST_ENDPOINTS,
        )

        calls = _extract_tool_calls()
        for tool in (PAPER_READONLY_TOOLS | PAPER_MOUNT_TOOLS):
            assert tool in calls, f"{tool} 未被 AST 提取（tools.py 结构漂移）"
            for c in calls[tool]:
                pool = (READONLY_ENDPOINTS if c.method == "GET"
                        else RESEARCH_POST_ENDPOINTS)
                path = c.path.replace("{param}", "{task_id}")
                assert path in pool, f"{tool} 端点 {c.path} 不在白名单"


class TestMountParamSurface:
    """mount 参数面与 REST POST /tasks 完全对拍（spec Scenario
    「挂载不越语义面」——参数面不含策略语义字段，无法构造该请求）。"""

    # 策略语义参数黑名单（spec「语义不可覆写」：方向/周期/信号基因/
    # 止损模式——AI 经纸盘挂载不可改写）
    SEMANTIC_KEYS = frozenset({
        "direction", "timeframe", "dna_json", "signal_genes",
        "risk_genes", "sl_mode", "stop_loss", "take_profit",
        "score_template", "prediction_dna_json"})

    def test_mount_params_equal_rest_create_model(self) -> None:
        """mount 签名参数集 == REST PaperTaskCreate 字段集（完全对拍：
        同一参数面，无一字段多无一字段少）。"""
        from api.routes.paper_trading import PaperTaskCreate

        mcp, _ = _register("research")
        mount_params = set(
            inspect.signature(mcp.registered["mount_paper_task"]).parameters)
        assert mount_params == set(PaperTaskCreate.model_fields), (
            f"mount 参数面与 REST 创建模型漂移："
            f"多 {sorted(mount_params - set(PaperTaskCreate.model_fields))} / "
            f"少 {sorted(set(PaperTaskCreate.model_fields) - mount_params)}")

    def test_mount_params_no_semantic_keys(self) -> None:
        """参数面零策略语义键（黑名单逐键断言）。"""
        mcp, _ = _register("research")
        mount_params = set(
            inspect.signature(mcp.registered["mount_paper_task"]).parameters)
        assert not (mount_params & self.SEMANTIC_KEYS)

    def test_mount_body_assembly_optional_only_when_given(self) -> None:
        """行为对拍：可选参数 None 不进 body（缺省面交 REST 层落 DNA），
        显式传入才落键——body 组装与既有工具形制一致。"""
        mcp, api = _register("research")
        mcp.registered["mount_paper_task"]("st-9")
        method, url, body = api.calls[-1]
        assert (method, url) == ("POST", "/api/paper-trading/tasks")
        assert body == {"strategy_id": "st-9"}  # 缺省面零键透传

        mcp.registered["mount_paper_task"](
            "st-9", initial_cash=50_000.0, leverage=5, window_bars=60)
        _, _, body = api.calls[-1]
        assert body == {"strategy_id": "st-9", "initial_cash": 50_000.0,
                        "leverage": 5, "window_bars": 60}


class TestMoneyTouchBoundary:
    """触金封禁集合断言（spec Scenario「触金封禁不动」）。"""

    def test_new_endpoints_disjoint_from_forbidden_patterns(self) -> None:
        """新端点族与 FORBIDDEN_PATTERNS 前缀零相交（含 stop 后缀路径
        ——/api/paper-trading/tasks/x/stop 不落入 /api/trading/stop 前
        线，前缀匹配防误伤双向核验）。"""
        from mcpserver.api_client import FORBIDDEN_PATTERNS

        paths = [
            "/api/paper-trading/tasks",
            "/api/paper-trading/tasks/abc123/stop",
            "/api/paper-trading/tasks/abc123/report",
            "/api/paper-trading/runner-status",
        ]
        for path in paths:
            for pat in FORBIDDEN_PATTERNS:
                assert not path.startswith(pat), \
                    f"{path} 误中触金前缀 {pat}"

    def test_paper_tools_not_money_touching_by_name(self) -> None:
        """六件工具名零触金嫌疑词（沿 test_server 名字扫描词表）。"""
        forbidden_substrings = ("order", "trade_open", "close_position",
                                "risk", "live", "liquidate", "leverage")
        for n in (PAPER_READONLY_TOOLS | PAPER_MOUNT_TOOLS):
            for bad in forbidden_substrings:
                assert bad not in n, f"触金嫌疑工具名: {n}"


class TestEndpointAllowance:
    """EndpointNotAllowed 路径核验（白名单两档分派+拒绝面）。"""

    def test_research_profile_allows_paper_endpoints(self) -> None:
        from mcpserver.api_client import QuantApiClient

        api = QuantApiClient(profile="research")
        for path in ("/api/paper-trading/tasks",
                     "/api/paper-trading/tasks/abc123",
                     "/api/paper-trading/tasks/abc123/fills",
                     "/api/paper-trading/tasks/abc123/equity",
                     "/api/paper-trading/tasks/abc123/funding",
                     "/api/paper-trading/tasks/abc123/report",
                     "/api/paper-trading/tasks/abc123/report/final",
                     "/api/paper-trading/runner-status"):
            api.check_allowed("GET", path)  # 不抛即通过
        api.check_allowed("POST", "/api/paper-trading/tasks")
        api.check_allowed("POST", "/api/paper-trading/tasks/abc123/stop")

    def test_readonly_profile_denies_mount(self) -> None:
        """readonly 档 POST 面（挂载）不可达。"""
        from mcpserver.api_client import EndpointNotAllowed, QuantApiClient

        api = QuantApiClient(profile="readonly")
        with pytest.raises(EndpointNotAllowed):
            api.check_allowed("POST", "/api/paper-trading/tasks")

    def test_delete_and_unregistered_paths_denied(self) -> None:
        """DELETE（任何档不支持）与未登记 GET 面拒绝。"""
        from mcpserver.api_client import EndpointNotAllowed, QuantApiClient

        api = QuantApiClient(profile="research")
        with pytest.raises(EndpointNotAllowed):
            api.check_allowed("DELETE", "/api/paper-trading/tasks/abc123")
        with pytest.raises(EndpointNotAllowed):
            api.check_allowed("GET", "/api/paper-trading/tasks/abc123/clone")
        with pytest.raises(EndpointNotAllowed):
            api.check_allowed("GET", "/api/paper-trading/tasks/abc123/bogus")


class TestCapabilityDisclosure:
    """capability 自述含新工具与挂载语义说明。"""

    def test_capability_doc_lists_paper_tools(self) -> None:
        from mcpserver.capability import build_capability_doc

        doc = build_capability_doc(sorted(
            PAPER_READONLY_TOOLS | PAPER_MOUNT_TOOLS))
        for name in (PAPER_READONLY_TOOLS | PAPER_MOUNT_TOOLS):
            assert f"- {name}" in doc

    def test_capability_doc_carries_mount_semantics(self) -> None:
        """协议要点静态段补挂载语义（参数面边界/撤盘语义/触金边界）。"""
        from mcpserver.capability import CAPABILITY_DOC

        for token in ("纸盘挂载", "mount_paper_task", "unmount_paper_task",
                      "不含策略语义参数", "撤盘=停止语义", "不改变触金封禁边界"):
            assert token in CAPABILITY_DOC, f"能力自述缺挂载语义要素: {token}"


class TestToolBehavior:
    """工具行为对拍（RecordingFakeApi——路径分派与结构化拒绝）。"""

    def test_get_paper_report_rolling_vs_final(self) -> None:
        mcp, api = _register("readonly")
        mcp.registered["get_paper_report"]("abc123")
        assert api.calls[-1][:2] == ("GET", "/api/paper-trading/tasks/abc123/report")
        mcp.registered["get_paper_report"]("abc123", final=True)
        assert api.calls[-1][:2] == (
            "GET", "/api/paper-trading/tasks/abc123/report/final")

    def test_get_paper_history_three_types(self) -> None:
        mcp, api = _register("readonly")
        for type_ in ("fills", "equity", "funding"):
            mcp.registered["get_paper_history"]("abc123", type=type_)
            method, url, params = api.calls[-1]
            assert (method, url) == (
                "GET", f"/api/paper-trading/tasks/abc123/{type_}")
            assert params == {"limit": 50}  # 100 缺省 clamp 至 _PAGE_MAX

    def test_get_paper_history_illegal_type_structured_reject(self) -> None:
        """非法 type 工具层结构化拒绝（error payload 形制，不发请求）。"""
        mcp, api = _register("readonly")
        n_calls = len(api.calls)
        result = mcp.registered["get_paper_history"]("abc123", type="report")
        assert "error" in result
        assert result["error"]["code"] == "MCP_PARAM_INVALID"
        assert len(api.calls) == n_calls  # 拒绝路径零外发请求

    def test_unmount_posts_stop_endpoint(self) -> None:
        """撤盘经 stop 端点（停止语义——非 DELETE，证据保留）。"""
        mcp, api = _register("research")
        mcp.registered["unmount_paper_task"]("abc123")
        method, url, body = api.calls[-1]
        assert (method, url) == (
            "POST", "/api/paper-trading/tasks/abc123/stop")

    def test_list_paper_tasks_params(self) -> None:
        mcp, api = _register("readonly")
        mcp.registered["list_paper_tasks"](status="running", limit=100)
        method, url, params = api.calls[-1]
        assert (method, url) == ("GET", "/api/paper-trading/tasks")
        assert params == {"limit": 50, "offset": 0, "status": "running"}


class TestMountFullParamAssembly:
    """mount 全参 body 组装分支（闸门盲区消解——fee/slippage/symbol/
    stop_loss_pct 可选键补齐）。"""

    def test_mount_all_optional_params_in_body(self) -> None:
        """五可选参数显式传入全落 body（与 REST 创建参数面完整对拍）。"""
        mcp, api = _register("research")
        mcp.registered["mount_paper_task"](
            "st-9", initial_cash=50_000.0, leverage=5, fee=0.002,
            slippage=0.001, symbol="ETHUSDT", window_bars=60,
            stop_loss_pct=0.2)
        _, _, body = api.calls[-1]
        assert body == {
            "strategy_id": "st-9", "initial_cash": 50_000.0,
            "leverage": 5, "fee": 0.002, "slippage": 0.001,
            "symbol": "ETHUSDT", "window_bars": 60,
            "stop_loss_pct": 0.2}


class TestRequestAuthHeader:
    """_request 在 API key 设置态的 Authorization 携带（闸门盲区
    消解——A2 客户端侧接线）。"""

    def test_api_key_sent_as_bearer_header(self, monkeypatch) -> None:
        """MYQUANT_API_KEY 设置态：请求头带 Bearer（未设则不带）。"""
        import mcpserver.api_client as ac
        from mcpserver.api_client import QuantApiClient

        captured: dict = {}

        class _Ctx:
            def __init__(self, resp):
                self._resp = resp

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None):
                return self._resp

        import httpx

        real_client = ac.httpx.Client

        def capturing_client(**kw):
            captured.update(kw)
            return _Ctx(httpx.Response(200, json={}))

        monkeypatch.setattr(ac.httpx, "Client", capturing_client)
        monkeypatch.setenv("MYQUANT_API_KEY", "secret-9")
        api = QuantApiClient(base_url="http://x", profile="research")
        api.get("/api/paper-trading/tasks")
        assert captured["headers"] == {"Authorization": "Bearer secret-9"}
        monkeypatch.delenv("MYQUANT_API_KEY")
        monkeypatch.setattr(ac.httpx, "Client", real_client)
