"""M2 边界契约对拍测试（improve-mcp-agent-loop 2.4 / D7）。

三项对拍进 CI，拦截跨边界形制漂移：
1) 白名单对拍：工具调用的端点 ∈ 档位白名单；
2) 分页对拍：工具传出的查询参数键 ⊆ 服务端端点签名（静默失效即缺陷）；
3) schema 对拍：工具 POST body 键 ⊆ 服务端 Request 模型字段；对象列表
   参数在工具 schema 中为对象数组（items=object）。

提取方式：AST 扫描 tools.py 源码取每个工具真实调用的端点/参数（不依赖
第二份注册表）；服务端签名经 FastAPI 路由表反射。
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Set

import pytest

from mcpserver import tools as tools_mod
from mcpserver.api_client import READONLY_ENDPOINTS, RESEARCH_POST_ENDPOINTS

pytestmark = [pytest.mark.integration]


def run_async(coro):
    """asyncio.run 的环境兼容版（同 test_server 形制——playwright e2e
    fixture 会在进程内泄漏运行中的事件循环，全量顺序下 asyncio.run
    抛 RuntimeError；检测到活动 loop 时改在新线程跑）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _norm(path: str) -> str:
    """路径归一：占位符统一为 {}（与 api_client._match 同语义，只看段数与位置）。"""
    return re.sub(r"\{[^}]+\}", "{}", path)


class ToolCall(NamedTuple):
    method: str        # GET | POST（poll_until_terminal 按 GET 计——内部走 get）
    path: str          # 模板化路径（f-string 段替换为 {param}）
    param_keys: Set[str]
    body_keys: Set[str]


