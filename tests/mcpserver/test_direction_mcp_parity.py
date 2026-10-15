"""做空假设通道——MCP 面对拍（add-direction-channel A2/A4，M1/M2/A3-5）。

- M1：register_hypothesis direction 透传 body（is not None 形制）
- M2：工具参数集与 HypothesisCreate 顶层键对齐（direction 在两侧）
- A3-5：三关工具 description 含空头口径披露句（人话标注契约）
"""

import inspect

import pytest

pytestmark = [pytest.mark.integration]


class _FakeMcp:
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


class TestDirectionPassthrough:
    def test_short_in_body(self):
        """M1：direction="short" 透传进请求体顶层。"""
        _, (url, body) = _register_and_call(direction="short")
        assert url == "/api/research/hypotheses"
        assert body["direction"] == "short"

    def test_none_omitted_and_long_in_body(self):
        """M1 补：缺省省略；显式 long 同样透传（值域权威在服务层）。"""
        _, (_, body) = _register_and_call()
        assert "direction" not in body
        _, (_, body2) = _register_and_call(direction="long")
        assert body2["direction"] == "long"


class TestSchemaParity:
    def test_tool_params_cover_create_model_fields(self):
        """M2：工具形参集 ⊇ HypothesisCreate 顶层键（direction 在两侧）。

        contract_parity 的 AST schema 对拍承担通用条款；此处锚
        direction 键的两侧在场（防单侧漂移）。
        """
        from api.schemas import HypothesisCreate
        from mcpserver import tools as tools_mod

        mcp, api = _FakeMcp(), _FakeApi()
        tools_mod.register_research_tools(mcp, api)
        params = set(inspect.signature(
            mcp.registered["register_hypothesis"]).parameters)
        # conditions 在工具面拆为 when_conditions/then_conditions 两参数
        # （既有映射形制），对拍集排除之
        model_fields = set(HypothesisCreate.model_fields) - {"conditions"}
        assert "direction" in params
        assert "direction" in model_fields
        assert model_fields <= params, (
            f"工具缺参数: {model_fields - params}")


class TestGateToolDisclosure:
    def test_three_gate_descriptions_carry_short_convention(self):
        """A3-5：三关工具 description 含空头口径披露句。"""
        from mcpserver import tools as tools_mod

        mcp, api = _FakeMcp(), _FakeApi()
        tools_mod.register_research_tools(mcp, api)
        for name in ("run_isolated_validation",
                     "run_random_window_test",
                     "run_staged_validation"):
            doc = inspect.getdoc(mcp.registered[name])
            assert doc is not None
            assert "direction=short" in doc, f"{name} 缺方向披露句"
            assert "空头口径" in doc, f"{name} 缺口径说明"
            assert "benchmark_return 恒为同期多头买入持有" in doc, \
                f"{name} 缺基准口径句"
