"""出场组内 OR 通道——MCP 面透传与 docstring 披露（add-exit-group-or 任务 5.1）。

- 透传：exit_logic/partial_exit_logic 经 register_hypothesis 进请求体
  顶层（is not None 形制；显式 "and" 同样透传——值域权威在服务层）
- 参数对拍：工具形参集 ⊇ HypothesisCreate 顶层键（两新键两侧在场）
- docstring 披露锚（D5 措辞三处同步之一）：值域/归一化/混组边界/
  add 组不设逻辑键四要点在 description 在场
- Q-E2 负向锚按现行行为定形：未知顶层键（如 add 组逻辑键）被
  pydantic 忽略——行为断言取「or 形产物 add_logic 无 OR 通道」
  （tests/analysis/test_exit_group_logic_translation.py 同名用例承载）
"""

import inspect

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
        when_conditions=[{"subject": {"indicator": "RSI",
                                       "params": {"period": 14},
                                       "field": "rsi"},
                          "action": "lt", "target": 30}],
        then_conditions=[{"subject": {"indicator": "RSI",
                                      "params": {"period": 14},
                                      "field": "rsi"},
                          "action": "gt", "target": 70}],
        statement="s", data_ranges=_RANGES, **kw)
    return out, api.posts[0]


class TestLogicKeyPassthrough:
    def test_or_in_body(self):
        """exit_logic="or" 透传进请求体顶层。"""
        _, (url, body) = _register_and_call(exit_logic="or")
        assert url == "/api/research/hypotheses"
        assert body["exit_logic"] == "or"

    def test_partial_or_in_body(self):
        """partial_exit_logic="or" 同款透传（partial 组在场形）。"""
        _, (_, body) = _register_and_call(
            exit_partial_conditions=[
                {"subject": {"indicator": "RSI", "params": {"period": 14},
                             "field": "rsi"}, "action": "gt", "target": 75}],
            partial_exit_logic="or")
        assert body["partial_exit_logic"] == "or"
        assert body["exit_partial_conditions"][0]["action"] == "gt"

    def test_none_omitted_and_and_passthrough(self):
        """缺省省略；显式 "and" 同样透传（归一化在服务层——显式 and
        与缺省同指纹同实体）。"""
        _, (_, body) = _register_and_call()
        assert "exit_logic" not in body
        assert "partial_exit_logic" not in body
        _, (_, body2) = _register_and_call(exit_logic="and")
        assert body2["exit_logic"] == "and"


class TestSchemaParity:
    def test_tool_params_cover_create_model_fields(self):
        """工具形参集 ⊇ HypothesisCreate 顶层键（两新键两侧在场——
        防单侧漂移）。"""
        from api.schemas import HypothesisCreate
        from mcpserver import tools as tools_mod

        mcp, api = _FakeMcp(), _FakeApi()
        tools_mod.register_research_tools(mcp, api)
        params = set(inspect.signature(
            mcp.registered["register_hypothesis"]).parameters)
        model_fields = set(HypothesisCreate.model_fields) - {"conditions"}
        assert "exit_logic" in params
        assert "partial_exit_logic" in params
        assert model_fields <= params, (
            f"工具缺参数: {model_fields - params}")


class TestDocstringDisclosure:
    def _desc(self) -> str:
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        mcp, _ = build_server(profile="research")
        tools = run_async(mcp.list_tools())
        return {t.name: t for t in tools}["register_hypothesis"].description

    def test_value_domain_and_normalization_disclosed(self):
        """值域（小写封闭）与归一化（缺省与显式 and 同身份、「or」才
        入指纹）披露在场。"""
        desc = self._desc()
        assert '{"and","or"}' in desc
        assert "缺省与显式 \"and\" 语义相同" in desc
        assert "「or」才入条件指纹" in desc

    def test_mixed_group_boundary_disclosed(self):
        """混组边界（D5 措辞）：(内置1 OR 内置2) AND (插件A AND 插件B)
        合成语义披露在场。"""
        desc = self._desc()
        assert "(内置1 OR 内置2) AND (插件A AND 插件B)" in desc
        assert "插件源维持独立 AND 通道" in desc

    def test_add_group_no_logic_key_disclosed(self):
        """add 组不设逻辑键披露（Q-E2 裁决不扩——加仓语义维持合取）。"""
        desc = self._desc()
        assert "组不设逻辑键（加仓语义维持合取" in desc
        assert "add_logic 恒 AND" in desc

    def test_host_group_guard_disclosed(self):
        """逻辑键无作用组拒绝披露在场。"""
        desc = self._desc()
        assert "逻辑键无作用组" in desc
