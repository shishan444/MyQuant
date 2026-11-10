"""run_backtest 任务级参数面 MCP 测试（sandbox-and-report-supplement
A1/A4）。

MCP 是 AI 发起沙盒回测的主入口（ai-research-tools「沙盒回测」条款的
WHEN 主语）——三可选参（leverage/margin_cap/halt_drawdown_pct）逐键
条件携带（传了才写进 body）＋docstring 值域/披露段在场锚（MCP 披露
纪律：docstring 未披露=AI 视角不存在）。透传形制同
test_ab_compare_mcp（mock api.post 捕获 body）。
"""
import pytest

pytestmark = [pytest.mark.integration]


def _tools_by_name():
    from mcpserver.server import build_server
    from tests.mcpserver.test_contract_parity import run_async

    mcp, _ = build_server(profile="research")
    tools = run_async(mcp.list_tools())
    return {t.name: t for t in tools}


_BASE_ARGS = {
    "strategy_id": "st_1", "symbol": "BTCUSDT", "timeframe": "4h",
    "start": "2024-01-01", "end": "2024-02-01"}


class TestRunBacktestTaskCapMcp:
    def test_docstring_discloses_param_domain(self) -> None:
        """披露在场锚：三参数值域＋③层缩量语义＋熔断三态均在
        run_backtest docstring（AI 视角可发现）。"""
        desc = _tools_by_name()["run_backtest"].description
        for token in ("[1,3]", "(0,0.60]", "halt_drawdown_pct",
                      "有效杠杆", "缩量至顶", "capped_by_task_cap",
                      "halt_info"):
            assert token in desc, f"docstring 缺披露 token: {token}"

    def test_task_cap_params_passthrough(self, monkeypatch):
        """三参透传：携带调用 → body 三键在场（值原样——值域校验在
        API 路由层单源）。"""
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        mcp, api = build_server(profile="research", base_url="http://x")
        captured: dict = {}

        def _post(path, json_body=None):
            captured.update(path=path, body=json_body)
            return {"strategy_id": "st_1", "metrics": {}, "sandbox": True}

        api.post = _post
        run_async(mcp.call_tool("run_backtest", {
            **_BASE_ARGS, "leverage": 3, "margin_cap": 0.45,
            "halt_drawdown_pct": 0.1}))
        body = captured["body"]
        assert captured["path"] == "/api/research/backtest-sandbox"
        assert body["leverage"] == 3
        assert body["margin_cap"] == 0.45
        assert body["halt_drawdown_pct"] == 0.1

    def test_default_call_omits_three_keys(self, monkeypatch):
        """缺省调用（false 侧）：body 无三键——③层与熔断均不启用
        （存量请求形制逐位不变）。"""
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        mcp, api = build_server(profile="research", base_url="http://x")
        captured: dict = {}

        def _post(path, json_body=None):
            captured.update(path=path, body=json_body)
            return {"strategy_id": "st_1", "metrics": {}, "sandbox": True}

        api.post = _post
        run_async(mcp.call_tool("run_backtest", dict(_BASE_ARGS)))
        body = captured["body"]
        for key in ("leverage", "margin_cap", "halt_drawdown_pct"):
            assert key not in body, f"缺省调用不应携带 {key}"


class TestMountPaperTaskHaltMcp:
    """mount_paper_task 熔断参数透传（阶段四闸门消解：带值侧
    :1299 条件携带分支未覆盖——缺省侧由既有 mount 用例走通）。"""

    def test_halt_passthrough_with_value(self, monkeypatch):
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        mcp, api = build_server(profile="research", base_url="http://x")
        captured: dict = {}

        def _post(path, json_body=None):
            captured.update(path=path, body=json_body)
            return {"task_id": "pt_1", "status": "pending"}

        api.post = _post
        run_async(mcp.call_tool("mount_paper_task", {
            "strategy_id": "st_1", "halt_drawdown_pct": 0.15}))
        assert captured["path"] == "/api/paper-trading/tasks"
        assert captured["body"]["halt_drawdown_pct"] == 0.15, \
            "带值挂载须携带熔断阈值（纸盘路 D3② 参数面）"
