"""双启动形态 /socket.io 可达性现状锚定（F3 线索，修复期安全网）。

现状记录语义——当前行为就是：
- `python -m api` 形态（run.py 总入口 / scripts/start.sh）：uvicorn 跑
  api/__main__.py 的 create_app() 纯 FastAPI 实例，/socket.io 不可达
  （404）——evolution/trading 实时推送、连接快照、streams 协作取消在该
  形态下静默失效（sio.emit 无连接不报错）。
- `uvicorn api.app:app` 形态（根 start.sh → start_api.sh / scripts/api_run.py）：
  跑 api/app.py 模块级 ASGIApp(sio, other_asgi_app) 组合实例，/socket.io
  握手 200 可达，REST 路径透传正常。

修复卡落地（如 __main__ 改挂 ASGIApp）后，本文件由 tester 同步改为
正确行为的断言。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

import runpy
import socketio
from fastapi import FastAPI
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_HANDSHAKE = {"EIO": "4", "transport": "polling"}


def _launch_main_form_app() -> FastAPI:
    """以 `python -m api` 的真实执行语义取得 uvicorn.run 收到的 app 对象。

    runpy 以 run_name="__main__" 执行 api/__main__，uvicorn.run mock 捕获
    其 app 实参——与真实 `-m api` 启动完全同构（不跑 lifespan、不起服务）。
    """
    captured: dict = {}
    with patch("uvicorn.run", side_effect=lambda app, **kw: captured.update(app=app)):
        runpy.run_module("api.__main__", run_name="__main__")
    return captured["app"]


class TestAppObjectType:
    def test_main_form_app_is_plain_fastapi(self) -> None:
        """-m 形态：uvicorn 收到的是纯 FastAPI 实例（无 socket.io 挂载）。"""
        app = _launch_main_form_app()
        assert isinstance(app, FastAPI)
        assert not isinstance(app, socketio.ASGIApp)

    def test_module_level_app_is_socketio_asgi_combo(self) -> None:
        """api.app:app 形态：模块级 app 是 ASGIApp 组合实例。"""
        from api.app import app as asgi_app
        from api.app import _fastapi_app
        assert isinstance(asgi_app, socketio.ASGIApp)
        # 组合实例携带 engineio 服务与 REST 透传承载者
        assert asgi_app.engineio_server is not None
        assert asgi_app.other_asgi_app is _fastapi_app
        assert isinstance(_fastapi_app, FastAPI)

    def test_create_app_has_no_socketio_route(self) -> None:
        """create_app() 本身不注册任何 /socket.io 路径（挂载只在模块级组合）。"""
        from api.app import create_app
        app = create_app()
        paths = {getattr(r, "path", "") for r in app.routes}
        assert not any("socket.io" in p for p in paths)


class TestHandshakeReachability:
    """HTTP 层锚定：同一条 engine.io 握手请求在两种形态下的可达性差异。

    TestClient 不进 with（不触发 lifespan），避免装配后台线程与真实库。
    """

    def test_main_form_socketio_unreachable_404(self) -> None:
        app = _launch_main_form_app()
        response = TestClient(app).get("/socket.io/", params=_HANDSHAKE)
        assert response.status_code == 404

    def test_asgi_form_socketio_handshake_200_with_sid(self) -> None:
        from api.app import app as asgi_app
        response = TestClient(asgi_app).get("/socket.io/", params=_HANDSHAKE)
        assert response.status_code == 200
        # engine.io OPEN packet 形制：'0{"sid":...}'（前缀 0 非纯 JSON）
        assert '"sid"' in response.text

    def test_asgi_form_passes_through_rest_routes(self) -> None:
        """组合实例下 REST 路径透传正常（不受 socket.io 前置分发影响）。

        timestamp 断言锚定 HealthResponse 完整契约（CARD-29 增量：
        类体字段曾被顶错致 health 丢 timestamp，归位后防再次回归）。
        """
        from api.app import app as asgi_app
        response = TestClient(asgi_app).get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"]
        assert data["timestamp"]


class TestLauncherFormPins:
    """启动器形态文本锚定：官方路径各自走哪种形态（改启动器须同步本网）。"""

    def test_run_py_total_entry_uses_dash_m_form(self) -> None:
        """docs/MAP.md 认定的应用总入口 run.py 以 `-m api` 拉起 API 子进程。"""
        content = (PROJECT_ROOT / "run.py").read_text(encoding="utf-8")
        assert '"-m", "api"' in content

    def test_scripts_start_sh_uses_dash_m_form(self) -> None:
        """scripts/start.sh 以 `-m api` 拉起 API。"""
        content = (PROJECT_ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
        assert "-m api" in content

    def test_root_start_api_sh_uses_uvicorn_app_form(self) -> None:
        """根 start.sh → start_api.sh 以 `uvicorn api.app:app` 拉起 API。"""
        content = (PROJECT_ROOT / "start_api.sh").read_text(encoding="utf-8")
        assert "uvicorn api.app:app" in content

    def test_scripts_api_run_py_uses_uvicorn_app_form(self) -> None:
        """scripts/api_run.py 以 `uvicorn api.app:app` 拉起 API。"""
        content = (PROJECT_ROOT / "scripts" / "api_run.py").read_text(encoding="utf-8")
        assert '"api.app:app"' in content
