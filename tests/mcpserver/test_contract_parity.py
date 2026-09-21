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

        from core.persistence.db_ext import init_db_ext, save_strategy
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
