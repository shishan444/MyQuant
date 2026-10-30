"""fapi 接入层测试夹具：零真实网络守卫（socket 层断言——CI 可判定）。

market-data-feed spec「SDK 依赖注入契约」Scenario：全部接入层测试以
注入替身驱动，无任何真实币安网络调用。守卫落在 socket 构造层——
任何代码路径意外发起真实连接即断言失败。

放行面（unify-fapi-sdk）：AF_UNIX/socketpair 是 asyncio 事件循环的
内部唤醒机制（本机 IPC 基建，非外联）——新 SDK async 桥接的事件循
环线程在测试内合法运行。
"""
import socket

import pytest

_ORIGINAL_SOCKET = socket.socket


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """fapi 测试域内禁真实外联 socket（AF_UNIX 本机 IPC 放行）。"""

    def _guarded(family=socket.AF_INET, *args, **kwargs):
        if family not in (socket.AF_UNIX,):
            raise AssertionError(
                "fapi 接入层测试禁止真实网络连接（应经注入替身驱动）")
        return _ORIGINAL_SOCKET(family, *args, **kwargs)

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "fapi 接入层测试禁止真实网络连接（应经注入替身驱动）")

    monkeypatch.setattr(socket, "socket", _guarded)
    monkeypatch.setattr(socket, "create_connection", _blocked)
