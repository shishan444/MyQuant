"""fapi 接入层测试夹具：零真实网络守卫（socket 层断言——CI 可判定）。

market-data-feed spec「SDK 依赖注入契约」Scenario：全部接入层测试以
注入替身驱动，无任何真实币安网络调用。守卫落在 socket 构造层——
任何代码路径意外发起真实连接即断言失败。
"""
import socket

import pytest


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """fapi 测试域内禁真实 socket（合成驱动纪律的机器判定）。"""

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "fapi 接入层测试禁止真实网络连接（应经注入替身驱动）")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
