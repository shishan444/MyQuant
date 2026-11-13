"""MCP 工具层 body 组装行为锚（trigger_policy 透传面）。

contract_parity 承担 AST 结构对拍；此处补行为级：register_hypothesis
的 body 组装对可选字段的条件透传（带 policy 传入 / 不带省略）。
"""

import pytest

pytestmark = [pytest.mark.integration]

from conftest import _FakeApi, _FakeMcp  # 同目录（pytest prepend）


_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-02-01T00:00:00Z"}]


def _register_and_call(**kw):
    from mcpserver import tools as tools_mod

    mcp, api = _FakeMcp(), _FakeApi()
    tools_mod.register_research_tools(mcp, api)
    fn = mcp.registered["register_hypothesis"]
    out = fn(
        when_conditions=[{"subject": "close", "action": "gt", "target": 0}],
        then_conditions=[{"subject": "close", "action": "gt", "target": 1}],
        statement="s", data_ranges=_RANGES, **kw)
    return out, api.posts[0]


class TestUnionBalanceReadonlyPassthrough:
    """阶段四盲区消解（fix-mtf-review-consistency ②）：union_balance
    只读工具 GET 透传行为锚——三必填参数进查询串、路由路径正确
    （:142 未覆盖行——tests/mcpserver 透传域）。"""

    def test_get_passthrough_three_required_params(self):
        from conftest import RecordingFakeApi
        from mcpserver import tools as tools_mod

        mcp, api = _FakeMcp(), RecordingFakeApi()
        tools_mod.register_readonly_tools(mcp, api)
        fn = mcp.registered["union_balance"]
        fn(symbol="BTCUSDT", timeframe="4h", hypothesis_id="hyp_x")
        method, url, params = api.calls[0]
        assert method == "GET"
        assert url == "/api/research/union-balance"
        assert params == {"symbol": "BTCUSDT", "timeframe": "4h",
                          "hypothesis_id": "hyp_x"}


class TestRegisterHypothesisTriggerPolicyPassthrough:
    def test_with_policy_in_body(self):
        out, (url, body) = _register_and_call(
            trigger_policy={"mode": "first_true"})
        assert out == {"hypothesis_id": "h1"}
        assert url == "/api/research/hypotheses"
        assert body["trigger_policy"] == {"mode": "first_true"}

    def test_without_policy_omitted(self):
        _, (url, body) = _register_and_call()
        assert "trigger_policy" not in body


class TestRegisterHypothesisParamsScalingPassthrough:
    """0.4.2 新参数透传行为锚：strategy_params 与分批组键三参数
    ——带值进请求体顶层、None 省略（与 trigger_policy 同款；
    schema 对拍在 contract_parity，此处锚 body 组装行为）。"""

    GROUP = [{"subject": "close", "action": "gt", "target": 0}]

    @pytest.mark.parametrize("param,value", [
        ("strategy_params", {"stop_loss": 0.05, "sl_mode": "pct"}),
        ("exit_partial_conditions", GROUP),
        ("add_trigger_conditions", GROUP),
        ("add_suppress_conditions", GROUP),
    ])
    def test_with_value_in_body(self, param, value):
        _, (url, body) = _register_and_call(**{param: value})
        assert body[param] == value, f"{param} 带值须透传到请求体顶层"

    @pytest.mark.parametrize("param", [
        "strategy_params", "exit_partial_conditions",
        "add_trigger_conditions", "add_suppress_conditions",
    ])
    def test_none_omitted(self, param):
        _, (url, body) = _register_and_call()
        assert param not in body, f"{param} 未传时须省略（不进 body 顶层）"


class TestValidationToolsPostThrough:
    """add-validation-detail B4-12 MCP 壳面：三验证工具 api.post 透传
    URL 与请求体形制（真实落盘链路的中枢段由
    tests/api/test_validation_detail_endpoint.py 的 HTTP 入口用例承载
    ——MCP 工具即该端点的透传壳）。"""

    def _call(self, name, **kw):
        from mcpserver import tools as tools_mod

        mcp, api = _FakeMcp(), _FakeApi()
        tools_mod.register_research_tools(mcp, api)
        mcp.registered[name](**kw)
        return api.posts[0]

    def test_isolated_posts_validate_backtest(self):
        url, body = self._call(
            "run_isolated_validation",
            hypothesis_id="hyp1", strategy_id="st1", symbol="BTCUSDT",
            timeframe="4h", start="2023-03-01T00:00:00Z",
            end="2023-04-01T00:00:00Z")
        assert url == "/api/research/validate-backtest"
        assert body == {
            "hypothesis_id": "hyp1", "strategy_id": "st1",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2023-03-01T00:00:00Z",
                              "end": "2023-04-01T00:00:00Z"}}

    def test_random_window_posts_endpoint(self):
        url, body = self._call(
            "run_random_window_test",
            strategy_id="st1", symbol="BTCUSDT", timeframe="4h",
            data_start="2023-01-01T00:00:00Z",
            data_end="2023-06-01T00:00:00Z",
            window_bars=100, n_windows=2, seed=11, hypothesis_id="hyp1")
        assert url == "/api/research/random-window-test"
        assert body["window_bars"] == 100 and body["seed"] == 11
        assert body["hypothesis_id"] == "hyp1"

    def test_staged_posts_endpoint(self):
        url, body = self._call(
            "run_staged_validation",
            strategy_id="st1", symbol="BTCUSDT", timeframe="4h",
            start="2023-02-01T00:00:00Z", end="2023-05-01T00:00:00Z",
            segmentation="explicit", segments=[{"label": "segA"}],
            hypothesis_id="hyp1")
        assert url == "/api/research/staged-validation"
        assert body["segmentation"] == "explicit"
        assert body["segments"] == [{"label": "segA"}]


class TestQueryIndicatorBareBody:
    """query_indicator 可选键全缺省侧（indicator/groups/params/reductions/
    start/end 均 is not None 透传——缺省侧不带键；单组/批量互斥交 API
    单一事实源判，工具层不吞键）。"""

    def test_no_optional_keys_bare_body(self):
        mcp, api = _FakeMcp(), _FakeApi()
        from mcpserver import tools as tools_mod

        tools_mod.register_research_tools(mcp, api)
        mcp.registered["query_indicator"](
            symbol="BTCUSDT", timeframe="4h")
        url, body = api.posts[0]
        assert url == "/api/research/query-indicator"
        assert body == {"symbol": "BTCUSDT", "timeframe": "4h"}, \
            "可选键缺省侧不带 indicator/groups 等键（裸 body）"
