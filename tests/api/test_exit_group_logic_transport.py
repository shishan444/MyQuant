"""API 层组内逻辑键传输（add-exit-group-or 特征网补齐——tester）。

覆盖缺口（对照 delta spec 与既有键族 API 级先例——direction 有
test_direction_registration、组键有 test_group_keys_transport）：
路由接线面（HypothesisCreate 顶层两键 → 路由取参 → 服务层，routes
+2 行）此前零覆盖——MCP 面测试经 _FakeApi 只锚 body 形成不经过
真实路由，服务层测试直调绕过路由；路由若漏传两键，全部既有测试
照绿（键静默丢失、登记退化为无键形）。

- 接线全链：POST 两键 → conditions_json 落库（「or」才写键）→
  翻译产物 logic_genes 两通道 "OR"
- 值域 422（RESEARCH_CONDITION_INVALID/legal_values——API 面错误
  即文档）
- 逻辑键无作用组 422（delta scenario 字面「422，说明逻辑键无作用
  组」的 API 面落位）
- 归一双身份（API 面显式 "and" 与缺省 duplicated 同 id——direction
  A1-7 同款）
- 非字符串值路由层 pydantic 拦截（direction A1-6 同款通道分层）
- MCP 工具通道值域错经真实路由转译（direction 收口先例 _HttpApi
  形制）
"""
import json

import pytest

pytestmark = [pytest.mark.integration]

RSI = {"indicator": "RSI", "params": {"period": 14}, "field": "rsi"}
_PARTIAL_GROUP = [{"subject": RSI, "action": "gt", "target": 75}]


def _body(**extra) -> dict:
    body = {
        "conditions": {
            "when_conditions": [{"subject": RSI, "action": "lt",
                                 "target": 30}],
            "then_conditions": [{"subject": RSI, "action": "gt",
                                 "target": 70}],
        },
        "statement": "exit-group-transport",
        "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": "2024-01-01T00:00:00Z",
                         "end": "2024-06-01T00:00:00Z"}],
        "strategy_params": {"stop_loss": 0.05},
    }
    body.update(extra)
    return body


def _conditions_of(api_client, hypothesis_id: str) -> dict:
    from core.persistence.repository.research import get_hypothesis

    return json.loads(get_hypothesis(
        api_client.app.state.db_path, hypothesis_id)["conditions"])


class TestLogicKeyTransport:
    """路由接线全链：body 顶层两键 → 服务层 → conditions_json → 翻译。"""

    def test_or_keys_routed_persisted_and_translated(self, api_client):
        """带两键（组在场）POST → 落库 conditions 顶层两键=="or"
        （路由漏传则服务层收 None 不写键，此处即红）→ 翻译产物
        logic_genes.exit_logic/reduce_logic 均 "OR"（全链到产物）。"""
        r = api_client.post("/api/research/hypotheses", json=_body(
            exit_logic="or",
            exit_partial_conditions=_PARTIAL_GROUP,
            partial_exit_logic="or"))
        assert r.status_code == 200, r.text
        hid = r.json()["hypothesis_id"]
        conds = _conditions_of(api_client, hid)
        assert conds["exit_logic"] == "or"
        assert conds["partial_exit_logic"] == "or"

        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.repository.strategy import get_strategy

        db = api_client.app.state.db_path
        t = translate_hypothesis(db, hypothesis_id=hid)
        dna = json.loads(get_strategy(db, t["strategy_id"])["dna_json"])
        assert dna["logic_genes"]["exit_logic"] == "OR"
        assert dna["logic_genes"]["reduce_logic"] == "OR"

    def test_omitted_keys_absent_from_conditions(self, api_client):
        """缺省零扰动：不带键 POST → conditions 顶层无两键
        （省略不落库，与组键 transport 同语义）。"""
        r = api_client.post("/api/research/hypotheses", json=_body())
        assert r.status_code == 200, r.text
        conds = _conditions_of(api_client, r.json()["hypothesis_id"])
        assert "exit_logic" not in conds
        assert "partial_exit_logic" not in conds


