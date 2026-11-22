"""T5：前后端四面对账（fix-replay-gap-alignment——endpoint-parity-audit
能力锚，New capability）。

四面（proposal 2.5/2.6）：
- T5-a 前端调用面 ⊆ 路由面（openapi.json 再生成为权威基准源）；
- T5-b 路由面 ⊆ 前端实调 ∪ MCP 消费 ∪ RETAINED_ENDPOINTS（登记面
  直挂 tests/api/_endpoint_registry.py）；
- T5-c 护栏排除项（/api/live 前缀引用+generated/ 不算消费——
  退役域历史引用不误报）；
- T5-e 6 孤儿删除落地负向断言+union-balance 薄壳契约。

红基线：E5/E6 抽样实证；预期红=前端死调用 25 对象（26−1：union-
balance 经薄壳恢复变活调用——裁决③主案 a）——死调用清理
（frontend U1-U10）完成后转绿。前端侧对账镜像（vitest）落位
web/src/test/alignment/endpoint-parity.test.ts（含 T5-WS），与本
pytest 侧互为独立证据。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from tests.api._endpoint_registry import RETAINED_ENDPOINTS

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WEB_SRC = _PROJECT_ROOT / "web" / "src"
_OPENAPI = _PROJECT_ROOT / "web" / "openapi.json"
_TOOLS = _PROJECT_ROOT / "mcpserver" / "tools.py"

#: 护栏排除项（T5-c，tester/frontend-tester 3.3 权威口径）：①前端面
#: 排除 src/test/** 与 *.test.*（工程红线：测试零外网）②排除
#: src/types/generated/**（生成物非实调）③MCP 消费面遇 /api/live
#: 前缀引用不误报（需求 2.8 护栏条款在 mcpserver 侧——退役域历史
#: 引用）④__fixtures__ 数据面。四项外无前缀护栏——前端死调用
#: 全量计入 T5-a 差集（预期红基线 25 对象=F0 口径）。
_GUARDED_GENERATED = ("/generated/",)
_MCP_GUARD_PREFIXES = ("/api/live",)


def _norm(path: str) -> str:
    """路径模板段归一：${expr}/{name} 两形制 → {}（两侧同源）。"""
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    return re.sub(r"\{[^}]*\}", "{}", path)


def _route_surface() -> set:
    """路由面：openapi.json（再生成权威基准源）方法级全集。"""
    spec = json.loads(_OPENAPI.read_text())
    out = set()
    for p, methods in spec["paths"].items():
        for m in methods:
            if m in ("get", "post", "put", "patch", "delete"):
                out.add(f"{m.upper()} {_norm(p)}")
    return out


# 前端调用提取：api.*（含泛型尖括号形制 api.get<T>(...)+模板字面量）
# 与 streamSse（记 POST——SSE 消费也是路由消费）两形态
_FRONT_CALL_RE = re.compile(
    r'(?:api|apiFetch)\.(get|post|put|patch|delete)(?:<[^<>]*>)?\(\s*'
    r'([`"\'])(/api/[^`"\']*)\2')
_FRONT_SSE_RE = re.compile(r'streamSse\(\s*[`"\'](/api/[^`"\']*)[`"\']')


def _frontend_surface() -> set:
    """前端实调面：web/src（排除 generated/与 test 域）api.*+streamSse。"""
    out = set()
    for f in _WEB_SRC.rglob("*.ts*"):
        s = str(f)
        if any(g in s for g in _GUARDED_GENERATED):
            continue
        if "/test/" in s or ".test." in s or "__fixtures__" in s:
            continue
        text = f.read_text()
        for m, _, p in _FRONT_CALL_RE.findall(text):
            out.add(f"{m.upper()} {_norm(p)}")
        for p in _FRONT_SSE_RE.findall(text):
            out.add(f"POST {_norm(p)}")
    return out


def _mcp_surface() -> set:
    """MCP 消费面：mcpserver/tools.py api.* 字面量+模板 f-string 归一。

    /api/live 前缀引用（退役域历史护栏条款）不进消费面。"""
    tools = _TOOLS.read_text()
    out = set()
    for m, p in re.findall(
            r'api\.(get|post|put|patch|delete)\("([^"]+)"', tools):
        if p.startswith(_MCP_GUARD_PREFIXES):
            continue
        out.add(f"{m.upper()} {_norm(p)}")
    for m, p in re.findall(
            r'api\.(get|post|put|patch|delete)\(\s*f"([^"]+)"', tools, re.S):
        if p.startswith(_MCP_GUARD_PREFIXES):
            continue
        out.add(f"{m.upper()} {_norm(p)}")
    return out


def _registry_surface() -> set:
    """登记保留面：RETAINED_ENDPOINTS 键集（同源规范化）。"""
    return {_norm(e["endpoint"]) for e in RETAINED_ENDPOINTS}


class TestT5aFrontendCallsCovered:
    """T5-a：前端调用 ⊆ 路由面（每条调用都有后端路由在）。

    挂载时点（F0）预期红：差集=25 对象死调用（26−1：union-balance
    经薄壳恢复变活调用——裁决③主案 a；E5 抽样+本断言即天然反例集
    ——现存死调用本身提供判别力自证）。死调用清理（2.6 U1-U10）
    完成后差集清零转绿（F6 口径）。"""

    def test_frontend_calls_subset_of_routes(self):
        routes = _route_surface()
        front = _frontend_surface()
        dead = sorted(front - routes)
        assert not dead, (
            f"前端死调用（调用面∉路由面）{len(dead)} 条: {dead}"
            f"——U1-U10 清理面未尽或新引入死调用")


class TestT5bRoutesConsumedOrRetained:
    """T5-b：路由面 ⊆ 前端实调 ∪ MCP 消费 ∪ 登记保留（无灰色端点）。"""

    def test_routes_subset_of_consumers_union_registry(self):
        routes = _route_surface()
        consumers = _frontend_surface() | _mcp_surface()
        retained = _registry_surface()
        grey = sorted(routes - consumers - retained)
        assert not grey, (
            f"灰色端点（无消费方且未登记保留）{len(grey)} 条: {grey}"
            f"——登记进 RETAINED_ENDPOINTS 或随下轮清理重评估")

    def test_registry_mcp_entries_double_anchored(self):
        """mcp-consumed 型条目双向锚：登记端点确在 MCP 消费面（indicators
        即实证样本——登记与消费两侧互锁，防登记面漂移成孤儿）。"""
        mcp = _mcp_surface()
        for e in RETAINED_ENDPOINTS:
            if e["retention_type"] != "mcp-consumed":
                continue
            assert _norm(e["endpoint"]) in mcp, (
                f"mcp-consumed 登记条目不在 MCP 消费面（漂移）: {e}")

    def test_registry_form_discipline(self):
        """登记表形制纪律：枚举域/键同源规范化/pending-handover deadline
        细则（非 deliberate 型空 deadline 红；deadline 过期红；retention_
        type 超枚举红）。"""
        legal = {"mcp-consumed", "pending-handover", "deliberate"}
        keys = set()
        for e in RETAINED_ENDPOINTS:
            assert e["retention_type"] in legal, (
                f"retention_type 超枚举: {e}")
            key = _norm(e["endpoint"])
            assert key not in keys, f"登记键重复: {key}"
            keys.add(key)
            if e["retention_type"] == "pending-handover":
                assert e.get("deadline"), (
                    f"pending-handover 型 deadline 必填: {e}")
                assert e["deadline"] >= "2026-11-21", (
                    f"deadline 已过期（承接逾期未销项）: {e}")
            else:
                # mcp-consumed 不校验 deadline；deliberate 可空——
                # 非 deliberate 型（即 mcp-consumed）带 deadline 属
                # 形制漂移（该型无时限语义）
                if e["retention_type"] == "mcp-consumed":
                    assert not e.get("deadline"), (
                        f"mcp-consumed 型无 deadline 语义: {e}")


class TestT5cGuardRails:
    """T5-c：护栏四项显式在案（test 域/generated//MCP /api/live 前缀/
    fixtures）——排除规则本身不入清理断言面。"""

    def test_guard_prefixes_declared(self):
        # 排除规则在案：MCP 面 /api/live 前缀（退役域历史护栏条款）
        assert _MCP_GUARD_PREFIXES == ("/api/live",)
        assert _GUARDED_GENERATED == ("/generated/",)

    def test_generated_not_counted_as_consumption(self):
        """generated/ 类型层不进消费面（生成物引用非实调）。"""
        front = _frontend_surface()
        assert not any(
            "/generated/" in k for k in front), "生成物路径不入集"


class TestT5eOrphansRemovedUnionBalanceRestored:
    """T5-e：6 孤儿不在场逐条点名+union-balance 薄壳契约（裁决③）。"""

    ORPHANS = [
        "GET /api/strategies/{}/transitions",
        "POST /api/strategies/{}/revalidate",
        "POST /api/strategies/batch",
        "GET /api/workbench/fleet-summary",
        "GET /api/workbench/process-status",
        "GET /api/workbench/hypothesis-union-remains",
    ]

    def test_six_orphans_absent_from_routes(self):
        routes = _route_surface()
        present = [o for o in self.ORPHANS if o in routes]
        assert not present, f"孤儿端点仍在路由面: {present}"

    def test_union_balance_present_three_param_contract(self):
        """union-balance 在场且 REST 契约为三参单假设精确查询（共同
        决定 3 语义——与无参全量聚合的 remains 不兼容）。"""
        routes = _route_surface()
        assert "GET /api/research/union-balance" in routes
        spec = json.loads(_OPENAPI.read_text())
        op = spec["paths"]["/api/research/union-balance"]["get"]
        params = op.get("parameters", [])
        names = [p["name"] for p in params]
        assert names == ["symbol", "timeframe", "hypothesis_id"], (
            f"三参必填契约（实测参数面 {names}）")
        assert all(p.get("required") for p in params), "三参均必填"


class TestT5BaselineEvidence:
    """红基线记录面：本文件挂载时点的对账快照留档（F0 预期红差集
    =25 对象；供 3.4 全绿收口对照——前红后绿证据链。快照用例自身
    恒绿，只打印不判定——判据在 T5-a 主断言）。"""

    def test_current_diff_snapshot(self, capsys):
        routes = _route_surface()
        front = _frontend_surface()
        dead = sorted(front - routes)
        with capsys.disabled():
            print(f"\n[T5 F0 快照] 路由 {len(routes)}｜前端调用 "
                  f"{len(front)}｜死调用差集 {len(dead)}（预期 25）")
            for d in dead:
                print(f"  {d}")
