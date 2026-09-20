"""应用总入口进程组集成测试（switch-mcp-http-transport 3.3）。

覆盖：一键起（双健康）→ SIGTERM 双杀零残留 → 端口占用显式失败。
"""

import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_ports(ports, timeout_s=45):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(_port_in_use(p) for p in ports):
            return True
        time.sleep(0.5)
    return False


class TestRunEntry:
    def test_start_health_and_sigterm_shutdown(self):
        """起 → 双端口健康 → SIGTERM → 总入口与两子进程全退零残留。"""
        api_port, mcp_port = _free_port(), _free_port()
        proc = subprocess.Popen(
            ["venv/bin/python", "run.py",
             "--api-port", str(api_port), "--mcp-port", str(mcp_port)],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            assert _wait_ports([api_port, mcp_port]), \
                f"健康等待超时: api={api_port} mcp={mcp_port}"
            import httpx
            resp = httpx.get(
                f"http://127.0.0.1:{api_port}/api/data/available-sources",
                timeout=5, trust_env=False)
            assert resp.status_code == 200, "API 健康端点异常"
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                pytest.fail("总入口 SIGTERM 后未退出")

        # 零残留：端口释放 + 无孤儿进程
        time.sleep(2)
        assert not _port_in_use(api_port), "API 端口未释放（子进程残留）"
        assert not _port_in_use(mcp_port), "MCP 端口未释放（子进程残留）"
        orphans = subprocess.run(
            ["pgrep", "-f", f"mcp-port {mcp_port}"], capture_output=True)
        assert not orphans.stdout.strip(), "发现孤儿 MCP 子进程"

    def test_port_conflict_fails_explicitly(self):
        """8800 被占时总入口显式报错退出（不静默换端口）。"""
        api_port, mcp_port = _free_port(), _free_port()
        # 占住 mcp_port
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", mcp_port))
        blocker.listen(1)
        try:
            proc = subprocess.run(
                ["venv/bin/python", "run.py",
                 "--api-port", str(api_port), "--mcp-port", str(mcp_port)],
                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30)
            assert proc.returncode == 1, f"应显式失败，实得 {proc.returncode}"
            assert "已被占用" in proc.stderr, proc.stderr
        finally:
            blocker.close()
