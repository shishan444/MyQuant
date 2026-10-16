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


class TestPollTimeoutClamp:
    """CARD-13 修复资产：poll_evolution_task 的 total_timeout 钳制
    [1, 3600]s（防误传 0/负值立即超时、超长值挂死会话）。"""

    def test_timeout_clamped_into_domain(self, monkeypatch) -> None:
        import json as _json

        mcp, api = build_server(profile="research", base_url="http://x")
        captured: dict = {}

        def _fake_poll(path, **kw):
            captured.update(total_timeout=kw.get("total_timeout"))
            return {"status": "completed"}

        api.poll_until_terminal = _fake_poll

        for raw, expect in [(0, 1.0), (-5, 1.0), (99999, 3600.0), (300, 300.0)]:
            run_async(mcp.call_tool("poll_evolution_task",
                                    {"task_id": "t", "total_timeout": raw}))
            assert captured["total_timeout"] == expect, \
                f"total_timeout={raw} 应钳制为 {expect}"


class TestPathSegmentEncoding:
    """CARD-11 修复资产：URL 路径段 quote 编码（防段逃逸/查询注入）。

    id 含 / .. ? 空格等字符时编码为单段（%2F/%3F/%20）——不改变
    端点语义与路径段数（白名单 {param} 模板匹配兼容）；合法 hex id
    编码零变化。原 f-string 直拼下 "a/b" 会多一段路径改变端点语义。
    """

    def test_get_single_segment_encoded(self, monkeypatch) -> None:
        mcp, api = build_server(profile="readonly", base_url="http://x")
        captured: dict = {}
        api.get = lambda path, params=None: (
            captured.update(path=path) or {"items": []})

        run_async(mcp.call_tool("get_strategy",
                                {"strategy_id": "a/b c"}))
        assert captured["path"] == "/api/strategies/a%2Fb%20c", \
            "注入形 id 编码为单段，不逃逸不多段"

    def test_post_two_segment_transition_encoded(self, monkeypatch) -> None:
        mcp, api = build_server(profile="research", base_url="http://x")
        captured: dict = {}
        api.post = lambda path, json_body=None: (
            captured.update(path=path) or {})

        run_async(mcp.call_tool("transition_hypothesis", {
            "hypothesis_id": "hyp/../evil", "target_status": "refuted",
            "evidence_ref": "rec_1"}))
        assert captured["path"] == (
            "/api/research/hypotheses/hyp%2F..%2Fevil/transition")

    def test_poll_path_encoded(self, monkeypatch) -> None:
        """poll_until_terminal 路径同编码（内部走 get——白名单语义）。"""
        mcp, api = build_server(profile="research", base_url="http://x")
        captured: dict = {}
        api.poll_until_terminal = lambda path, **kw: (
            captured.update(path=path) or {"status": "completed"})

        run_async(mcp.call_tool("poll_evolution_task",
                                {"task_id": "x?y=1", "total_timeout": 0.01}))
        assert captured["path"] == "/api/evolution/tasks/x%3Fy%3D1", \
            "查询注入形 id 不产生查询串"

    def test_legal_hex_id_passthrough(self, monkeypatch) -> None:
        """合法 hex/kebab id 编码零变化（常规路径零漂移）。"""
        mcp, api = build_server(profile="readonly", base_url="http://x")
        captured: dict = {}
        api.get = lambda path, params=None: (
            captured.update(path=path) or {})

        run_async(mcp.call_tool("get_analysis_task",
                                {"task_id": "ant_abc123-def456"}))
        assert captured["path"] == "/api/research/analysis/tasks/ant_abc123-def456"


