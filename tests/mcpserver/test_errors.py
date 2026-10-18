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


# ---------------------------------------- 变更二 4.4 防漂移双向对拍

def _scan_param_invalid_keys():
    """AST 扫描全工程 RESEARCH_PARAM_INVALID 抛出面的 param/field 值集。

    三形制并入：①ResearchError("RESEARCH_PARAM_INVALID", ..., detail=
    {param/field: 字面量})；②HTTPException(422, detail={code:
    RESEARCH_PARAM_INVALID, param/field: 字面量})；③param_guard 守卫
    函数（_require_int_range/_require_enum）调用点的 name= 字面实参
    （guard 内 detail={"param": name} 为变量形——调用点即值源）。
    AST 提取非文本匹配（design 卡裁决）。

    已知限制：JoinedStr 动态拼接形（如 "strategy_params."+key 的
    32 处 f-string/detail 键）静态不可枚举——这类参数面在守护域外
    （变更二批次 review 已核，固有限制而非沉默不收）。
    """
    import ast, os

    found = set()
    # guard 家族（reviewer 变更二批次发现 2 补全）：param_guard 守卫
    # 三函数 name= 关键字实参 + case_inspection._validate_range_param
    # 的首位位置实参（name 形参）——guard 内 detail={"param": name}
    # 为变量形，调用点即值源
    guard_funcs = {"_require_int_range", "_require_enum",
                   "_require_time_window", "_require_iso_datetime",
                   "_validate_range_param"}
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for base in ("core", "api", "mcpserver"):
        for root, _, files in os.walk(os.path.join(repo_root, base)):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(root, fn)
                try:
                    tree = ast.parse(open(p).read())
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fname = (node.func.id if isinstance(node.func, ast.Name)
                             else (node.func.attr
                                   if isinstance(node.func, ast.Attribute)
                                   else None))
                    if fname == "ResearchError" and node.args and \
                            isinstance(node.args[0], ast.Constant) and \
                            node.args[0].value == "RESEARCH_PARAM_INVALID":
                        for kw in node.keywords:
                            if kw.arg == "detail" and \
                                    isinstance(kw.value, ast.Dict):
                                for k, v in zip(kw.value.keys,
                                                kw.value.values):
                                    if isinstance(k, ast.Constant) and \
                                            k.value in ("param", "field") and \
                                            isinstance(v, ast.Constant) and \
                                            isinstance(v.value, str):
                                        found.add(v.value)
                    elif fname == "HTTPException":
                        for kw in node.keywords:
                            if kw.arg == "detail" and \
                                    isinstance(kw.value, ast.Dict):
                                if any(
                                    isinstance(k, ast.Constant)
                                    and k.value == "code"
                                    and isinstance(v, ast.Constant)
                                    and v.value == "RESEARCH_PARAM_INVALID"
                                    for k, v in zip(kw.value.keys,
                                                    kw.value.values)):
                                    for k, v in zip(kw.value.keys,
                                                    kw.value.values):
                                        if isinstance(k, ast.Constant) and \
                                                k.value in ("param", "field") and \
                                                isinstance(v, ast.Constant) and \
                                                isinstance(v.value, str):
                                            found.add(v.value)
                    elif fname in guard_funcs:
                        for kw in node.keywords:
                            if kw.arg == "name" and \
                                    isinstance(kw.value, ast.Constant) and \
                                    isinstance(kw.value.value, str):
                                found.add(kw.value.value)
                        # 位置参数形（_validate_range_param(name, ...)——
                        # name 为首位形参）
                        if fname == "_validate_range_param" and node.args and \
                                isinstance(node.args[0], ast.Constant) and \
                                isinstance(node.args[0].value, str):
                            found.add(node.args[0].value)
    return found


# 已知豁免集（known-gaps 快照锚定——变更二 4.4 落地时点的差集）。
# 语义：这些参数面存在但暂无场景专属 hint（走注册表通用文案）——
# 有意为之的基线；新增参数面不在此集且不在场景表 → 反向断言红
# （显式选择：补场景表或加豁免集）。
_KNOWN_GAPS = frozenset({
    "add_fraction", "add_suppress_conditions", "condition",
    "condition.inner_action", "condition.subject", "condition.target",
    "condition.window", "conditions", "db_path", "direction",
    "exit_fraction", "features", "grid", "grid.modes[].mode",
    "grid.modes[].stops", "grid.modes[].take_profits", "groups",
    "horizon", "horizons", "indicator", "indicators", "leverage",
    "max_exposure", "max_hold", "max_lag", "min_events", "n_neighbors",
    "params",
    "position_size", "provenance", "provenance.task_id", "reductions",
    "segmentation", "segments", "selector", "selector.condition",
    "selector.timestamps", "sl_mode", "strategy_params", "sweep.axis",
    "sweep.values", "take_profit", "threshold_profile", "timeframe",
    "transform", "trigger_policy", "trigger_policy.bars",
    "trigger_policy.mode", "validate_range", "window",
    # guard 家族补收五键（2026-10-14 review 发现 2——无场景专属
    # hint，走通用文案，有意豁免）
    "group_a", "group_b", "as_of", "limit", "context_bars",
})


