"""e2e 通路件静态文本锚（add-e2e-isolated-harness H2）。

web/vite.config.ts 的 proxy 三条目是双重载体：e2e 自管服务组经
VITE_API_PROXY_TARGET 注入临时 api 地址的通路件（web 子进程 env 消费
点），兼观察面缺省行为载体（缺省 target 指常驻 8000）。文本锚锁
env 读点、缺省值与三条目在场——改 proxy 配置须同步本锚（与
TestLauncherFormPins 同族的实现权威锚定形制）。实时通路的端到端
验证由 test_harness_sentinel 的握手/WS 首帧锚承载。
"""
from __future__ import annotations

from pathlib import Path

VITE_CONFIG_TS = Path(__file__).resolve().parents[2] / "web" / "vite.config.ts"


def test_vite_proxy_env_point_and_default_target() -> None:
    """target 统一读 VITE_API_PROXY_TARGET，缺省仍指常驻 8000
    （观察面缺省行为零变化的文本面）。"""
    text = VITE_CONFIG_TS.read_text(encoding="utf-8")
    assert "process.env.VITE_API_PROXY_TARGET" in text
    assert '"http://localhost:8000"' in text, "proxy 缺省 target 漂移——观察面行为载体变化"


def test_vite_proxy_three_entries_present() -> None:
    """/api、/socket.io、/ws 三条目在场（spec：web proxy MUST 覆盖三路径）。"""
    text = VITE_CONFIG_TS.read_text(encoding="utf-8")
    for entry in ('"/api": {', '"/socket.io": {', '"/ws": {'):
        assert entry in text, f"proxy 条目缺失：{entry}"


def test_vite_proxy_websocket_entries_keep_ws_true() -> None:
    """websocket 转发条目保持 ws:true（/socket.io 实时链路与 /ws 逃生口
    至少各一处——engine.io 握手与 WS 首帧锚的端到端证据对应此配置面）。"""
    text = VITE_CONFIG_TS.read_text(encoding="utf-8")
    assert text.count("ws: true") >= 2, "ws:true 少于两处——/socket.io 或 /ws 的 WS 转发被删"
