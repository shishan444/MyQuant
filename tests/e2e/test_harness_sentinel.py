"""e2e 服务地址注入哨兵 + 隔离生效直接证据（add-e2e-isolated-harness 任务 3）。

防两类全绿假象：
①fixture 起了服务但用例实际打常驻 8080——用例模块 BASE（import 期
  求值）与 conftest 注入值比对；
②MYQUANT_DB_PATH 未生效而直写生产库——临时库文件在场=生效文件锚
  （api 探活就绪则 lifespan init_db_ext 必已建表建文件）+ api 子进程
  日志含 FAPI_STREAM_DISABLED 旁路 warning 行（服务侧行情写面旁路的
  生效直接证据）。

外加实时通路哨兵（spec「实时通路完整」Scenario 的常驻回归锚）：经
web 端口验证 vite proxy /socket.io 条目覆盖（polling 握手）与
websocket 转发（真 WS 客户端首帧含 sid）——条目缺失或 ws:true 被删
时红，防页面实时链路退化为断连重连循环而套件静默。
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

from harness import ServiceProcess

# 与业务用例同形制：模块级求值（import 期）——晚于 pytest_configure 注入
BASE = os.environ.get("E2E_WEB_URL", "http://localhost:8080")


def test_base_equals_injected_web_url() -> None:
    """BASE（import 期求值）== conftest 注入值且非缺省 8080。"""
    injected = os.environ["E2E_WEB_URL"]
    assert BASE == injected, "BASE 在注入前被固化——用例会打错服务"
    assert ":8080" not in BASE
    assert BASE.startswith("http://127.0.0.1:")


def test_temp_db_file_present(e2e_api: ServiceProcess, e2e_db: Path) -> None:
    """api 探活就绪后临时库文件必然在场——MYQUANT_DB_PATH 生效文件锚。"""
    assert e2e_db.exists(), (
        "api 已探活但临时库不存在——业务库落在别处（生产库危险信号）"
    )


def test_fapi_stream_bypass_warning_in_api_log(e2e_api: ServiceProcess) -> None:
    """api 子进程日志含 FAPI_STREAM_DISABLED 旁路留痕 warning 行。"""
    log = e2e_api.log_path.read_text(encoding="utf-8", errors="replace")
    assert "FAPI_STREAM_DISABLED 置位" in log, (
        "api 子进程日志无旁路留痕——行情写面旁路未生效（外网+生产 parquet 危险信号）"
    )


# 直连 opener：与 harness._DIRECT_OPENER 同因（系统代理会对 localhost
# 回 503 而非拒绝连接——127.0.0.1 直连无代理正当性，哨兵不走代理）。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_socketio_polling_handshake_via_web_proxy(e2e_web: ServiceProcess) -> None:
    """经 web 端口发起 /socket.io engine.io 握手（HTTP polling 形态）：
    200 且含 sid——proxy /socket.io 条目覆盖生效的常驻锚（spec「实时
    通路完整」THEN 前半句）。条目缺失时 vite 返回 SPA fallback 无 sid。"""
    url = f"{BASE}/socket.io/?EIO=4&transport=polling"
    with _OPENER.open(url, timeout=5) as resp:
        assert resp.status == 200
        body = resp.read().decode("utf-8", errors="replace")
    assert '"sid"' in body, "engine.io 握手无 sid——/socket.io proxy 条目未生效"


def test_socketio_websocket_first_frame_via_proxy(e2e_web: ServiceProcess) -> None:
    """经 web 端口以 transport=websocket 直连（engine.io v4 允许 WS 未经
    polling 先行握手，sid 在 WS 首帧下发），断言首帧含 sid——proxy
    `ws:true` 转发生效的直接证据（spec 裁定此面由真 WS 客户端首帧
    断言承载；curl/HTTP 层无法断言 101 升级后的帧）。"""
    from websockets.sync.client import connect

    ws_url = BASE.replace("http://", "ws://") + "/socket.io/?EIO=4&transport=websocket"
    with connect(ws_url, open_timeout=10) as ws:
        first_frame = ws.recv(timeout=10)
    assert '"sid"' in first_frame, "WS 首帧无 sid——proxy websocket 转发未生效"
