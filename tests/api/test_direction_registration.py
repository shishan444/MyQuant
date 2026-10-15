"""做空假设通道——登记面（add-direction-channel A1，A1-1~A1-10）。

覆盖 tasks A1 完成标准六项：
- 值域拒绝（字符串值域错三通道穿透服务层 3.7 步，422 错误即文档）
- short 归一写键落库 + hash 异于不带方向（指纹输入与 conditions_json 同源）
- 双方向独立实体（同底层条件集+同区间集，双向登记序均两实体、hash 互异）
- 不带键登记逐字节回归（conditions 无键、返回 direction="long"）
- 幂等命中回显既有方向（含 short）
- 非字符串值通道分层（REST 面路由层 pydantic 拦截 / service 直调
  3.7 步 not in 集合天然兜住——两通道分开断言）
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import (
    ResearchError,
    hash_json,
    register_hypothesis,
)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.research import get_hypothesis

CONDITIONS = {
    "when_conditions": [{"subject": "close", "action": "gt", "target": 0}],
    "then_conditions": [{"subject": "close", "action": "gt", "target": -1}],
}
RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
           "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


def _body(direction=None, statement="方向测试"):
    body = {"conditions": CONDITIONS, "statement": statement,
            "data_ranges": RANGES}
    if direction is not None:
        body["direction"] = direction
    return body


class TestDirectionValueDomain:
    """A1-1/A1-6：值域拒绝与错误通道分层。"""

    def test_bad_string_value_rejected_with_detail(self, api_client):
        """A1-1：字符串值域错 → 422 RESEARCH_PARAM_INVALID，错误即文档。"""
        resp = api_client.post("/api/research/hypotheses", json=_body("both"))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["field"] == "direction"
        assert detail["detail"]["legal_range"] == ["long", "short"]

    def test_non_string_layered_rest(self, api_client):
        """A1-6（REST 面）：非字符串值被路由层 pydantic 拦截（框架 422）。"""
        resp = api_client.post("/api/research/hypotheses",
                               json=_body(direction=123))
        assert resp.status_code == 422, resp.text
        errors = resp.json()["detail"]
        assert isinstance(errors, list)
        assert any("direction" in (e.get("loc") or []) for e in errors)

    def test_non_string_layered_service_direct(self, tmp_path: Path):
        """A1-6（service 直调面）：绕过 pydantic 的通道由 3.7 步
        not in 集合判定对跨类型值天然兜住。"""
        db = tmp_path / "q.db"
        init_db_ext(db)
        from core.analysis.executor import ensure_research_tables
        ensure_research_tables(db)
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=json.loads(json.dumps(CONDITIONS)),
                statement="s", data_ranges=RANGES, direction=123)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["field"] == "direction"


class TestDirectionIdentity:
    """A1-2/A1-3/A1-4：写键、双实体隔离、缺省回归。"""

    def test_short_writes_conditions_key_and_changes_hash(self, api_client):
        """A1-2：short 归一写键进 conditions_json；hash 异于不带方向。"""
        r_base = api_client.post(
            "/api/research/hypotheses", json=_body()).json()
        r_short = api_client.post(
            "/api/research/hypotheses", json=_body("short")).json()

        cond_short = json.loads(
            get_hypothesis(api_client.app.state.db_path,
                           r_short["hypothesis_id"])["conditions"])
        assert cond_short["direction"] == "short"
        cond_base = json.loads(
            get_hypothesis(api_client.app.state.db_path,
                           r_base["hypothesis_id"])["conditions"])
        assert "direction" not in cond_base
        assert hash_json(cond_short) != hash_json(cond_base)

    @pytest.mark.parametrize("order", ["long_first", "short_first"])
    def test_dual_direction_independent_entities(self, api_client, order):
        """A1-3：双向登记序均两实体、两 id、hash 互异（不互相顶掉）。"""
        seq = (["short", None] if order == "long_first"
               else [None, "short"])
        ids = []
        for d in seq:
            r = api_client.post(
                "/api/research/hypotheses",
                json=_body(d, statement=f"hyp-{d}")).json()
            assert r["duplicated"] is False
            ids.append(r["hypothesis_id"])
        assert ids[0] != ids[1]
        c1 = json.loads(get_hypothesis(
            api_client.app.state.db_path, ids[0])["conditions"])
        c2 = json.loads(get_hypothesis(
            api_client.app.state.db_path, ids[1])["conditions"])
        assert hash_json(c1) != hash_json(c2)

    def test_missing_key_is_long_and_conditions_unchanged(self, api_client):
        """A1-4：不带键登记 conditions 无 direction 键混入（canonical
        化 subject 形制为既有行为）、hash 稳定、回显 long。"""
        r = api_client.post(
            "/api/research/hypotheses", json=_body()).json()
        assert r["direction"] == "long"
        cond = json.loads(get_hypothesis(
            api_client.app.state.db_path, r["hypothesis_id"])["conditions"])
        assert "direction" not in cond  # 归一写键不发生（缺省路径零扰动）
        r2 = api_client.post(
            "/api/research/hypotheses", json=_body()).json()
        assert r2["duplicated"] is True  # hash 稳定（canonical 形既有一致）

    def test_explicit_long_same_identity_as_missing(self, api_client):
        """A1-7：显式 long 与缺省同指纹同实体（归一形 long 不写键）。"""
        r1 = api_client.post(
            "/api/research/hypotheses", json=_body()).json()
        r2 = api_client.post(
            "/api/research/hypotheses", json=_body("long")).json()
        assert r2["duplicated"] is True
        assert r2["hypothesis_id"] == r1["hypothesis_id"]
        assert r2["direction"] == "long"


class TestDirectionEcho:
    """A1-5：幂等命中回显既有方向。"""

    def test_idempotent_replay_echoes_short(self, api_client):
        """A1-5：带 short 重放命中既有 short 假设，回显 short。

        注意：不带键重放不构成同身份（conditions 无 direction 键 →
        hash 回到 base 身份 → duplicated=false 新实体）——归一写键的
        必然结果，与 test_dual_direction_independent_entities 一致。
        """
        r_short = api_client.post(
            "/api/research/hypotheses", json=_body("short")).json()
        assert r_short["direction"] == "short"
        r_again = api_client.post(
            "/api/research/hypotheses", json=_body("short")).json()
        assert r_again["duplicated"] is True
        assert r_again["hypothesis_id"] == r_short["hypothesis_id"]
        assert r_again["direction"] == "short"

    def test_idempotent_missing_key_replay_echoes_long(self, api_client):
        """A1-5 补：base 假设（无方向）不带键重放（分支①）回显 long。"""
        r1 = api_client.post(
            "/api/research/hypotheses", json=_body()).json()
        r2 = api_client.post(
            "/api/research/hypotheses", json=_body()).json()
        assert r2["duplicated"] is True
        assert r2["hypothesis_id"] == r1["hypothesis_id"]
        assert r2["direction"] == "long"


class _FakeMcp:
    def __init__(self):
        self.registered = {}

    def tool(self):
        def deco(fn):
            self.registered[fn.__name__] = fn
            return fn
        return deco


class TestDirectionChannelLayeringFull:
    """A1 完成标准⑥补全：字符串值域错三通道收口（tester 补）。

    既有用例已覆盖 REST 面（A1-1）与非字符串的 REST/service 直调
    （A1-6 两用例）；此处补 service 直调字符串值域错与 MCP 工具通道
    两形制（经 tools.py api.post 到 REST 路由——服务端同一校验点，
    错误转译复用 QuantApiClient._raise_api_error 同一函数）。
    """

    def test_bad_string_service_direct(self, tmp_path: Path):
        """字符串值域错（service 直调通道）→ 3.7 步 RESEARCH_PARAM_INVALID。"""
        db = tmp_path / "q.db"
        init_db_ext(db)
        from core.analysis.executor import ensure_research_tables
        ensure_research_tables(db)
        with pytest.raises(ResearchError) as ei:
            register_hypothesis(
                db, conditions=json.loads(json.dumps(CONDITIONS)),
                statement="s", data_ranges=RANGES, direction="both")
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["field"] == "direction"

    def _mcp_register_fn(self, api_client):
        from mcpserver import tools as tools_mod

        class _HttpApi:
            """MCP 工具 api 依赖薄壳：走 TestClient 真实路由，
            错误转译复用 QuantApiClient._raise_api_error（同形制）。"""

            def post(self, url, body):
                resp = api_client.post(url, json=body)
                if resp.is_error:
                    from mcpserver.api_client import _raise_api_error
                    _raise_api_error(resp)
                return resp.json()

        mcp = _FakeMcp()
        tools_mod.register_research_tools(mcp, _HttpApi())
        return mcp.registered["register_hypothesis"]

    def test_non_string_via_mcp_tool_channel(self, api_client):
        """非字符串值（MCP 工具通道）：与 REST 面同值同形制——
        被路由层 pydantic 拦截（string_type + body.direction loc），
        tool_safe 统一错误通道转 {"error": {...}} 返回。"""
        fn = self._mcp_register_fn(api_client)
        out = fn(when_conditions=CONDITIONS["when_conditions"],
                 then_conditions=CONDITIONS["then_conditions"],
                 statement="s", data_ranges=RANGES, direction=123)
        assert "error" in out
        assert out["error"]["code"] == "API_INTERNAL_ERROR"  # 422 框架形制
        detail = out["error"]["detail"]
        assert isinstance(detail, list)
        assert any(e.get("type") == "string_type"
                   and "direction" in (e.get("loc") or []) for e in detail)

    def test_bad_string_via_mcp_tool_channel(self, api_client):
        """字符串值域错（MCP 工具通道）：pydantic 不校验字符串值域，
        穿透到服务层 3.7 步 → {"error": RESEARCH_PARAM_INVALID}。"""
        fn = self._mcp_register_fn(api_client)
        out = fn(when_conditions=CONDITIONS["when_conditions"],
                 then_conditions=CONDITIONS["then_conditions"],
                 statement="s", data_ranges=RANGES, direction="both")
        assert "error" in out
        assert out["error"]["code"] == "RESEARCH_PARAM_INVALID"
        assert out["error"]["detail"]["field"] == "direction"
        assert out["error"]["detail"]["legal_range"] == ["long", "short"]
