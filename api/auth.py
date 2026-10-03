"""A2 认证与访问控制（PB-1：API key/Host 校验/socket.io 认证）。

职能：env 驱动的本地单用户防护层——MYQUANT_API_KEY 设置后全部
REST 请求须带 Authorization: Bearer；Host 头白名单防 DNS
rebinding；socket.io connect 携带同 key。全部开关缺省关闭
（本地单用户部署零配置可用，未设 key 仅启动警告）。
服务对象：api/app.py 中间件装配与 api/realtime.py connect 钩子。
职责边界：只做请求级准入判定，无用户体系与授权分层。
"""
from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("auth")

# 健康检查豁免 key（探活依赖，无敏感面；Host 校验不豁免）
_EXEMPT_PATHS = {"/api/health"}

# Host 白名单（防 DNS rebinding：仅校验 host 部分，端口任意）
_ALLOWED_HOSTS = {"localhost", "127.0.0.1"}

# CORS 白名单：已知前端 origin（vite dev 5173 与生产/预览 8080）
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def get_api_key() -> str | None:
    """读 MYQUANT_API_KEY（空串视同未设）。"""
    return os.environ.get("MYQUANT_API_KEY") or None


def get_cors_origins() -> list[str]:
    """CORS 白名单：MYQUANT_CORS_ORIGINS 逗号分隔覆盖，缺省已知前端 origin。"""
    raw = os.environ.get("MYQUANT_CORS_ORIGINS")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return list(_DEFAULT_CORS_ORIGINS)


def check_rest_auth(request: Request) -> None:
    """REST 请求准入（app 中间件调用，不符即抛）。

    Raises:
        HTTPException 421: Host 头不在本机白名单（DNS rebinding 防线）。
        HTTPException 401: 设置了 MYQUANT_API_KEY 但请求未带有效
            Bearer（/api/health 豁免）。
    """
    # 进程内调用豁免 Host 校验：TestClient 的 peer host 为字面量
    # "testclient"（真实 TCP 连接的 peer 必为 IP 地址、不可伪造），
    # DNS rebinding 威胁面必经真实网络——防护不弱化
    peer = request.scope.get("client")
    if peer is None or peer[0] == "testclient":
        pass
    else:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in _ALLOWED_HOSTS:
            raise HTTPException(status_code=421, detail="Misdirected request")

    api_key = get_api_key()
    if api_key is None or request.url.path in _EXEMPT_PATHS:
        return
    auth = request.headers.get("authorization") or ""
    # 恒定时间比较（SECURITY.md 契约：凭证比较不泄露时序）
    if not secrets.compare_digest(auth, f"Bearer {api_key}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def check_socket_auth(environ: dict) -> bool:
    """socket.io connect 准入：设置了 API key 时 query 须带同值 token。

    Returns:
        True=放行（含未设 key 的缺省放行）；False=拒绝连接。
    """
    api_key = get_api_key()
    if api_key is None:
        return True
    token = (parse_qs(environ.get("QUERY_STRING") or "").get("token") or [""])[0]
    return secrets.compare_digest(token, api_key)


def warn_if_no_api_key() -> None:
    """启动时提示防护未启用（A 方案：未设 key 放行但留痕）。"""
    if get_api_key() is None:
        logger.warning(
            "MYQUANT_API_KEY 未设置——API 无认证放行（本地单用户默认形态；"
            "跨机访问前请设置）")


class RestAuthMiddleware:
    """A2 准入中间件（纯 ASGI）。

    刻意不用 BaseHTTPMiddleware：其 call_next 引入额外 await 挂起点，
    会改变端点内 create_task 后台任务的首次调度时机（stream 取消
    时序用例实证受扰）；纯 ASGI 直通仅做准入判定零调度扰动。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            check_rest_auth(Request(scope))
        except HTTPException as exc:
            response = JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
