"""双启动形态 /socket.io 一致性锚定（F3/CARD-01 修复后行为）。

修复前为 D 类现状容忍锚定（-m 形态纯 FastAPI 不可达 404）——CARD-01
落地后 __main__ 消费模块级组合实例（from api.app import app），两形
态统一，本文件随正确行为改写：
- `python -m api` 形态（run.py 总入口 / scripts/start.sh）与
  `uvicorn api.app:app` 形态（根 start.sh→start_api.sh /
  scripts/api_run.py）同跑 ASGIApp(sio, other_asgi_app) 组合实例，
  engine.io 握手 200 带 sid，REST 透传正常。
- 启动器文本锚定保留（改启动形态须同步本网）。
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


def _launch_main_form_app():
    """以 `python -m api` 的真实执行语义取得 uvicorn.run 收到的 app 对象。

    runpy 以 run_name="__main__" 执行 api/__main__，uvicorn.run mock 捕获
    其 app 实参——与真实 `-m api` 启动完全同构（不跑 lifespan、不起服务）。
    """
    captured: dict = {}
    with patch("uvicorn.run", side_effect=lambda app, **kw: captured.update(app=app)):
        runpy.run_module("api.__main__", run_name="__main__")
    return captured["app"]


class TestAppObjectType:
    def test_main_form_app_is_socketio_asgi_combo(self) -> None:
        """-m 形态：uvicorn 收到模块级 ASGIApp 组合实例（F3 修复后）。"""
        app = _launch_main_form_app()
        assert isinstance(app, socketio.ASGIApp)
        assert app.engineio_server is not None

    def test_main_form_consumes_module_level_app(self) -> None:
        """__main__ 消费模块级组合实例（与 api.app:app 形态同对象）。"""
        from api.app import app as module_app
        assert _launch_main_form_app() is module_app

    def test_module_level_app_is_socketio_asgi_combo(self) -> None:
        """api.app:app 形态：模块级 app 是 ASGIApp 组合实例。"""
        from api.app import app as asgi_app
        from api.app import _fastapi_app
        assert isinstance(asgi_app, socketio.ASGIApp)
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
    """HTTP 层锚定：两形态 engine.io 握手一致可达（TestClient 不进 with，
    不触发 lifespan，避免装配后台线程与真实库）。"""

    def test_main_form_socketio_handshake_200_with_sid(self) -> None:
        """-m 形态握手 200 带 sid（修复前 404 不可达）。"""
        app = _launch_main_form_app()
        response = TestClient(app).get("/socket.io/", params=_HANDSHAKE)
        assert response.status_code == 200
        assert '"sid"' in response.text

    def test_asgi_form_socketio_handshake_200_with_sid(self) -> None:
        from api.app import app as asgi_app
        response = TestClient(asgi_app).get("/socket.io/", params=_HANDSHAKE)
        assert response.status_code == 200
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

    def test_main_module_consumes_combo_app(self) -> None:
        """F3 修复核心锚定：__main__ 消费模块级组合实例（非自建裸 FastAPI）。"""
        content = (PROJECT_ROOT / "api" / "__main__.py").read_text(encoding="utf-8")
        assert "from api.app import app" in content
        # 排除自建裸 FastAPI 的旧形（= create_app() 赋值；注释提及历史不算）
        assert "= create_app()" not in content
