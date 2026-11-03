"""E2E 自管服务组 conftest（add-e2e-isolated-harness H1 重写）。

以前 e2e 依赖常驻双服务（svc.sh web:8080 + api:8000）——载旧码、并发
互踢、写生产业务库三重问题。现在 session fixture 拉起一次性双进程，
机制件在同目录 harness.py（单元用例直测对象）。两层结构：

- pytest_configure：双端口内核随机探测 + E2E_WEB_URL 无条件注入——
  早于 collection（用例模块 BASE 为模块级常量，import 期求值先于
  一切 session fixture，注入点必须前移到 configure）。
- session 级 autouse fixture 链 e2e_db → e2e_api → e2e_web（依赖序
  即启动序，teardown 逆序销毁）。

armed 形式：Popen 成功后任何失败路径先杀进程组再 raise（session
fixture setup 段 raise 则 teardown 不执行——清理责任不能依赖 teardown）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from harness import (
    E2E_STATE,
    ServiceProcess,
    assert_no_residue,
    probe_free_port,
    spawn_api_process,
    spawn_web_process,
    wait_http_ready,
)


def pytest_configure(config: pytest.Config) -> None:
    """双端口探测 + E2E_WEB_URL 无条件注入（早于 collection）。

    外部预置的 E2E_WEB_URL 覆写语义废除——自管服务组是该变量唯一
    权威来源（防用例打到常驻 8080 的全绿假象）。
    """
    E2E_STATE["api_port"] = probe_free_port()
    E2E_STATE["web_port"] = probe_free_port()
    os.environ["E2E_WEB_URL"] = f"http://127.0.0.1:{E2E_STATE['web_port']}"


@pytest.fixture(scope="session", autouse=True)
def e2e_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """临时业务库路径唯一分配器（不预建 schema——权威=api lifespan
    init_db_ext 自建+ensure_research_tables 幂等自愈）。

    文件名 e2e.db 刻意避开生产库名 quant.db（命名层消除误读面）；
    teardown 收口零残留断言（pid/端口/库三伴生——fixture 逆序保证
    此处 web/api 均已销毁）。
    """
    base_dir = tmp_path_factory.mktemp("e2e")
    db_path = base_dir / "e2e.db"
    yield db_path
    assert_no_residue(db_path)


@pytest.fixture(scope="session", autouse=True)
def e2e_api(e2e_db: Path) -> Iterator[ServiceProcess]:
    """临时 api 进程唯一所有者（起/探活/销毁；探活 /api/health 200）。"""
    service = spawn_api_process(
        api_port=E2E_STATE["api_port"],
        db_path=e2e_db,
        log_path=e2e_db.parent / "api.log",
    )
    E2E_STATE["api_pid"] = service.proc.pid
    try:
        wait_http_ready(
            f"{service.base_url}/api/health",
            service.proc,
            service.log_path,
            description="e2e api",
        )
    except Exception:
        # armed：setup 段失败 teardown 不会执行——先自清理再上抛
        service.close()
        raise
    yield service
    service.close()


@pytest.fixture(scope="session", autouse=True)
def e2e_web(e2e_api: ServiceProcess) -> Iterator[ServiceProcess]:
    """临时 web（vite dev --strictPort）进程唯一所有者（探活 / 200）。

    依赖 e2e_api=api 先就绪；VITE_API_PROXY_TARGET 指向该 api
    （/api、/socket.io、/ws 三条目同 target）。
    """
    service = spawn_web_process(
        web_port=E2E_STATE["web_port"],
        api_port=e2e_api.port,
        log_path=e2e_api.log_path.parent / "web.log",
    )
    E2E_STATE["web_pid"] = service.proc.pid
    try:
        wait_http_ready(
            f"{service.base_url}/",
            service.proc,
            service.log_path,
            description="e2e web",
        )
    except Exception:
        service.close()
        raise
    yield service
    service.close()


@pytest.fixture(scope="session")
def browser_type_launch_args():
    # Use the Playwright-managed chromium (installed via `playwright install`).
    # The previous hardcoded executable_path pointed at a Linux cache path
    # (/home/ss/.cache/.../chromium-1217/chrome-linux64) that does not exist on
    # macOS or CI; omitting it lets Playwright locate the browser itself.
    return {
        "headless": True,
    }


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 1440, "height": 900},
    }
