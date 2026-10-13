"""M1 错误通道测试（improve-mcp-agent-loop 1.1/1.2/1.3）。

覆盖三件：ApiError 解析（409 结构化透传 / 500 无 body 降级 / 连接失败）、
hint 注册表与服务端实际抛出码对拍（漂移即红）、tool_safe 装饰器失败
注入（code+hint 齐备）与成功路径形制不变。
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from mcpserver.api_client import (
    ApiError,
    ApiUnreachable,
    EndpointNotAllowed,
    QuantApiClient,
)
from mcpserver.errors import HINTS, error_payload, hint_for, payload_from
from mcpserver.tools import tool_safe

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeResp:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self.is_error = status_code >= 400
        self.reason_phrase = "Fake"
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeCtx:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        return self._resp

    def post(self, url, json=None):
        return self._resp


def _client_with(monkeypatch, resp) -> QuantApiClient:
    import mcpserver.api_client as ac
    monkeypatch.setattr(ac.httpx, "Client", lambda **kw: _FakeCtx(resp))
    return QuantApiClient(base_url="http://x", profile="research")


class TestApiErrorParsing:
    """1.1 三类失败：结构化透传 / 降级 / 不可达。"""

    def test_409_structured_body_passthrough(self, monkeypatch) -> None:
        """4xx 带 code 的结构化 body → 透传工程码族。"""
        resp = _FakeResp(409, body={
            "detail": {"code": "RESEARCH_RANGE_OVERLAP",
                       "message": "区间重叠",
                       "detail": {"judged_ranges": [], "overlaps": ["R"]}}})
        api = _client_with(monkeypatch, resp)
        with pytest.raises(ApiError) as ei:
            api.post("/api/research/validate-backtest", {})
        assert ei.value.status == 409
        assert ei.value.code == "RESEARCH_RANGE_OVERLAP"
        assert ei.value.message == "区间重叠"
        assert ei.value.detail == {"judged_ranges": [], "overlaps": ["R"]}

    def test_500_no_body_degrades(self, monkeypatch) -> None:
        """5xx 无结构化 body → 降级 API_INTERNAL_ERROR 形式（message 状态行）。"""
        api = _client_with(monkeypatch, _FakeResp(500))
        with pytest.raises(ApiError) as ei:
            api.get("/api/research/hypotheses")
        assert ei.value.code == "API_INTERNAL_ERROR"
        assert "500" in ei.value.message

    def test_422_pydantic_detail_preserved(self, monkeypatch) -> None:
        """422 pydantic 校验失败（detail 为 list 无 code）→ 降级但保留原始校验信息。"""
        resp = _FakeResp(422, body={"detail": [{"loc": ["body", "conditions"],
                                                 "msg": "Field required"}]})
        api = _client_with(monkeypatch, resp)
        with pytest.raises(ApiError) as ei:
            api.post("/api/research/hypotheses", {"bad": 1})
        assert ei.value.code == "API_INTERNAL_ERROR"
        assert isinstance(ei.value.detail, list)

    def test_connect_error_unreachable(self) -> None:
        """连接失败 → ApiUnreachable（不变：不误判为服务端拒绝）。"""
        api = QuantApiClient(base_url="http://127.0.0.1:1", profile="research")
        with pytest.raises(ApiUnreachable):
            api.get("/api/research/hypotheses")


class TestHintRegistry:
    """1.2 注册表对拍：覆盖服务端实际抛出的全部 ResearchError 码。"""

    def test_registry_covers_all_raised_codes(self) -> None:
        """静态扫描 core/analysis + api/routes 的 raise ResearchError 码，注册表须全覆盖。"""
        raised: set[str] = set()
        for src_dir in ("core/analysis", "api/routes"):
            for path in (REPO_ROOT / src_dir).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                raised.update(re.findall(r'ResearchError\(\s*"([A-Z_]+)"', text))
        assert raised, "扫描结果为空——源码路径或提取模式失效"
        missing = raised - HINTS.keys()
        assert not missing, f"注册表缺码（hint 不可行动）: {sorted(missing)}"

    def test_mcp_layer_codes_registered(self) -> None:
        """MCP 客户端侧与降级码也须有 hint。"""
        for code in ("MCP_ENDPOINT_NOT_ALLOWED", "API_UNREACHABLE",
                     "API_INTERNAL_ERROR"):
            assert code in HINTS

    def test_template_hint_matches_authority(self) -> None:
        """hint 模板清单与 _TEMPLATES 权威源集合对拍（fix-hint-template-list 1.2）。

        hint 文案是静态抄录（字符串字面量无法引用元组），本断言让任何
        模板扩容自动逼同步——清单漂移即红（0.2.5/0.2.6/0.2.7 三轮扩容
        均漏同步的根因修复）。
        """
        from api.routes.research.analysis import _TEMPLATES

        hint = HINTS["RESEARCH_TEMPLATE_UNKNOWN"]
        m = re.search(r"template\s*∈\s*([A-Za-z_|]+)", hint)
        assert m, f"hint 文案缺 'template ∈ ' 管道清单锚点: {hint!r}"
        listed = set(m.group(1).split("|"))
        assert listed == set(_TEMPLATES), (
            f"hint 模板清单与 _TEMPLATES 失联: "
            f"hint 多出 {sorted(listed - set(_TEMPLATES))} / "
            f"漏掉 {sorted(set(_TEMPLATES) - listed)}"
        )

    def test_unknown_code_falls_back(self) -> None:
        """未注册码回退通用指引（不空、不抛）。"""
        hint = hint_for("RESEARCH_NEVER_SEEN_BEFORE")
        assert hint and hint != ""


class TestToolSafeDecorator:
    """1.3 装饰器：失败转 {"error": {...}}；成功形制不变。"""

    def test_api_error_becomes_payload(self) -> None:
        @tool_safe
        def probe():
            raise ApiError(409, "RESEARCH_RANGE_OVERLAP", "区间重叠",
                           {"overlaps": ["R"]})

        out = probe()
        err = out["error"]
        assert set(err) == {"code", "message", "detail", "hint"}
        assert err["code"] == "RESEARCH_RANGE_OVERLAP"
        assert "并集" in err["hint"]  # hint 可行动（指明换区间）

    @pytest.mark.parametrize("code", [
        "RESEARCH_HYPOTHESIS_TERMINAL",
        "RESEARCH_EVIDENCE_NOT_FOUND",
        "RESEARCH_TRANSITION_INVALID",
        "RESEARCH_DATA_INSUFFICIENT",
        "RESEARCH_WARMUP_INSUFFICIENT",
        "RESEARCH_NOT_EXPRESSIBLE",
        "RESEARCH_CONDITION_INVALID",
        "API_INTERNAL_ERROR",
    ])
    def test_eight_failure_codes_carry_hint(self, code) -> None:
        """≥8 类典型失败：code+hint 齐备（hint 是注册值而非回退值）。"""

        @tool_safe
        def probe():
            raise ApiError(422, code, "x", None)

        err = probe()["error"]
        assert err["code"] == code
        assert err["hint"] == HINTS[code]

    def test_endpoint_not_allowed(self) -> None:
        @tool_safe
        def probe():
            raise EndpointNotAllowed("POST 端点 /x 不在白名单")

        err = probe()["error"]
        assert err["code"] == "MCP_ENDPOINT_NOT_ALLOWED"
        assert "档" in err["hint"]

    def test_unreachable(self) -> None:
        @tool_safe
        def probe():
            raise ApiUnreachable("API 不可达")

        err = probe()["error"]
        assert err["code"] == "API_UNREACHABLE"

    def test_success_path_unchanged(self) -> None:
        """成功路径零改变：原样返回，无包裹层。"""

        @tool_safe
        def probe():
            return {"items": [1, 2], "total": 2}

        assert probe() == {"items": [1, 2], "total": 2}


class TestErrorChannelHints:
    """fix-mcp-error-channel 卡6（0.3.2）：hint 参数面可执行性——
    通用文案去参数绑定 + 新码条目语义 + 快照调用点特化覆写直测。"""

    def test_warmup_hint_generalized_no_start_binding(self) -> None:
        """specs「快照 warmup 报错指引与参数面匹配」静态面：
        RESEARCH_WARMUP_INSUFFICIENT 文案不得含「start 前移」参数
        绑定（快照参数面无 start），须含对一切调用方成立的通用
        动作集（短 lookback / 降周期 / 扩数据前缀）。"""
        hint = HINTS["RESEARCH_WARMUP_INSUFFICIENT"]
        assert "start 前移" not in hint, \
            f"warmup hint 仍绑定 start 参数（快照参数面无 start）: {hint!r}"
        for action in ("lookback 更短", "降低请求周期"):
            assert action in hint, f"warmup hint 缺通用动作 {action!r}: {hint!r}"

    def test_internal_code_hint_directs_to_report(self) -> None:
        """RESEARCH_TEMPLATE_INTERNAL 条目：指向附 detail 提交问题
        报告 + 明示重试大概率无效（500 语义防盲重试，D3）。"""
        hint = HINTS["RESEARCH_TEMPLATE_INTERNAL"]
        assert "问题报告" in hint, f"新码 hint 须指向问题报告: {hint!r}"
        assert "重试" in hint, f"新码 hint 须含重试无效语义: {hint!r}"


class TestSnapshotHintOverride:
    """卡6/A3 快照调用点特化覆写：get_market_snapshot 内局部
    except ApiError 按码覆写 hint（不动 tool_safe 全局签名）。"""

    @staticmethod
    def _make_api_raising(code: str):
        api_holder: dict = {}

        def _raise(path, params=None):
            raise ApiError(422, code, "x", {"probe": 1})

        return api_holder, _raise

    def test_warmup_failure_hint_specialized_to_snapshot_params(self) -> None:
        """specs 场景锚：快照 warmup 失败 hint 仅涉及快照参数面实际
        存在的参数（降 timeframes 子集），不引用通用文案的不可执行
        指引（扩大数据前缀——快照参数面无该操作）。"""
        import asyncio

        from mcpserver.server import build_server
        mcp, api = build_server(profile="readonly", base_url="http://x")
        _, raiser = self._make_api_raising("RESEARCH_WARMUP_INSUFFICIENT")
        api.get = raiser

        from tests.mcpserver.test_contract_parity import run_async
        out = run_async(mcp.call_tool(
            "get_market_snapshot",
            {"symbol": "BTCUSDT", "timeframes": "1d,4h"}))
        err = out.structured_content["result"]["error"]
        assert err["code"] == "RESEARCH_WARMUP_INSUFFICIENT"
        assert "降低 timeframes" in err["hint"], \
            f"hint 须为快照特化文案: {err['hint']!r}"
        assert "可得数据前缀" not in err["hint"], \
            "通用文案的不可执行指引不得残留（快照参数面无该操作）"

    def test_other_code_hint_not_specialized(self) -> None:
        """非 warmup 码不进覆写分支：走 tool_safe 全局注册表 hint
        （特化只对参数面失配的码成立）。"""
        import asyncio

        from mcpserver.server import build_server
        mcp, api = build_server(profile="readonly", base_url="http://x")
        _, raiser = self._make_api_raising("RESEARCH_CONDITION_INVALID")
        api.get = raiser

        from tests.mcpserver.test_contract_parity import run_async
        out = run_async(mcp.call_tool(
            "get_market_snapshot", {"symbol": "BTCUSDT"}))
        err = out.structured_content["result"]["error"]
        assert err["code"] == "RESEARCH_CONDITION_INVALID"
        assert err["hint"] == HINTS["RESEARCH_CONDITION_INVALID"], \
            "非 warmup 码不得被特化覆写"


class TestParamHintScenarioDispatch:
    """fix-research-c2（fr_ce325c609a8d）：RESEARCH_PARAM_INVALID 按
    detail.param 场景分派——specs「参数错误 hint 与场景对应」THEN 要求
    各自 hint 指向对应参数面、不出现与该错误无关的其他模板参数文案；
    场景表是注册表的增量层（并存断言：无场景命中回退注册表机制不动）。"""

    # 场景键 → 该参数面的判别词（hint 须含自身、不含其他场景的）
    _SCENE_MARKERS = {
        "forward_bars": "forward_bars",
        "min_samples": "min_samples",
        "condition.action": "legal_values",
        "data_range": "区间",
        "condition|conditions": "二选一",
    }

    @pytest.mark.parametrize(
        ("param", "marker"),
        [
            ("forward_bars", "forward_bars"),
            ("min_samples", "min_samples"),
            ("condition.action", "legal_values"),
            ("data_range", "区间"),
            ("condition|conditions", "二选一"),
        ],
        ids=["前向持有期", "小样本阈值", "比较动作", "区间参数", "键组分派"])
    def test_each_scene_hint_excludes_other_scenes(self, param, marker):
        """互不串面：场景 hint 含自身参数面判别词，且不含任何其他
        场景的判别词（错配文案回归即红）。"""
        p = error_payload("RESEARCH_PARAM_INVALID", "x", {"param": param})
        others = [m for k, m in self._SCENE_MARKERS.items() if k != param]
        assert marker in p["hint"], f"param={param} hint 未指向本参数面: {p['hint']!r}"
        for m in others:
            assert m not in p["hint"], (
                f"param={param} hint 串入其他参数面判别词 {m!r}: {p['hint']!r}")

    def test_no_scene_detail_falls_back_to_registry(self):
        """并存断言：detail 无 param（或非 dict）→ 回退注册表通用文案
        ——场景分派为增量层，注册表机制（hint_for）零改动。"""
        p = error_payload("RESEARCH_PARAM_INVALID", "x", None)
        assert p["hint"] == HINTS["RESEARCH_PARAM_INVALID"]
        assert hint_for("RESEARCH_PARAM_INVALID") == \
            HINTS["RESEARCH_PARAM_INVALID"]

    def test_run_analysis_tool_error_carries_legal_values(self):
        """A8 工具层透传：condition_stats 统计的未知 action 422 经
        run_analysis 工具可观测——错误载荷 code/detail.legal_values
        （price_above 在列）+ hint 为 condition.action 场景文案
        （静默点证据打到 MCP 工具层）。"""
        import asyncio

        from mcpserver.api_client import ApiError
        from mcpserver.server import build_server
        from tests.mcpserver.test_contract_parity import run_async

        legal = ["cross_above", "gt", "lt", "price_above", "price_below"]

        def _raise(path, json=None):
            raise ApiError(422, "RESEARCH_PARAM_INVALID",
                           "未识别的比较动作: 'stays_above'",
                           {"param": "condition.action",
                            "value": "stays_above",
                            "legal_values": legal})

        mcp, api = build_server(profile="research", base_url="http://x")
        api.post = _raise
        out = run_async(mcp.call_tool(
            "run_analysis",
            {"template": "condition_stats",
             "params": {"indicator": "EMA",
                        "condition": {"subject": "close",
                                      "action": "stays_above",
                                      "target": 1}},
             "symbol": "BTCUSDT", "timeframe": "1h",
             "start": "2024-01-01", "end": "2024-02-01"}))
        err = out.structured_content["result"]["error"]
        assert err["code"] == "RESEARCH_PARAM_INVALID"
        assert err["detail"]["param"] == "condition.action"
        assert "price_above" in err["detail"]["legal_values"]
        assert "legal_values" in err["hint"], \
            f"hint 须为动作场景文案: {err['hint']!r}"
        assert "forward_bars" not in err["hint"], "不得串入其他参数面文案"
