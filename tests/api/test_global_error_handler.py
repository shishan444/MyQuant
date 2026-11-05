"""ResearchError 全局兜底处理器测试（add-research-error-global-handler）。

A 组锚定 api-error-contract「ResearchError 出站全局兜底」requirement
的身份与状态/数据契约：A1 探针冒泡（期望独立手写，不经
research_error_to_http 生成，防同源假绿）、A2 状态透传四态参数化
（硬编码 422 的实现在 404/409/500 三参数上必红）、A3 detail=None
缺失值键在场、A4 模块尾单例注册直证。B1/C3/D1 组随对应任务落位
同文件（探针对拍/宽捕获优先级/防回潮静态锚）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.errors import ResearchError


def test_a1_uncaught_research_error_bubbles_to_structured_response(api_client):
    """A1 探针路由冒泡：无捕获 ResearchError → 422 + detail 三段具体值。"""
    @api_client.app.get("/__probe/a1")
    def probe_a1():
        raise ResearchError(
            code="PROBE_BUBBLE_CODE",
            message="探针消息-冒泡",
            detail={"k": "v"},
        )

    resp = api_client.get("/__probe/a1")

    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["code"] == "PROBE_BUBBLE_CODE"
    assert body["message"] == "探针消息-冒泡"
    assert body["detail"] == {"k": "v"}


@pytest.mark.parametrize("status", [422, 404, 409, 500])
def test_a2_status_passthrough(api_client, status):
    """A2 状态透传：响应状态码逐态等于异常构造值（禁硬编码出站状态）。"""
    @api_client.app.get("/__probe/a2")
    def probe_a2():
        raise ResearchError(code="PROBE_STATUS", message="状态透传探针", status=status)

    resp = api_client.get("/__probe/a2")

    assert resp.status_code == status


def test_a3_detail_none_keeps_null_key(api_client):
    """A3 缺失值：detail=None → 响应体 detail 键在场且值为 null。"""
    @api_client.app.get("/__probe/a3")
    def probe_a3():
        raise ResearchError(code="PROBE_NO_DETAIL", message="无明细探针")

    resp = api_client.get("/__probe/a3")

    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert "detail" in body
    assert body["detail"] is None


def test_a4_singleton_handler_registered():
    """A4 单例形态直证：模块尾 _fastapi_app 注册了 ResearchError handler。"""
    from api.app import _fastapi_app

    assert ResearchError in _fastapi_app.exception_handlers


# ---------------------------------------------------------------------------
# D 组·静态对账锚（防回潮，无 IO；沿 TD-02 锚先例形制）
# ---------------------------------------------------------------------------

_API_ROOT = Path(__file__).resolve().parents[2] / "api"

# except ResearchError 豁免清单（带副作用的显式捕获）——新增须显式
# 更新本清单并在锚消息注明理由；纯转换捕获一律冒泡走全局兜底
_EXPECTED_CATCHES = {
    # rejected 审计块：先落审计记录再转结构化拒绝（业务契约）
    "api/routes/research/plugins.py": ("except ResearchError as exc:",),
    # 宽窄双通道窄通道：bare re-raise 防吞（删则 ResearchError 掉进
    # 宽通道被转 RESEARCH_TEMPLATE_INTERNAL 500）
    "api/routes/research/analysis.py": ("except ResearchError:",),
}

# research_error_to_http 消费点（文件 + 语句语义）：hypothesis_cards 为
# 主动构造本地转换（非捕获、design §3 范围外不动项）
_EXPECTED_CONSUMERS = {
    "api/app.py": ("import", "call"),  # 模块级 import + 全局 handler 调用
    "api/routes/research/common.py": ("def",),  # 唯一定义
    "api/routes/research/plugins.py": ("import", "call"),  # 模块级 import + 保留块调用
    "api/routes/research/hypothesis_cards.py": ("import", "call"),  # 函数内 import + 主动构造转换
}


def _scan_api_domain() -> tuple[dict, dict]:
    """api 域静态扫描（ast 语义级，免疫注释/docstring 干扰）。

    Returns:
        (catches, consumers)——catches: 文件 → except 语句原文元组；
        consumers: 文件 → 语句语义元组（import/def/call，按行号序）。
    """
    catches: dict[str, tuple] = {}
    consumers: dict[str, tuple] = {}
    for py in sorted(_API_ROOT.rglob("*.py")):
        rel = f"api/{py.relative_to(_API_ROOT)}"
        text = py.read_text(encoding="utf-8")
        lines = text.splitlines()
        tree = ast.parse(text)
        catch_stmts = []
        sem_rows = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.ExceptHandler)
                    and isinstance(node.type, ast.Name)
                    and node.type.id in ("ResearchError", "_ResearchError")):
                catch_stmts.append((node.lineno, lines[node.lineno - 1].strip()))
            elif (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "research_error_to_http"):
                sem_rows.append((node.lineno, "def"))
            elif (isinstance(node, ast.ImportFrom)
                    and any(a.name == "research_error_to_http" for a in node.names)):
                sem_rows.append((node.lineno, "import"))
            elif (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "research_error_to_http"):
                sem_rows.append((node.lineno, "call"))
        if catch_stmts:
            catches[rel] = tuple(s for _, s in sorted(catch_stmts))
        if sem_rows:
            consumers[rel] = tuple(s for _, s in sorted(sem_rows))
    return catches, consumers


def test_d1_catch_sites_frozen():
    """D1 锚一：except ResearchError 点位与豁免清单精确相等。

    纪律：未来合法新增带副作用的 ResearchError 捕获（审计/降级等）
    须显式更新 _EXPECTED_CATCHES；纯转换捕获一律冒泡走应用层全局
    兜底（api/app.py），不得新增本地转换点。
    """
    catches, _ = _scan_api_domain()
    assert catches == dict(_EXPECTED_CATCHES), (
        "except ResearchError 点位漂移——豁免清单须显式更新并注明理由：\n"
        f"实际: {catches}\n预期: {dict(_EXPECTED_CATCHES)}"
    )


def test_d1_converter_consumers_frozen():
    """D1 锚二：research_error_to_http 消费点与预期精确相等。

    纪律：转换函数消费面收敛为四文件七处（app 全局兜底 / common
    定义 / plugins 保留块 / hypothesis_cards 主动构造）；新增消费点
    须显式更新 _EXPECTED_CONSUMERS 并注明语义。
    """
    _, consumers = _scan_api_domain()
    assert consumers == dict(_EXPECTED_CONSUMERS), (
        "research_error_to_http 消费点漂移——须显式更新预期清单：\n"
        f"实际: {consumers}\n预期: {dict(_EXPECTED_CONSUMERS)}"
    )


@pytest.mark.parametrize("status", [422, 404, 409, 500])
def test_b1_handwritten_vs_global_handler_byte_identical(api_client, status):
    """B1 字节级对拍：手写块形态与全局 handler 冒泡输出逐字节一致。

    自包含证明（不依赖删除前后时序基线）：路由 X 重现删除前的
    手写捕获转换形态，路由 Y 无捕获冒泡全局兜底；独立期望锚由
    A1 承担，两层分工防同源假绿。
    """
    from api.routes.research.common import research_error_to_http

    @api_client.app.get("/__probe/b1x")
    def probe_x_handwritten():
        try:
            raise ResearchError(code="PROBE_PARITY", message="对拍探针",
                                status=status, detail={"s": status})
        except ResearchError as exc:
            raise research_error_to_http(exc) from exc

    @api_client.app.get("/__probe/b1y")
    def probe_y_bubble():
        raise ResearchError(code="PROBE_PARITY", message="对拍探针",
                            status=status, detail={"s": status})

    rx = api_client.get("/__probe/b1x")
    ry = api_client.get("/__probe/b1y")

    assert rx.status_code == ry.status_code
    assert rx.content == ry.content
    assert (rx.headers.get("content-type")
            == ry.headers.get("content-type"))
