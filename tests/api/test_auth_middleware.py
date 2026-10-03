"""A2 认证四件套特征网（CARD-32 修复锚定，V3 锚点分类：契约+边界）。

覆盖 api/auth.py 与接入面（app 中间件/CORS 白名单/realtime connect）：
- API key 三态：未设放行（A 方案）/设了缺 Bearer 401/带错 401/带对 200
- /api/health 豁免 key（Host 校验不豁免）
- Host 白名单 421（防 DNS rebinding；TestClient peer 豁免判据下经
  纯 ASGI 直调触达真实网络路径）
- CORS 白名单：四已知 origin 放行、未知 origin 拒绝、env 覆盖
- socket.io connect token 校验（设 key 才启用）
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch):
    """每用例清理认证 env，防外部环境泄漏影响判定。"""
    monkeypatch.delenv("MYQUANT_API_KEY", raising=False)
    monkeypatch.delenv("MYQUANT_CORS_ORIGINS", raising=False)
    yield


class TestApiKeyThreeStates:
    def test_no_key_allows_all(self, api_client: TestClient) -> None:
        """未设 key（A 方案缺省）：放行。"""
        response = api_client.get("/api/strategies")
        assert response.status_code == 200

    def test_key_set_missing_bearer_401(
        self, api_client: TestClient, monkeypatch,
    ) -> None:
        monkeypatch.setenv("MYQUANT_API_KEY", "secret-key-1")
        response = api_client.get("/api/strategies")
        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized"

    def test_key_set_wrong_bearer_401(
        self, api_client: TestClient, monkeypatch,
    ) -> None:
        monkeypatch.setenv("MYQUANT_API_KEY", "secret-key-1")
        response = api_client.get(
            "/api/strategies", headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401

    def test_key_set_correct_bearer_200(
        self, api_client: TestClient, monkeypatch,
    ) -> None:
        monkeypatch.setenv("MYQUANT_API_KEY", "secret-key-1")
        response = api_client.get(
            "/api/strategies", headers={"Authorization": "Bearer secret-key-1"},
        )
        assert response.status_code == 200

    def test_health_exempt_from_key(
        self, api_client: TestClient, monkeypatch,
    ) -> None:
        """/api/health 豁免 key（探活依赖），其余端点仍 401。"""
        monkeypatch.setenv("MYQUANT_API_KEY", "secret-key-1")
        assert api_client.get("/api/health").status_code == 200
        assert api_client.get("/api/strategies").status_code == 401

    def test_empty_string_key_treated_as_unset(
        self, api_client: TestClient, monkeypatch,
    ) -> None:
        """空串 key 视同未设（get_api_key or None 语义）。"""
        monkeypatch.setenv("MYQUANT_API_KEY", "")
        assert api_client.get("/api/strategies").status_code == 200


class TestHostAllowlist:
    """Host 校验经纯 ASGI 直调（TestClient peer="testclient" 按设计豁免，
    真实网络路径以伪造 IP peer 的 scope 直调触达）。"""

    @staticmethod
    def _make_scope(host: str, path: str = "/api/strategies") -> dict:
        return {
            "type": "http", "http_version": "1.1", "method": "GET",
            "path": path, "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"host", host.encode())],
            "client": ("192.168.1.77", 51000),  # 真实 TCP peer 形制
            "scheme": "http", "server": ("127.0.0.1", 8000),
        }

    def _run_middleware(self, scope: dict) -> int:
        """直调中间件捕获状态码（inner app 恒 200）。"""
        import asyncio
        from api.auth import RestAuthMiddleware
        sent: list = []

        async def _inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def _send(message):
            sent.append(message)

        async def _receive():
            return {"type": "http.request", "body": b""}

        middleware = RestAuthMiddleware(_inner)
        asyncio.run(middleware(scope, _receive, _send))
        return sent[0]["status"]

    def test_evil_host_421(self) -> None:
        status = self._run_middleware(self._make_scope("evil.example.com"))
        assert status == 421

    def test_localhost_host_passes(self) -> None:
        assert self._run_middleware(self._make_scope("localhost:8000")) == 200
        assert self._run_middleware(self._make_scope("127.0.0.1:8000")) == 200

    def test_testclient_peer_exempt_from_host_check(self) -> None:
        """TestClient peer 豁免判据：字面量 "testclient"（真实 TCP 不可
        伪造为该值——ASGI scope client 由服务器注入）。"""
        scope = self._make_scope("evil.example.com")
        scope["client"] = ("testclient", 50000)
        assert self._run_middleware(scope) == 200

    def test_no_client_info_exempt(self) -> None:
        scope = self._make_scope("localhost:8000")
        scope["client"] = None
        assert self._run_middleware(scope) == 200


class TestCorsAllowlist:
    @pytest.mark.parametrize("origin", [
        "http://localhost:8080", "http://127.0.0.1:8080",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ])
    def test_known_origins_allowed(self, api_client: TestClient, origin) -> None:
        response = api_client.options(
            "/api/strategies",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin

    def test_unknown_origin_rejected(self, api_client: TestClient) -> None:
        response = api_client.options(
            "/api/strategies",
            headers={"Origin": "http://evil.example.com",
                     "Access-Control-Request-Method": "GET"},
        )
        assert response.status_code == 400  # CORSMiddleware 拒绝非白名单
        assert "access-control-allow-origin" not in response.headers

    def test_env_override_origins(self, monkeypatch, tmp_path) -> None:
        """CORS 白名单在 create_app 装配时读取——先行设 env 自建 app。"""
        import tempfile as _tf
        from api.app import create_app
        monkeypatch.setenv("MYQUANT_CORS_ORIGINS",
                           "http://custom.example.com")
        db = tmp_path / "cors.db"
        app = create_app(db_path=db, data_dir=tmp_path)
        with TestClient(app) as client:
            response = client.options(
                "/api/strategies",
                headers={"Origin": "http://custom.example.com",
                         "Access-Control-Request-Method": "GET"},
            )
            assert response.headers.get("access-control-allow-origin") == \
                "http://custom.example.com"


class TestSocketTokenAuth:
    def test_no_key_connect_allowed(self, monkeypatch) -> None:
        from api.auth import check_socket_auth
        monkeypatch.delenv("MYQUANT_API_KEY", raising=False)
        assert check_socket_auth({"QUERY_STRING": ""}) is True

    def test_key_set_token_required(self, monkeypatch) -> None:
        from api.auth import check_socket_auth
        monkeypatch.setenv("MYQUANT_API_KEY", "sk-1")
        assert check_socket_auth({"QUERY_STRING": ""}) is False
        assert check_socket_auth(
            {"QUERY_STRING": "token=wrong"}) is False
        assert check_socket_auth(
            {"QUERY_STRING": "task_id=t1&token=sk-1"}) is True

    def test_connect_handlers_enforce_token(self, monkeypatch) -> None:
        """三 namespace connect 钩子接入 token 校验（False 拒连）。"""
        import asyncio
        from unittest.mock import patch as _patch
        from api import realtime
        monkeypatch.setenv("MYQUANT_API_KEY", "sk-1")
        with _patch.object(realtime, "sio") as sio_mock:
            result_no_token = asyncio.run(realtime._connect_evolution(
                "sid", {"QUERY_STRING": "task_id=t1"}))
            result_with = asyncio.run(realtime._connect_evolution(
                "sid", {"QUERY_STRING": "task_id=t1&token=sk-1"}))
        assert result_no_token is False
        assert result_with is None  # 放行（handler 正常路径返回 None）
