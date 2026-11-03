"""e2e 服务地址注入哨兵 + 隔离生效直接证据（add-e2e-isolated-harness 任务 3）。

防两类全绿假象：
①fixture 起了服务但用例实际打常驻 8080——用例模块 BASE（import 期
  求值）与 conftest 注入值比对；
②MYQUANT_DB_PATH 未生效而直写生产库——临时库文件在场=生效文件锚
  （api 探活就绪则 lifespan init_db_ext 必已建表建文件）+ api 子进程
  日志含 FAPI_STREAM_DISABLED 旁路 warning 行（服务侧行情写面旁路的
  生效直接证据）。
"""
from __future__ import annotations

import os
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
