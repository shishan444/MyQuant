"""ResearchError 全局兜底处理器测试（add-research-error-global-handler）。

A 组锚定 api-error-contract「ResearchError 出站全局兜底」requirement
的身份与状态/数据契约：A1 探针冒泡（期望独立手写，不经
research_error_to_http 生成，防同源假绿）、A2 状态透传四态参数化
（硬编码 422 的实现在 404/409/500 三参数上必红）、A3 detail=None
缺失值键在场、A4 模块尾单例注册直证。B1/C3/D1 组随对应任务落位
同文件（探针对拍/宽捕获优先级/防回潮静态锚）。
"""
from __future__ import annotations

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
