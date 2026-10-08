"""MCP 工具层 body 组装行为锚（trigger_policy 透传面）。

contract_parity 承担 AST 结构对拍；此处补行为级：register_hypothesis
的 body 组装对可选字段的条件透传（带 policy 传入 / 不带省略）。
"""

import pytest

pytestmark = [pytest.mark.integration]


class _FakeMcp:
    """捕获 @mcp.tool() 注册的工具函数（恒等装饰）。"""

    def __init__(self):
        self.registered = {}

    def tool(self):
        def deco(fn):
            self.registered[fn.__name__] = fn
            return fn
        return deco


class _FakeApi:
    def __init__(self):
        self.posts = []

    def post(self, url, body):
        self.posts.append((url, body))
        return {"hypothesis_id": "h1"}


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