def _template_of(node: ast.AST) -> str | None:
    """路径字面量/f-string → 模板路径（与白名单同形制）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(v.value)
            else:
                parts.append("{param}")
        return "".join(parts)
    return None


def _is_tool_function(fn: ast.AST) -> bool:
    return (isinstance(fn, ast.FunctionDef) and any(
        (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
         and d.func.attr == "tool")
        or (isinstance(d, ast.Attribute) and d.attr == "tool")
        for d in fn.decorator_list))


def _extract_tool_calls() -> Dict[str, List[ToolCall]]:
    """扫 tools.py AST：工具名 → 其全部 API 调用（端点/查询键/body 键）。"""
    tree = ast.parse(Path(tools_mod.__file__).read_text(encoding="utf-8"))
    out: Dict[str, List[ToolCall]] = {}
    for fn in ast.walk(tree):
        if not _is_tool_function(fn):
            continue
        # 函数级变量收集：params/body 的 Dict 字面量初始键 + 动态追加键
        var_keys: Dict[str, Set[str]] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                name = None
                if isinstance(node.targets[0], ast.Name):
                    name = node.targets[0].id
                elif (isinstance(node.targets[0], ast.Subscript)
                        and isinstance(node.targets[0].value, ast.Name)):
                    name = node.targets[0].value.id
                if name is None:
                    continue
                keys = var_keys.setdefault(name, set())
                if isinstance(node.value, ast.Dict):
                    keys.update(k.value for k in node.value.keys
                                if isinstance(k, ast.Constant))
                else:
                    # Subscript 追加（params["x"] = ...）：键在 target 上
                    t = node.targets[0]
                    if isinstance(t, ast.Subscript):
                        s = t.slice
                        if isinstance(s, ast.Constant):
                            keys.add(s.value)

        calls: List[ToolCall] = []
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "api"):
                continue
                # poll_until_terminal 内部走 self.get（GET 白名单语义）
            if node.func.attr not in ("get", "post", "poll_until_terminal"):
                continue
            method = "GET" if node.func.attr in ("get", "poll_until_terminal") else "POST"
            path = _template_of(node.args[0]) if node.args else None
            if path is None:
                continue
            param_keys: Set[str] = set()
            body_keys: Set[str] = set()
            for kw in node.keywords:
                if kw.arg == "params":
                    if isinstance(kw.value, ast.Dict):
                        param_keys.update(k.value for k in kw.value.keys
                                          if isinstance(k, ast.Constant))
                    elif isinstance(kw.value, ast.Name):
                        param_keys |= var_keys.get(kw.value.id, set())
            # post body：位置第二参或 json_body keyword，追到函数级 body 变量
            if method == "POST":
                body_node = None
                if len(node.args) >= 2:
                    body_node = node.args[1]
                for kw in node.keywords:
                    if kw.arg == "json_body":
                        body_node = kw.value
                if isinstance(body_node, ast.Dict):
                    body_keys.update(k.value for k in body_node.keys
                                     if isinstance(k, ast.Constant))
                elif isinstance(body_node, ast.Name):
                    body_keys |= var_keys.get(body_node.id, set())
            calls.append(ToolCall(method, path, param_keys, body_keys))
        if calls:
            out[fn.name] = calls
    return out


@pytest.fixture(scope="module")
def route_signatures(tmp_path_factory) -> Dict[str, Any]:
    """服务端契约表：path → {查询参数集, Request 模型字段集}（经 OpenAPI 解析，
    惰性 include 下的路由反射拿不到带前缀完整路径）。"""
    from api.app import create_app

    db = tmp_path_factory.mktemp("parity") / "quant.db"
    data_dir = tmp_path_factory.mktemp("parity_data")
    app = create_app(db_path=db, data_dir=data_dir)
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})

    def _resolve_body_fields(schema: dict) -> set:
        """requestBody schema → 顶层字段集（解一层 $ref）。"""
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            schema = components.get(name, {})
        return set(schema.get("properties", {}).keys())

    routes: Dict[str, Any] = {}
    for path, ops in spec["paths"].items():
        query_params: set = set()
        model_fields: set = set()
        for op in ops.values():
            if not isinstance(op, dict):
                continue
            for p in op.get("parameters", []):
                if p.get("in") == "query":
                    query_params.add(p["name"])
            body = op.get("requestBody", {})
            for content in body.get("content", {}).values():
                model_fields |= _resolve_body_fields(content.get("schema", {}))
        routes[_norm(path)] = {"query": query_params,
                               "model_fields": model_fields}
    return routes


class TestWhitelistParity:
    """对拍 1：工具端点 ∈ 档位白名单。"""

    def test_all_tool_endpoints_in_tier_whitelists(self) -> None:
        calls = _extract_tool_calls()
        assert calls, "AST 提取为空——tools.py 结构变更导致失效"
        for tool, entries in calls.items():
            for c in entries:
                pool = (READONLY_ENDPOINTS if c.method == "GET"
                        else RESEARCH_POST_ENDPOINTS)
                pool_norm = {_norm(p) for p in pool}
                assert _norm(c.path) in pool_norm, \
                    f"{tool} 端点 {c.path} 不在 {c.method} 白名单"


class TestSnapshotToolParity:
    """add-market-snapshot 2.2：get_market_snapshot 三处同步对拍——
    工具定义存在 + tier 归属 + 白名单字面量匹配。"""

    def test_snapshot_tool_registered_and_whitelisted(self) -> None:
        from mcpserver.tools import TOOL_TIERS

        calls = _extract_tool_calls()
        assert "get_market_snapshot" in calls, \
            "tools.py 须定义 get_market_snapshot 工具"
        # 装配后 tier 归属（TOOL_TIERS 注册时填充，直读为空集）
        from mcpserver.server import build_server
        run_async(build_server(profile="readonly")[0].list_tools())
        assert "get_market_snapshot" in TOOL_TIERS["readonly"]
        entries = calls["get_market_snapshot"]
        assert entries and all(c.method == "GET" for c in entries)
        for c in entries:
            assert _norm(c.path) == "/api/research/market-snapshot"
        assert "/api/research/market-snapshot" in READONLY_ENDPOINTS


class TestPaginationParity:
    """对拍 2：工具查询参数键 ⊆ 服务端端点签名。"""

    def test_param_keys_subset_of_endpoint_signature(self, route_signatures) -> None:
        for tool, entries in _extract_tool_calls().items():
            for c in entries:
                key = _norm(c.path)
                assert key in route_signatures, \
                    f"{tool} 端点 {c.path} 不在服务端路由表"
                endpoint_params = route_signatures[key]["query"]
                stray = c.param_keys - endpoint_params
                assert not stray, \
                    f"{tool} 传出的查询参数 {sorted(stray)} 不在端点 " \
                    f"{c.path} 签名 {sorted(endpoint_params)} 中——分页/过滤静默失效"


class TestSchemaParity:
    """对拍 3：工具 schema 与服务端模型一致。"""

    def test_post_body_keys_subset_of_request_model(self, route_signatures) -> None:
        for tool, entries in _extract_tool_calls().items():
            for c in entries:
                if c.method != "POST" or not c.body_keys:
                    continue
                model_fields = route_signatures[_norm(c.path)]["model_fields"]
                if not model_fields:
                    continue  # 端点收裸 dict（无 pydantic 模型）——无字段可对拍
                stray = c.body_keys - model_fields
                assert not stray, \
                    f"{tool} POST body 键 {sorted(stray)} 不在 Request 模型 " \
                    f"{sorted(model_fields)} 中"

    def test_object_list_params_declared_as_object_arrays(self) -> None:
        """对象列表参数必须是对象数组（schema 即文档——agent 按文档传对象一次成功）。"""
        import asyncio
        from mcpserver.server import build_server

        mcp, _ = build_server(profile="research")
        by_name = {t.name: t.input_schema.get("properties", {})
                   for t in run_async(mcp.list_tools())}
        props = by_name["register_hypothesis"]
        for param in ("when_conditions", "then_conditions", "data_ranges"):
            assert props[param]["type"] == "array", param
            assert props[param].get("items", {}).get("type") == "object", \
                f"register_hypothesis.{param} 须为对象数组（服务端为对象列表）"

    def test_new_params_schema_parity(self) -> None:
        """0.4.2 新参数对拍：strategy_params 对象+分批组键三参数对象数组
        ——与 HypothesisCreate 顶层键形制对齐（组键与 trigger_policy 同款：
        非 None 才进请求体顶层）。Optional 参数为 anyOf(t|null) 形。"""
        import asyncio

        from api.schemas import HypothesisCreate
        from mcpserver.server import build_server

        mcp, _ = build_server(profile="research")
        by_name = {t.name: t.input_schema.get("properties", {})
                   for t in run_async(mcp.list_tools())}
        props = by_name["register_hypothesis"]

        def _variants(schema: dict) -> list:
            return schema.get("anyOf") or [schema]

        # strategy_params：对象（服务端弱形制 Dict）
        assert any(v.get("type") == "object"
                   for v in _variants(props["strategy_params"]))
        # 组键三参数：对象数组（服务端 List[ConditionItem]）
        for param in ("exit_partial_conditions", "add_trigger_conditions",
                      "add_suppress_conditions"):
            is_array_of_obj = any(
                v.get("type") == "array"
                and v.get("items", {}).get("type") == "object"
                for v in _variants(props[param]))
            assert is_array_of_obj, \
                f"register_hypothesis.{param} 须为对象数组"
        # 服务端模型顶层键存在（对齐对象=HypothesisCreate 顶层键形制）
        for key in ("strategy_params", "exit_partial_conditions",
                    "add_trigger_conditions", "add_suppress_conditions"):
            assert key in HypothesisCreate.model_fields, \
                f"HypothesisCreate 缺顶层键 {key}（对拍断裂）"

    def test_register_hypothesis_docs_disclose_scaling(self) -> None:
        """工具描述披露声明块与身份关系+分批组键语义（沿 trigger_policy
        工具文档先例——错误即文档纪律）。review-042 CARD-14 补 token：
        八键键集与三键值域联动须披露（与 capability.py 八键版对齐）。"""
        import inspect

        from mcpserver import tools as tools_mod
        src = inspect.getsource(tools_mod)
        for token in ("strategy_params", "幂等四分支", "终态后带新声明重登记",
                      "exit_partial_conditions", "ADD_INHIBIT",
                      "分批条件组", "exit_fraction", "生效杠杆"):
            assert token in src, f"工具文档缺要素: {token}"


class TestSizingParamsDisclosure:
    """仓位参数两键三处披露对拍（add-position-sizing-params 5.1——
    族 8a/b：工具 schema 描述+能力自述+错误场景文案）。"""

    def test_register_hypothesis_docs_disclose_sizing_params(self) -> None:
        """工具描述（schema 面）：两键进键集列举+值域/语义/互斥+
        「回测与纸盘同语义执行」披露（沿动态定仓两键形制）。"""
        import inspect

        from mcpserver import tools as tools_mod
        src = inspect.getsource(tools_mod)
        for token in ("initial_fraction", "max_position_loss",
                      "动用资金比例", "持仓金额防线",
                      "同笔首仓不可两个指挥", "回测与纸盘同语义执行"):
            assert token in src, f"工具文档缺仓位参数要素: {token}"

    def test_capability_doc_discloses_sizing_params(self) -> None:
        """能力自述：声明块披露段含两键+值域+互斥+同语义执行
        （沿 TestCapabilityResource 形制——AI 宿主按 doc 认知参数面）。"""
        from mcpserver.server import build_server

        mcp, _ = build_server(profile="research")
        res = run_async(mcp.read_resource("quant://capabilities"))
        text = res[0].content if isinstance(res, list) else str(res)
        for token in ("initial_fraction", "max_position_loss",
                      "互斥", "回测与纸盘同语义执行"):
            assert token in text, f"能力自述缺仓位参数披露: {token}"


class TestListFolding:
    """2.2 分页真实生效 + 列表折叠默认响应 <20KB（D8）。"""

    @pytest.fixture(scope="class")
    def live_api(self, tmp_path_factory):
        import threading
        import time as _time

        import numpy as np
        import pandas as pd
        import uvicorn

        from api.app import create_app

        db = tmp_path_factory.mktemp("fold") / "quant.db"
        data_dir = tmp_path_factory.mktemp("fold_data")
        app = create_app(db_path=db, data_dir=data_dir)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = _time.monotonic() + 15
        while _time.monotonic() < deadline:
            if server.started:
                break
            _time.sleep(0.1)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}", db
        server.should_exit = True
        thread.join(timeout=5)

    def test_pagination_effective_and_folding_small(self, live_api) -> None:
        """分页参数真实生效（不静默失效）+ 折叠后默认响应 <20KB。"""
        import asyncio
        import json

        from core.persistence.repository.strategy import save_strategy
        from core.persistence.db_ext import init_db_ext
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA)
        from mcpserver.server import build_server

        base, db = live_api
        init_db_ext(db)
        for i in range(20):
            # 每条参数不同：同基因签名会被 save_strategy 去重吞掉（D2 已裁决的坑）
            dna = StrategyDNA(
                signal_genes=[SignalGene("RSI", {"period": 10 + i},
                                         SignalRole.ENTRY_TRIGGER,
                                         f"rsi_{10 + i}",
                                         {"type": "lt", "threshold": 35})],
                logic_genes=LogicGenes(),
                execution_genes=ExecutionGenes(timeframe="4h"),
                risk_genes=RiskGenes())
            save_strategy(db, strategy_id=f"st_fold_{i}",
                          dna_json=dna.to_json(), symbol="BTCUSDT",
                          timeframe="4h", name=f"fold_{i}")

        mcp, api = build_server(profile="research", base_url=base)

        # 分页真实生效：limit=5 → REST 返回 5 条（参数不静默失效）
        direct = api.get("/api/strategies", params={"limit": 5, "offset": 0})
        assert len(direct["items"]) == 5

        # 工具经 call_tool 走全链路：折叠后条目无 dna、有 gene_signature
        result = run_async(mcp.call_tool("list_strategies",
                                         {"limit": 20, "offset": 0}))
        payload = json.loads(result.content[0].text) if hasattr(
            result.content[0], "text") else result
        assert payload["total"] == 20
        assert len(payload["items"]) == 20
        for item in payload["items"]:
            assert "dna" not in item
            assert item["gene_signature"]

        # 默认响应体积受控（<20KB）；未折叠的 REST 响应显著更大
        folded_size = len(json.dumps(payload, ensure_ascii=False).encode())
        raw_size = len(json.dumps(direct, ensure_ascii=False).encode())
        assert folded_size < 20 * 1024, f"折叠后默认响应 {folded_size}B 超 20KB"


class TestDisclosureParity:
    """0.3.3 G4/G5 披露对拍：mcpserver 文本层与 core 权威源同口径。

    生产代码经 REST 分层不跨层 import——一致性由本锚强制（文本含
    WARMUP_RULE_TEXT 关键口径子串；阈值数值 == api 侧常量；旧口径清零）。
    工具经 @mcp.tool() 闭包注册、模块级无函数属性——doc 断言走源码文本
    （同本文件 AST 扫描的取材纪律）。
    """

    _TOOLS_SRC = Path(tools_mod.__file__).read_text(encoding="utf-8")

    @staticmethod
    def _squash(text: str) -> str:
        """空白归一（docstring 换行不切断口径子串）。"""
        return "".join(text.split())

    def test_list_indicators_doc_carries_rule_essentials(self):
        from core.analysis.registry_meta import WARMUP_RULE_TEXT
        src = self._squash(self._TOOLS_SRC)
        for key in ("周期类参数注册表上限之和", "条件实际参数×3,20",
                    "plugin_formula_lookback"):
            assert self._squash(key) in src, f"list_indicators 描述缺口径要素: {key}"
            assert self._squash(key) in self._squash(WARMUP_RULE_TEXT)  # 要素确属权威文本

    def test_capability_warmup_rule_faithful(self):
        from mcpserver.capability import CAPABILITY_DOC
        for key in ("周期类参数注册表上限之和", "plugin_formula_lookback",
                    "条件实际参数 × 3, 20"):
            assert key in CAPABILITY_DOC, f"能力自述缺口径要素: {key}"
        # 旧口径清零（capability 曾写「最大周期×3」——Q3 裁决禁旧口径）
        assert "最大周期×3" not in CAPABILITY_DOC
        assert "max_indicator_period" not in CAPABILITY_DOC

    def test_run_analysis_doc_heavy_threshold_matches_constant(self):
        import re
        from api.routes.research.analysis import _HEAVY_ROWS
        m = re.search(r"超过 (\d+)", self._TOOLS_SRC)
        assert m is not None, "run_analysis 描述未披露同步/异步阈值"
        assert int(m.group(1)) == _HEAVY_ROWS

    def test_warmup_basis_uses_authoritative_text(self):
        from core.analysis import validation_service as vs
        src = Path(vs.__file__).read_text(encoding="utf-8")
        assert "max_indicator_period" not in src, "basis 旧口径残留"
        assert src.count("WARMUP_RULE_TEXT") >= 2  # import + basis 落用

    def test_high_tf_conversion_elements_across_three_faces(self):
        """CARD-03 锚：高周期折算三要素（折算语义 / 42 bar=42 天具体例 /
        下限分属两式）三披露面齐备一致——CAPABILITY_DOC 与 tools docstring
        折算句跨行，原文 in 检查会被换行切断，须 squash 后断言。"""
        from core.analysis.registry_meta import WARMUP_RULE_TEXT
        from mcpserver.capability import CAPABILITY_DOC
        faces = {
            "WARMUP_RULE_TEXT": self._squash(WARMUP_RULE_TEXT),
            "CAPABILITY_DOC": self._squash(CAPABILITY_DOC),
            "tools 源码": self._squash(self._TOOLS_SRC),
        }
        for face_name, face in faces.items():
            for key in ("折算为前扩时长", "42bar=42天", "分属两式"):
                assert key in face, f"{face_name} 缺高周期折算要素: {key}"


class TestTriggerPolicyDisclosure:
    """事件策略层披露面（trigger-policy tasks 7.1/7.2）。

    三披露面（run_analysis/register_hypothesis 工具描述 + 能力自述）
    MUST 携带三值枚举语义与「整理触发不创造触发」边界——工具经
    @mcp.tool() 闭包注册无模块级属性，doc 断言走源码文本（同
    TestDisclosureParity 取材纪律）；工具签名增 trigger_policy 后
    schema 对拍（body 键 ⊆ Request 模型字段）由既有 TestSchemaParity
    承载，本类跑通即对拍同步成立的证据。
    """

    _TOOLS_SRC = Path(tools_mod.__file__).read_text(encoding="utf-8")

    @staticmethod
    def _squash(text: str) -> str:
        return "".join(text.split())

    def test_register_hypothesis_doc_三枚举与边界(self) -> None:
        src = self._squash(self._TOOLS_SRC)
        for token in ('"none"|"first_true"|"suppress_bars"',
                      "不创造触发", "幂等身份"):
            assert token in src, f"register_hypothesis 描述缺要素: {token}"

    def test_run_analysis_doc_事件口径与分形态(self) -> None:
        src = self._squash(self._TOOLS_SRC)
        for token in ("first_true", "suppress_bars", "event_density",
                      "不创造触发", "逐字节不变"):
            assert token in src, f"run_analysis 描述缺要素: {token}"

    def test_capability_doc_事件策略层条目(self) -> None:
        from mcpserver.capability import CAPABILITY_DOC
        doc = self._squash(CAPABILITY_DOC)
        for token in ("first_true", "suppress_bars", "同一权威变换",
                      "不静默降级", "不创造触发"):
            assert token in doc, f"能力自述缺要素: {token}"

    def test_register_hypothesis_签名含_trigger_policy(self) -> None:
        import re
        m = re.search(
            r"def register_hypothesis\((?:[^)]*\n)*?[^)]*"
            r"trigger_policy: Optional\[dict\[str, Any\]\] = None",
            self._TOOLS_SRC)
        assert m is not None, "register_hypothesis 签名未增 trigger_policy"


class TestIndicatorCountAlignment:
    """S3a/S3b：指标计数四处声明对齐（unify-tool-return-contract 任务 6）。

    双保险：相对断言（四处声明文本含 str(len(INDICATOR_REGISTRY))）
    + 绝对快照（len()==45——包三 add-session-structure-expression 三
    新指标入库后翻转 48，届时同步四处声明）。
    """

    def test_s3b_absolute_snapshot_45(self) -> None:
        """绝对快照：注册表实测 45（与声明锁死；新指标入库先翻此断言）。"""
        from core.features.registry import INDICATOR_REGISTRY
        assert len(INDICATOR_REGISTRY) == 48, (
            "注册表基数漂移——包三三指标已入库（48）；后续增减须同步四处声明"
            "与本快照")

    def test_s3a_four_declarations_carry_runtime_count(self) -> None:
        """相对断言：四处声明文本携带运行时真实计数（漂移即红）。"""
        from core.features.registry import INDICATOR_REGISTRY
        count = str(len(INDICATOR_REGISTRY))
        sources = {
            "core/analysis/registry_meta.py":
                Path("core/analysis/registry_meta.py").read_text("utf-8"),
            "api/routes/research/registry.py":
                Path("api/routes/research/registry.py").read_text("utf-8"),
            "mcpserver/tools.py":
                Path("mcpserver/tools.py").read_text("utf-8"),
            "core/features/registry.py":
                Path("core/features/registry.py").read_text("utf-8"),
        }
        for path, src in sources.items():
            # docstring/注释声明位（45 项 / 45 indicators / 45 指标）。
            # fix-mtf-review-consistency S3a 词形修复：CARD-02 计数同步
            # +CARD-09 翻译后 registry.py 声明词形为「48 指标定义/注册表」
            # ——token 集补「{N} 指标」形（计数断言语义不变）
            assert f"内置 {count}" in src or f"{count} 项" in src \
                or f"({count} indicators" in src \
                or f"{count} 指标" in src, \
                f"{path} 计数声明未携带运行时计数 {count}"


class TestMultiTimeframeBoundaryDisclosure:
    """任务 6：工具描述多周期边界三句（unblock-research-validation）。"""

    def test_translate_and_register_docs_carry_boundary(self) -> None:
        src = Path("mcpserver/tools.py").read_text("utf-8")
        for token in ("when 与",
                      "加仓触发组",
                      "不低于策略" ,"不低于", "基准周期",
                      "拒绝"):
            assert token in src, f"描述缺边界要素: {token}"


class TestTranslateTimeframeContractDisclosure:
    """R2 转化执行周期契约披露锚（fix-mtf-execution-consistency 组 2）。

    spec delta「转化执行周期契约」明文 MCP docstring 同步披露——
    docstring 未披露等于 AI 消费者视角的能力不存在（三面一致纪律）。
    锁 translate 工具 docstring 执行周期契约句四要素：缺省继承语义、
    继承来源（data_ranges[0].timeframe）、A1 异周期拒绝指引、回执
    execution_timeframe 键。服务层行为锚在 tests/analysis/
    test_translate_timeframe_contract.py（R2 六用例），本锚为 MCP
    披露面（R2 锚族的 tests/mcpserver/ 面）。
    """

    def test_translate_doc_carries_timeframe_contract(self) -> None:
        src = Path("mcpserver/tools.py").read_text("utf-8")
        for token in ("继承假设基准周期",
                      "data_ranges[0].timeframe",
                      "存量异周期假设拒绝",
                      "execution_timeframe"):
            assert token in src, f"translate 披露缺执行周期契约要素: {token}"


class TestRegimeSplitConditionsDisclosure:
    """regime_split 组合条件披露对拍（regime-split-combined-conditions）。

    功能面 cd3f1a9 已落地（模板/路由/spec/测试四层），本锚守护声明面
    三处不回退：run_analysis conditions 总述句清单、regime_split 模板
    段落、能力自述组合条件条目——docstring 未披露等于 AI 消费者视角的
    能力不存在（fr_5eb0c37ab1b0 误用病灶面）。A4 防回退：既有
    counter_profile/exit_sweep 的 condition|conditions 披露不被误伤。
    工具经 @mcp.tool() 闭包注册无模块级属性，doc 断言走源码文本
    （同 TestDisclosureParity 取材纪律）。
    B1 支持面互拍：主条款「与路由键组实际支持面一致——描述面能力
    清单漂移即缺陷」的类别级防线（A1-A3 只钉 regime_split 成员在场，
    清单多写/漏写其他成员不红——B1 补此洞）。
    A5 插件列条目锚（CARD-02）：capability 插件列条目清单含
    regime_split（spec「插件列条件可用」Scenario 的披露面），清单
    串内命中形制——新标题自带 regime_split 括注（CARD-01 同类），
    段内子串命中不构成成员证据。
    """

    _PLUGINS_ENTRY_SEG = ("分析模板插件列条件（fix-research-c3起；"
                          "regime_split2026-10-14起）", "纯公式进场形制")

    _TOOLS_SRC = Path(tools_mod.__file__).read_text(encoding="utf-8")

    @staticmethod
    def _squash(text: str) -> str:
        return "".join(text.split())

    @staticmethod
    def _segment(squashed: str, start: str, end: str) -> str:
        """squash 域内截段：[start 锚起点, end 锚起点)，两锚均须在场。"""
        i = squashed.find(start)
        assert i != -1, f"截段起点锚不在场: {start}"
        j = squashed.find(end, i)
        assert j != -1, f"截段终点锚不在场: {end}"
        return squashed[i:j]

    @staticmethod
    def _conditions_support_list(squashed: str) -> set:
        """conditions 支持清单提取：斜杠清单串 → 成员集（B1/A5 共用）。"""
        mo = re.search(
            r"condition_stats/event_study(?:/\w+)*支持conditions", squashed)
        assert mo is not None, "conditions 支持清单锚不在场"
        return set(
            mo.group(0).removesuffix("支持conditions").split("/"))

    def test_a1_overview_list_carries_regime_split(self) -> None:
        src = self._squash(self._TOOLS_SRC)
        m = re.search(r"condition_stats/event_study(?:/\w+)*支持conditions",
                      src)
        assert m is not None, "conditions 总述句清单锚漂移"
        assert "/regime_split" in m.group(0), (
            "run_analysis conditions 总述句清单未含 regime_split"
            "（斜杠连写形制，与竖线模板枚举串可区分）")

    def test_a2_regime_split_segment_tokens(self) -> None:
        m = re.search(
            r"regime_split 行情分段.*?(?=precheck_hypothesis 假设预检)",
            self._TOOLS_SRC, re.S)
        assert m is not None, "regime_split 段落截段锚漂移"
        seg = self._squash(m.group(0))
        for token in ("conditions", "二选一", "互斥", "condition|conditions",
                      "1..5", "AND",
                      "timeframe", "并集", "插件列",
                      "params.indicator+params.condition"):
            assert token in seg, f"regime_split 段落缺披露要素: {token}"

    def test_a3_capability_combo_entry_carries_regime_split(self) -> None:
        from mcpserver.capability import CAPABILITY_DOC
        doc = self._squash(CAPABILITY_DOC)
        seg = self._segment(
            doc, "组合条件（0.4.1起；regime_split2026-10-14起）",
            "区间事件摘要")
        # 清单成员断言（非段内子串）：CARD-01 后条目标题自带
        # regime_split 括注，段内任意子串命中不构成成员在场证据——
        # 须命中斜杠清单串本身（与 A1/B1 提取形制一致）
        mo = re.search(
            r"condition_stats/event_study(?:/\w+)*支持conditions", seg)
        assert mo is not None, "能力自述组合条件条目缺 conditions 支持清单"
        assert "/regime_split" in mo.group(0), (
            "能力自述组合条件条目清单未含 regime_split")

    def test_a4_existing_counter_exit_disclosure_intact(self) -> None:
        src = self._squash(self._TOOLS_SRC)
        for name, start, end in (
            ("counter_profile", "counter_profile反例画像",
             "exit_sweep退出参数扫描"),
            ("exit_sweep", "exit_sweep退出参数扫描", "走202+轮询"),
        ):
            seg = self._segment(src, start, end)
            assert "condition|conditions" in seg, (
                f"{name} 段落既有 condition|conditions 披露回退")

    def test_b1_support_faces_consistent_with_route(self) -> None:
        """conditions 支持面互拍：路由实际支持面与三披露面清单一致。

        主条款「与路由键组实际支持面一致——描述面能力清单漂移即
        缺陷」的直接锚（fr_5eb0c37ab1b0 病灶的类别级防线）：新增
        模板支持 conditions 而忘披露、或描述面多写/漏写成员均红。
        路由面 = 键组表含 conditions 的模板 ∪ regime_split（无必填
        键不进键组表——分支内 conditions 分叉在场断言，不写死集合）；
        描述面 = 总述句清单 ∪ 段落披露面（counter_profile/exit_sweep
        的 condition|conditions 段落形制，A4 守护在场）；能力自述面
        = 组合条件条目清单（与总述句同层互拍）。
        """
        from api.routes.research import analysis as analysis_mod
        table = analysis_mod._TEMPLATE_REQUIRED_KEYS
        face_route = {t for t, groups in table.items()
                      if any("conditions" in g for g in groups)}
        analysis_src = Path(analysis_mod.__file__).read_text(encoding="utf-8")
        m = re.search(
            r'template == "regime_split":.*'
            r'(?=template == "segment_narrative")',
            analysis_src, re.S)
        assert m is not None, "regime_split 路由分支截段锚漂移"
        assert 'p.get("conditions")' in m.group(0), (
            "regime_split 路由分支缺 conditions 分叉——支持面提取失真")
        face_route.add("regime_split")

        src = self._squash(self._TOOLS_SRC)
        face_overview = self._conditions_support_list(src)
        face_segment = {
            name for name, start, end in (
                ("counter_profile", "counter_profile反例画像",
                 "exit_sweep退出参数扫描"),
                ("exit_sweep", "exit_sweep退出参数扫描", "走202+轮询"),
            )
            if "condition|conditions" in self._segment(src, start, end)}
        face_doc = face_overview | face_segment

        from mcpserver.capability import CAPABILITY_DOC
        doc = self._squash(CAPABILITY_DOC)
        face_capability = self._conditions_support_list(
            self._segment(
                doc, "组合条件（0.4.1起；regime_split2026-10-14起）",
                "区间事件摘要"))

        assert face_capability == face_overview, (
            "conditions 支持清单两声明面漂移（总述句 vs 能力自述条目）: "
            f"{sorted(face_overview)} vs {sorted(face_capability)}")
        assert face_doc == face_route, (
            "描述面 conditions 支持清单与路由实际支持面漂移: "
            f"{sorted(face_doc)} vs {sorted(face_route)}")

    def test_a5_plugin_column_entry_carries_regime_split(self) -> None:
        """capability 插件列条目清单含 regime_split（CARD-02）。

        spec「插件列条件可用」Scenario（conditions 含公式插件列
        subject）的披露面锚——行为面由 P2 组 test_plugin_column_
        conditions_route_nonzero 守护，本锚钉能力自述清单成员。
        两断言：①conditions 支持面（B1 face_overview）⊆ 插件列
        清单——凡支持 conditions 的模板其条件 subject 插件列披露
        须一致（spec 混合形制条款「结构化/基础列/插件列 subject
        均可」）；②fix-research-c3 时代既有五成员不回退。清单串
        内命中形制（标题自带 regime_split 括注——CARD-01 同类，
        段内子串命中不构成成员证据）。不与路由装载链互拍：
        event_study 旧形走普通装载、inspect_cases 走 cases 端点，
        装载链代理指标不精确，互拍产假漂移。
        """
        from mcpserver.capability import CAPABILITY_DOC
        doc = self._squash(CAPABILITY_DOC)
        seg = self._segment(doc, *self._PLUGINS_ENTRY_SEG)
        mo = re.search(r"([\w]+(?:/\w+)*)的条件引用插件列", seg)
        assert mo is not None, "插件列条目缺清单串（披露形制漂移）"
        face_plugins = set(mo.group(1).split("/"))

        missing = self._conditions_support_list(
            self._squash(self._TOOLS_SRC)) - face_plugins
        assert not missing, (
            "conditions 支持面未全部披露插件列条件（混合形制条款——"
            f"插件列 subject 均可）: 缺 {sorted(missing)}")
        for tpl in ("condition_stats", "event_study", "inspect_cases",
                    "param_sweep", "counter_profile"):
            assert tpl in face_plugins, f"插件列条目既有成员回退: {tpl}"