class TestApiRejection422:
    """delta scenario 的 422 断言 API 面落位（错误即文档）。"""

    def test_bad_value_422_with_legal_values(self, api_client):
        """exit_logic="AND"（大写）→ 422 RESEARCH_CONDITION_INVALID，
        内层 detail 附 field/legal_values。"""
        r = api_client.post("/api/research/hypotheses",
                            json=_body(exit_logic="AND"))
        assert r.status_code == 422, r.text
        outer = r.json()["detail"]
        assert outer["code"] == "RESEARCH_CONDITION_INVALID"
        assert outer["detail"]["field"] == "exit_logic"
        assert outer["detail"]["legal_values"] == ["and", "or"]

    def test_host_group_missing_422(self, api_client):
        """partial_exit_logic="or" 而组缺失 → 422，message 说明
        「逻辑键无作用组」，内层 detail 附 host_group（scenario 字面）。"""
        r = api_client.post("/api/research/hypotheses",
                            json=_body(partial_exit_logic="or"))
        assert r.status_code == 422, r.text
        outer = r.json()["detail"]
        assert outer["code"] == "RESEARCH_CONDITION_INVALID"
        assert "逻辑键无作用组" in outer["message"]
        assert outer["detail"]["host_group"] == "exit_partial_conditions"

    def test_non_string_layered_rest(self, api_client):
        """非字符串值（123）被路由层 pydantic 拦截（框架 422，
        loc=body.exit_logic string_type）——与 direction A1-6 同款
        通道分层；服务层 not in 集合兜底由服务级测试承载。"""
        r = api_client.post("/api/research/hypotheses",
                            json=_body(exit_logic=123))
        assert r.status_code == 422, r.text
        errors = r.json()["detail"]
        assert isinstance(errors, list)
        assert any(e.get("type") == "string_type"
                   and "exit_logic" in (e.get("loc") or []) for e in errors)


class TestApiNormalizationIdentity:
    """API 面归一双身份（direction A1-7 同款）。"""

    def test_explicit_and_same_identity_as_missing(self, api_client):
        """无键登记后再显式 "and" POST → duplicated=True 同 id
        （归一在服务层，API 面同指纹同实体）。"""
        r1 = api_client.post("/api/research/hypotheses", json=_body())
        assert r1.status_code == 200, r1.text
        r2 = api_client.post("/api/research/hypotheses",
                             json=_body(exit_logic="and"))
        assert r2.status_code == 200, r2.text
        assert r2.json()["duplicated"] is True
        assert r2.json()["hypothesis_id"] == r1.json()["hypothesis_id"]


class _FakeMcp:
    def __init__(self):
        self.registered = {}

    def tool(self):
        def deco(fn):
            self.registered[fn.__name__] = fn
            return fn
        return deco


class TestMcpToolChannelFullChain:
    """MCP 工具通道经真实路由的值域错转译（direction 收口先例形制
    ——错误转译复用 _raise_api_error 同一函数）。"""

    def _mcp_register_fn(self, api_client):
        from mcpserver import tools as tools_mod

        class _HttpApi:
            """MCP 工具 api 依赖薄壳：走 TestClient 真实路由。"""

            def post(self, url, body):
                resp = api_client.post(url, json=body)
                if resp.is_error:
                    from mcpserver.api_client import _raise_api_error
                    _raise_api_error(resp)
                return resp.json()

        mcp = _FakeMcp()
        tools_mod.register_research_tools(mcp, _HttpApi())
        return mcp.registered["register_hypothesis"]

    def test_bad_value_via_mcp_tool_channel(self, api_client):
        """exit_logic="AND" 经 MCP 工具通道 → 穿透 pydantic（字符串
        形制合法）到服务层值域校验 → {"error": RESEARCH_CONDITION_
        INVALID}（legal_values 随行）。"""
        fn = self._mcp_register_fn(api_client)
        out = fn(
            when_conditions=[{"subject": RSI, "action": "lt",
                              "target": 30}],
            then_conditions=[{"subject": RSI, "action": "gt",
                              "target": 70}],
            statement="s",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-01-01T00:00:00Z",
                          "end": "2024-06-01T00:00:00Z"}],
            strategy_params={"stop_loss": 0.05}, exit_logic="AND")
        assert "error" in out
        assert out["error"]["code"] == "RESEARCH_CONDITION_INVALID"
        assert out["error"]["detail"]["legal_values"] == ["and", "or"]