def _assert_bidirectional(scenarios: dict, known_gaps: frozenset) -> None:
    """双向断言单点（供正常断言与红绿自证变异复用）。

    正向：场景表每键 ∈ 全工程扫描集（防悬空键——场景表引用了不
    存在的参数面）；反向：扫描集 − 场景表 ⊆ 豁免集（防漏登记——
    新增参数面未同步场景表且未显式豁免）。
    """
    scanned = _scan_param_invalid_keys()
    dangling = set(scenarios) - scanned
    assert not dangling, f"场景表悬空键（全工程无此参数面）: {sorted(dangling)}"
    unregistered = scanned - set(scenarios) - set(known_gaps)
    assert not unregistered, (
        f"扫描集存在未登记参数面（补场景表或显式加豁免集）: "
        f"{sorted(unregistered)}")


class TestParamHintScenariosParity:
    """G4-T04/T05（变更二 4.4）：场景表键集双向防漂移对拍+红绿自证。"""

    def test_bidirectional_parity(self):
        from mcpserver.errors import _PARAM_HINT_SCENARIOS
        _assert_bidirectional(_PARAM_HINT_SCENARIOS, _KNOWN_GAPS)

    def test_sizing_params_scenarios_readable(self):
        """仓位参数两键场景文案（add-position-sizing-params 5.1；
        SEC 修复补有限性措辞）：键在场（登记链抛出点经 5.2 落盘——
        双向对拍非悬空）+文案含值域/互斥/有限性要素（可读锚）。"""
        from mcpserver.errors import _PARAM_HINT_SCENARIOS

        assert "initial_fraction" in _PARAM_HINT_SCENARIOS
        assert "max_position_loss" in _PARAM_HINT_SCENARIOS
        if_hint = _PARAM_HINT_SCENARIOS["initial_fraction"]
        assert "(0, 1]" in if_hint and "互斥" in if_hint
        mpl_hint = _PARAM_HINT_SCENARIOS["max_position_loss"]
        assert "> 0" in mpl_hint and "有限数" in mpl_hint
        assert "inf" in mpl_hint  # inf 拒绝的引导要素（SEC-02）


class TestMutualExclusionThreeFaceMcp:
    """族 2d 三面对拍·MCP 参数面（add-position-sizing-params 6.3）。

    同一互斥声明（initial_fraction+risk_budget）的服务端 422 经
    tool_safe 错误通道：detail 契约透传（field=initial_fraction）+
    hint 场景分派（互斥文案可读）——validate_dna 面与登记声明块面
    见 tests/strategy 与 tests/analysis，三面共拒同一组合。
    """

    def test_422_payload_carries_field_and_mutex_hint(self):
        """422 RESEARCH_PARAM_INVALID（登记链互斥抛出形）经 tool_safe
        → {"error": {code/message/detail/hint}}：detail.field 透传+
        hint=initial_fraction 场景文案（含互斥与值域要素）。"""
        from mcpserver.errors import _PARAM_HINT_SCENARIOS

        @tool_safe
        def probe():
            raise ApiError(
                422, "RESEARCH_PARAM_INVALID",
                "initial_fraction 与 position_mode='risk_budget' 互斥"
                "（两者都只决定首笔定仓——同笔首仓不可两个指挥）",
                {"field": "initial_fraction",
                 "legal_range": "声明 initial_fraction 时 "
                                "position_mode 须为 fixed 或缺省"})

        err = probe()["error"]
        assert err["code"] == "RESEARCH_PARAM_INVALID"
        assert err["detail"]["field"] == "initial_fraction"
        assert err["hint"] == _PARAM_HINT_SCENARIOS["initial_fraction"]
        assert "互斥" in err["hint"]
        assert "initial_fraction" in err["hint"]

    def test_mpl_infinite_422_hint_guides_finiteness(self):
        """SEC-02 MCP 路径对拍：inf 声明的服务端 422（登记链有限性
        拒绝形）经 tool_safe → detail.field=max_position_loss 透传+
        hint 引导有限性（inf/超大整数非金额）。"""
        from mcpserver.errors import _PARAM_HINT_SCENARIOS

        @tool_safe
        def probe():
            raise ApiError(
                422, "RESEARCH_PARAM_INVALID",
                "max_position_loss inf 越出值域 > 0.0（金额无人为上界"
                "但须为有限数——inf/超大整数非金额）",
                {"field": "max_position_loss",
                 "legal_range": "> 0.0 (finite)"})

        err = probe()["error"]
        assert err["detail"]["field"] == "max_position_loss"
        assert err["hint"] == _PARAM_HINT_SCENARIOS["max_position_loss"]
        assert "有限数" in err["hint"] and "inf" in err["hint"]

    def test_parity_self_evidence_remove_key_red(self):
        """G4-T05 红绿自证①（隔离变异）：删场景表任一键 → 正向断言
        不变红（键仍在扫描集）但反向断言红（键落入未登记差集）——
        证明反向断言真拦得住漂移。"""
        from mcpserver.errors import _PARAM_HINT_SCENARIOS
        assert "stop_loss" in _PARAM_HINT_SCENARIOS
        with pytest.raises(AssertionError, match="stop_loss"):
            _assert_bidirectional(
                {k: v for k, v in _PARAM_HINT_SCENARIOS.items()
                 if k != "stop_loss"}, _KNOWN_GAPS)

    def test_parity_self_evidence_inject_key_red(self):
        """G4-T05 红绿自证②：注入未登记 param 键 → 正向断言红
        （悬空键——证明正向真拦得住）。"""
        from mcpserver.errors import _PARAM_HINT_SCENARIOS
        injected = {**_PARAM_HINT_SCENARIOS,
                    "no_such_param_face": "悬空键探针"}
        with pytest.raises(AssertionError, match="no_such_param_face"):
            _assert_bidirectional(injected, _KNOWN_GAPS)