class TestToolBodyInvocation:
    """闸门终验消解：28 工具体逐个经 call_tool 实调（对拍测试断言注册面，
    此处钉「工具→端点」映射的行为面——工具体参数组装 + method/path）。

    L3（stdio/http 真进程）覆盖真实链路；本类补进程内工具体触达与
    映射锚：method + 归一化路径（{param} 段）逐工具断言。
    """

    # (工具名, 最小参数, 期望 method, 期望归一路径)
    CASES = [
        ("list_data_sources", {}, "GET", "/api/data/available-sources"),
        ("list_strategies", {"limit": 5, "offset": 0}, "GET", "/api/strategies"),
        ("get_strategy", {"strategy_id": "st_1"}, "GET", "/api/strategies/{param}"),
        ("list_evolution_tasks", {"page": 1, "page_size": 10}, "GET", "/api/evolution/tasks"),
        ("list_indicators", {}, "GET", "/api/research/indicators"),
        ("list_signal_columns", {}, "GET", "/api/research/signals"),
        ("get_market_snapshot", {"symbol": "BTCUSDT", "timeframes": "1d,4h",
                             "as_of": "2024-06-01T00:00:00Z"},
         "GET", "/api/research/market-snapshot"),
        ("list_hypotheses", {"page": 1, "status": "active",
                          "cursor": "2026-01-01T00:00:00|h1"},
         "GET", "/api/research/hypotheses"),
        ("get_hypothesis", {"hypothesis_id": "hyp_1"}, "GET", "/api/research/hypotheses/{param}"),
        ("get_research_log", {"page": 1, "hypothesis_id": "h1",
                           "entry_type": "register",
                           "cursor": "2026-01-01T00:00:00|l1"},
         "GET", "/api/research/log"),
        ("get_analysis_task", {"task_id": "ant_1"}, "GET", "/api/research/analysis/tasks/{param}"),
        ("get_validation_report", {"strategy_id": "st_1"}, "GET", "/api/research/validation/{param}/report"),
        ("list_feature_requests", {"page": 1, "status": "open"},
         "GET", "/api/research/feature-requests"),
        ("run_analysis", {"template": "condition_stats", "params": {},
                          "symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-01-01", "end": "2024-02-01",
                          "hypothesis_id": "h1"},
         "POST", "/api/research/analysis"),
        ("inspect_cases", {"symbol": "BTCUSDT", "timeframe": "4h",
                           "selector": {"condition": {"subject": "close", "action": "gt", "target": 1}},
                           "start": "2024-01-01", "end": "2024-02-01",
                           "indicator": "RSI"},
         "POST", "/api/research/inspect-cases"),
        ("query_indicator", {"symbol": "BTCUSDT", "timeframe": "4h",
                              "indicator": "RSI", "params": {"period": 21},
                              "reductions": ["latest"], "start": "2024-01-01",
                              "end": "2024-02-01"},
         "POST", "/api/research/query-indicator"),
        ("register_hypothesis", {"when_conditions": [{"subject": "close", "action": "gt", "target": 0}],
                                 "then_conditions": [{"subject": "close", "action": "gt", "target": -1}],
                                 "statement": "s",
                                 "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                                                  "start": "2024-01-01", "end": "2024-02-01"}],
                                 "provenance": {"task_id": "ant_1",
                                                "candidate_fingerprint": "fp"}},
         "POST", "/api/research/hypotheses"),
        ("transition_hypothesis", {"hypothesis_id": "hyp_1", "target_status": "refuted",
                                   "evidence_ref": "rec_1"},
         "POST", "/api/research/hypotheses/{param}/transition"),
        ("run_isolated_validation", {"hypothesis_id": "hyp_1", "strategy_id": "st_1",
                                     "symbol": "BTCUSDT", "timeframe": "4h",
                                     "start": "2024-01-01", "end": "2024-02-01"},
         "POST", "/api/research/validate-backtest"),
        ("run_random_window_test", {"strategy_id": "st_1", "symbol": "BTCUSDT",
                                    "timeframe": "4h", "data_start": "2024-01-01",
                                    "data_end": "2024-03-01",
                                    "hypothesis_id": "h1"},
         "POST", "/api/research/random-window-test"),
        ("run_staged_validation", {"strategy_id": "st_1", "symbol": "BTCUSDT",
                                   "timeframe": "4h", "start": "2024-01-01",
                                   "end": "2024-02-01",
                                   "segments": [{"label": "a", "start": "2024-01-01",
                                                 "end": "2024-02-01"}],
                                   "hypothesis_id": "h1"},
         "POST", "/api/research/staged-validation"),
        ("run_backtest", {"strategy_id": "st_1", "symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-01-01", "end": "2024-02-01",
                          "hypothesis_id": "h1"},
         "POST", "/api/research/backtest-sandbox"),
        ("register_formula_plugin", {"name": "n", "expression": "zscore(close, 20)"},
         "POST", "/api/research/plugins/formula"),
        ("register_model_plugin", {"name": "m", "features": ["close"],
                                   "data_start": "2024-01-01", "data_end": "2024-02-01"},
         "POST", "/api/research/plugins/model"),
        ("translate_hypothesis_to_strategy", {"hypothesis_id": "hyp_1",
                                              "strategy_symbol": "BTCUSDT",
                                              "strategy_timeframe": "4h"},
         "POST", "/api/research/translate/{param}"),
        ("submit_feature_request", {"title": "t", "payload": {}},
         "POST", "/api/research/feature-requests"),
        ("trigger_evolution", {"config": {}}, "POST", "/api/evolution/tasks"),
        ("poll_evolution_task", {"task_id": "evo_1"},
         "POLL", "/api/evolution/tasks/{param}"),
    ]

    # 全工具均安全的可选键（缺省轮剔除走 false 侧；hypothesis_id/
    # start/end/indicator/params 在部分工具必填，由专项用例覆盖）
    SAFE_OPTIONAL_KEYS = frozenset({
        "timeframes", "as_of", "status", "cursor", "reductions",
        "provenance", "segments", "strategy_symbol", "strategy_timeframe",
    })

    def test_every_tool_invokes_expected_endpoint(self) -> None:
        import re

        mcp, api = build_server(profile="research", base_url="http://x")
        calls: list = []
        api.get = lambda path, params=None: (calls.append(("GET", path)) or {})
        api.post = lambda path, json_body=None: (calls.append(("POST", path)) or {})
        api.poll_until_terminal = lambda path, **kw: (
            calls.append(("POLL", path)) or {"status": "completed"})

        for tool, args, method, expected in self.CASES:
            # 双轮：满参（true 侧）+ 缺省（false 侧）——可选携带分支双向走
            for call_args in (args,
                              {k: v for k, v in args.items()
                               if k not in self.SAFE_OPTIONAL_KEYS}):
                calls.clear()
                out = run_async(mcp.call_tool(tool, call_args))
                assert "error" not in out, f"{tool} 意外失败: {out}"
                assert calls and calls[0][0] == method, \
                    f"{tool} method 不符: {calls}"
                got = calls[0][1]
                exp_re = re.escape(expected).replace(
                    re.escape("{param}"), r"[^/]+")
                assert re.fullmatch(exp_re, got), \
                    f"{tool} 端点 {got} 不符期望 {expected}"

    def test_optional_only_args_omitted_paths(self) -> None:
        """部分工具必填的可选键（hypothesis_id/start/end/indicator/
        params）在其可选场合的缺省调用（false 侧专项）。"""
        mcp, api = build_server(profile="research", base_url="http://x")
        posts: list = []
        api.post = lambda path, json_body=None: (posts.append(path) or {})
        api.get = lambda path, params=None: {}

        run_async(mcp.call_tool("run_backtest", {
            "strategy_id": "st_1", "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01", "end": "2024-02-01"}))
        run_async(mcp.call_tool("run_analysis", {
            "template": "condition_stats", "params": {},
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01", "end": "2024-02-01"}))
        run_async(mcp.call_tool("run_random_window_test", {
            "strategy_id": "st_1", "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-01-01", "data_end": "2024-03-01"}))
        run_async(mcp.call_tool("run_staged_validation", {
            "strategy_id": "st_1", "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01", "end": "2024-02-01"}))
        run_async(mcp.call_tool("inspect_cases", {
            "symbol": "BTCUSDT", "timeframe": "4h",
            "selector": {"condition": {"subject": "close", "action": "gt",
                                       "target": 1}}}))
        run_async(mcp.call_tool("query_indicator", {
            "symbol": "BTCUSDT", "timeframe": "4h", "indicator": "RSI"}))
        run_async(mcp.call_tool("get_research_log", {"page": 1}))
        assert len(posts) == 6, "六个 POST 可选场合缺省调用均到达"

    def test_query_indicator_body_passthrough_card06(self) -> None:
        """CARD-06：query_indicator body 组装——单组字段无条件透传。

        indicator+groups 同传两键均进 body（互斥归 API 单一事实源判
        422——原 else 分支吞 indicator 使互斥对同传永不触发、静默走
        批量路径）；start="" 空串透传交 API 判（原 truthy 吞掉）；
        None 缺省不进 body（既有语义不变锚）。"""
        mcp, api = build_server(profile="research", base_url="http://x")
        bodies: list = []

        def _capture(path, json_body=None):
            bodies.append(json_body)
            return {}

        api.post = _capture
        run_async(mcp.call_tool("query_indicator", {
            "symbol": "BTCUSDT", "timeframe": "4h", "indicator": "RSI",
            "groups": [{"indicator": "EMA"}]}))
        body = bodies[0]
        assert body["indicator"] == "RSI", "同传时 indicator 不被吞"
        assert body["groups"] == [{"indicator": "EMA"}], "groups 照常透传"

        run_async(mcp.call_tool("query_indicator", {
            "symbol": "BTCUSDT", "timeframe": "4h", "indicator": "RSI",
            "start": ""}))
        assert bodies[1]["start"] == "", "空串 start 透传（API 侧判）"

        run_async(mcp.call_tool("query_indicator", {
            "symbol": "BTCUSDT", "timeframe": "4h", "indicator": "RSI"}))
        bare = bodies[2]
        for key in ("params", "reductions", "start", "end", "groups"):
            assert key not in bare, f"None 缺省 {key} 不进 body"


class TestServerEntryGaps:
    """闸门终验消解：server.py 装配校验/自检/main dispatch 的进程内触达。

    main() 的真链路由 L3 承担（test_stdio_l3 真子进程 -m mcpserver.server
    + env stdio；test_http_l3 真子进程 streamable-http——含
    _check_api_reachable 真实执行）；此类补进程内分支语义与覆盖率。
    """

    def test_unknown_profile_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知档位"):
            build_server(profile="bogus")

    def test_check_api_reachable_ok_logs_info(self, monkeypatch, caplog) -> None:
        import httpx

        import mcpserver.server as srv

        class _Resp:
            status_code = 200
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Resp())
        with caplog.at_level("INFO"):
            srv._check_api_reachable()
        assert any("自检通过" in r.message for r in caplog.records)

    def test_check_api_reachable_down_logs_warning_not_raise(
            self, monkeypatch, caplog) -> None:
        import httpx

        import mcpserver.server as srv

        def _boom(*a, **kw):
            raise httpx.ConnectError("refused")
        monkeypatch.setattr(httpx, "get", _boom)
        with caplog.at_level("WARNING"):
            srv._check_api_reachable()  # 不可达：记 warning 后继续（不抛）
        assert any("不可达" in r.message for r in caplog.records)

    def test_check_api_reachable_non_200_logs_warning(
            self, monkeypatch, caplog) -> None:
        import httpx

        import mcpserver.server as srv

        class _Resp:
            status_code = 503
        monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Resp())
        with caplog.at_level("WARNING"):
            srv._check_api_reachable()
        assert any("自检异常" in r.message for r in caplog.records)

    def test_unsupported_method_rejected(self) -> None:
        """check_allowed 只收 GET/POST（闸门终验消解：else 分支）。"""
        api = QuantApiClient(base_url="http://127.0.0.1:1", profile="research")
        with pytest.raises(EndpointNotAllowed, match="不受支持"):
            api.check_allowed("DELETE", "/api/research/hypotheses")

    def test_poll_passes_get_params(self, monkeypatch) -> None:
        """poll_until_terminal 的 get_params 透传（闸门终验消解）。"""
        captured: dict = {}

        def _fake_get(path, params=None):
            captured["params"] = params
            return {"status": "completed"}
        api = QuantApiClient(base_url="http://x", profile="research")
        api.get = _fake_get
        out = api.poll_until_terminal(
            "/api/research/analysis/tasks/ant_1", get_params={"x": 1},
            initial_delay=0.01, total_timeout=1.0)
        assert captured["params"] == {"x": 1}
        assert out["status"] == "completed"

    def test_poll_falsy_get_falls_back_empty(self, monkeypatch) -> None:
        """poll 的 `self.get(...) or {}` 兜底：GET 返回 falsy 不炸（终验消解）。"""
        api = QuantApiClient(base_url="http://x", profile="research")
        api.get = lambda path, params=None: None
        out = api.poll_until_terminal(
            "/api/research/analysis/tasks/ant_1",
            initial_delay=0.01, total_timeout=0.05)
        assert out["_poll_timeout"] is True  # None 状态不炸，超时正常标注

    def test_post_204_returns_none(self, monkeypatch) -> None:
        """post 的 204/空 body → None 分支（终验消解）。"""
        api = QuantApiClient(base_url="http://x", profile="research")

        class _Resp:
            status_code = 204
            content = b""
        monkeypatch.setattr(
            api, "_request", lambda *a, **kw: _Resp())
        assert api.post("/api/evolution/tasks", {"x": 1}) is None

    def test_request_explicit_timeout_branch(self, monkeypatch) -> None:
        """_request 显式 timeout 直达（终验消解：timeout 非 None 分支——
        当前 get/post 薄壳均不传，预留参数分支白盒直调）。"""
        import httpx

        class _Resp:
            status_code = 200
            is_error = False

            def json(self):
                return {"ok": 1}

        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None):
                return _Resp()

        captured: dict = {}
        orig_client = httpx.Client

        def _spy_client(**kw):
            captured.update(kw)
            return _Ctx()
        monkeypatch.setattr(httpx, "Client", _spy_client)
        try:
            api = QuantApiClient(base_url="http://x", profile="research")
            api._request("GET", "/api/research/hypotheses", timeout=5.0)
        finally:
            monkeypatch.setattr(httpx, "Client", orig_client)
        assert captured["timeout"] == 5.0, "显式 timeout 不被缺省取档改写"

    @pytest.mark.parametrize("transport", ["stdio", "streamable-http"])
    def test_main_dispatches_transport(self, monkeypatch, transport) -> None:
        """main() 按 MYQUANT_MCP_TRANSPORT 分派 mcp.run（进程内 stub——
        真链路 L3 已证；此处钉 dispatch 语义与端口显式传）。"""
        import mcpserver.server as srv

        runs: list = []
        monkeypatch.setenv("MYQUANT_MCP_TRANSPORT", transport)
        monkeypatch.setenv("MYQUANT_MCP_PORT", "8899")

        class _FakeMcp:
            def run(self, *a, **kw):
                runs.append(dict(kw, _args=a))
        monkeypatch.setattr(srv, "build_server",
                            lambda profile=None, base_url=None: (_FakeMcp(), None))
        monkeypatch.setattr(srv, "_check_api_reachable", lambda: None)

        srv.main()
        assert runs and runs[0]["transport"] == transport
        if transport == "streamable-http":
            assert runs[0]["host"] == "127.0.0.1" and runs[0]["port"] == 8899, \
                "端口必须显式传（SDK 默认 8000 与 API 冲突）"
        else:
            assert runs[0]["_args"] == (), "stdio 形态无额外位置参数"

    def test_main_unknown_transport_rejected(self, monkeypatch) -> None:
        import mcpserver.server as srv

        monkeypatch.setenv("MYQUANT_MCP_TRANSPORT", "grpc")
        monkeypatch.setattr(srv, "build_server",
                            lambda profile=None, base_url=None: (object(), None))
        with pytest.raises(ValueError, match="未知 transport"):
            srv.main()


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
        assert len(got_ind) == len(direct_ind) == 48  # Q3 摘牌后

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
        # 闸门②补强：超时到期显式标注（客户端可区分「完成」与「超时截断」）
        assert out.get("_poll_timeout") is True


class _FakeCtx:
    def __init__(self, resp):
        self._resp = resp
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def get(self, url, params=None):
        return self._resp
