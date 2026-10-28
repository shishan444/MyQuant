"""QuantApiClient PUT 通道 unit 直构（阶段四闸门第 2 步盲区消解）。

盲区来源：CARD-05 前新增的 PUT 白名单+put 方法（api_client.py:184-
186/221-222/249-252）此前零执行——既有 update_strategy_metadata 测试
经 FakeMCP+TestClient 直绕 QuantApiClient。沿 test_errors.py 的
_FakeCtx 形制直构三面：白名单拒绝/放行、put 响应三态（204/空体/
json 体）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from mcpserver.api_client import EndpointNotAllowed, QuantApiClient


class _FakeResp:
    def __init__(self, status_code=200, content=b"{}", json_body=None):
        self.status_code = status_code
        self.content = content
        self.is_error = status_code >= 400
        self._json = json_body

    def json(self):
        return self._json


class _FakeCtx:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        return self._resp

    def post(self, url, json=None):
        return self._resp

    def put(self, url, json=None):
        self.calls.append((url, json))
        return self._resp


def _client_with(monkeypatch, ctx) -> QuantApiClient:
    import mcpserver.api_client as ac
    monkeypatch.setattr(ac.httpx, "Client", lambda **kw: ctx)
    return QuantApiClient(base_url="http://x", profile="research")


def test_put_allowed_path_passes_whitelist(monkeypatch):
    """research 档 PUT 白名单放行：/api/strategies/{id} 经 httpx put
    发出（body 透传）。"""
    ctx = _FakeCtx(_FakeResp(json_body={"ok": True}))
    api = _client_with(monkeypatch, ctx)
    out = api.put("/api/strategies/st-1", {"tags": "a,b"})
    assert out == {"ok": True}
    assert len(ctx.calls) == 1
    url, body = ctx.calls[0]
    assert url.endswith("/api/strategies/st-1") and body == {"tags": "a,b"}


def test_put_not_in_whitelist_rejected(monkeypatch):
    """白名单外 PUT 端点拒绝（EndpointNotAllowed——写面防线在方法
    分派层）。"""
    api = _client_with(monkeypatch, _FakeCtx(_FakeResp()))
    with pytest.raises(EndpointNotAllowed):
        api.put("/api/evolution/tasks/t-1", {})


def test_put_204_and_empty_body_tolerated(monkeypatch):
    """put 响应三态：204/空体容错返 None。"""
    api = _client_with(monkeypatch, _FakeCtx(_FakeResp(status_code=204)))
    assert api.put("/api/strategies/st-1", {}) is None
    api2 = _client_with(monkeypatch,
                        _FakeCtx(_FakeResp(status_code=200, content=b"")))
    assert api2.put("/api/strategies/st-1", {}) is None
