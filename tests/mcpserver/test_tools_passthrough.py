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
