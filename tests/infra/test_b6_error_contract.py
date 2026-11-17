"""B6 错误族统一形制守护（对齐前端 G16.0 拦截器契约）。

分层契约：422=Pydantic 校验明细数组（框架默认）、409/4xx=业务语义
字符串、5xx=固定通用文案不含 detail（FastAPI 默认 Internal Server
Error，无自定义 handler——design.md B6.0 普查卡登记）。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.app import create_app
    tmp = Path(tempfile.mkdtemp())
    app = create_app(db_path=tmp / "t.db", data_dir=tmp)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestErrorContractG16:
    """G16.0 分层：可凭 status 稳定分类，无泄露。"""

    def test_404_business_string(self, client):
        r = client.get("/api/strategies/no-such-id")
        assert r.status_code == 404
        assert isinstance(r.json()["detail"], str)

    def test_422_is_pydantic_detail_array(self, client):
        """422 仅出自请求体校验——detail 为校验明细（loc/msg 结构）。"""
        r = client.post("/api/strategies", json={"missing_required": True})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, list) and detail, "422=校验明细数组（Pydantic 默认）"

    def test_5xx_fixed_generic_detail(self, client):
        """未捕获异常 → 固定通用文案（无堆栈/路径泄露）。"""
        from unittest.mock import patch

        with patch("api.routes.strategies.crud.get_strategy",
                   side_effect=RuntimeError("secret internal /path/sql")):
            r = client.get("/api/strategies/boom")
        assert r.status_code >= 500
        assert "secret" not in r.text
        assert "/path" not in r.text
