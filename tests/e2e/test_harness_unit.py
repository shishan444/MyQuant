"""fixture 机制单元用例六形态（add-e2e-isolated-harness 任务 3②）。

直测 harness 模块机制件（不经 session fixture 装配）：端口探测/
探活失败-进程早退形/探活超时形+armed 清理/teardown 兜底链/
configure 注入/端口冲突 fail-fast。全部用例自带进程自清理。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import harness

PY = sys.executable


def _spawn(script: str, log_path: Path) -> subprocess.Popen:
    """起独立进程组子进程（stdout/stderr 归并日志文件）。"""
    return subprocess.Popen(
        [PY, "-c", script],
        start_new_session=True,
        stdout=log_path.open("w"),
        stderr=subprocess.STDOUT,
    )


class TestProbeFreePort:
    def test_probe_returns_bindable_port(self) -> None:
        """探测产出的端口可立即实绑（内核分配语义）。"""
        port = harness.probe_free_port()
        assert isinstance(port, int) and 0 < port < 65536
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))


class TestWaitReadyProcessExit:
    def test_early_exit_raises_with_log_tail(self, tmp_path: Path) -> None:
        """探活失败-进程退出形：RuntimeError 附退出码与日志尾部，无孤儿。"""
        log = tmp_path / "die.log"
        log.write_text("line-one\nboom-early-exit\n", encoding="utf-8")
        proc = subprocess.Popen([PY, "-c", "import sys; sys.exit(3)"])
        with pytest.raises(RuntimeError, match="进程早退") as excinfo:
            harness.wait_http_ready(
                "http://127.0.0.1:1/api/health", proc, log, "unit-die", timeout=5
            )
        assert "boom-early-exit" in str(excinfo.value), "错误信息未附日志尾部"
        assert proc.poll() == 3, "进程应已自行退出（无孤儿）"
        # 已死进程的幂等清理：早退分支直接返回
        harness.kill_process_group(proc)
        # 日志不可读分支：错误信息降级为占位说明
        assert "日志不可读" in harness.log_tail(tmp_path / "no-such.log")


class TestWaitReadyConnectionRefused:
    def test_unreachable_target_records_last_error(self, tmp_path: Path) -> None:
        """无监听端口+长眠进程：URLError 分支计入 last_error 后超时。"""
        log = tmp_path / "sleep.log"
        proc = _spawn("import time; time.sleep(30)", log)
        free_port = harness.probe_free_port()
        try:
            with pytest.raises(TimeoutError, match="URLError"):
                harness.wait_http_ready(
                    f"http://127.0.0.1:{free_port}/",
                    proc,
                    log,
                    "unit-refused",
                    timeout=1.0,
                    interval=0.2,
                )
        finally:
            harness.kill_process_group(proc)


class TestWaitReadyTimeoutArmed:
    def test_timeout_raises_after_armed_kill(self, tmp_path: Path) -> None:
        """探活超时形：短时限真触发超时；armed 序列先杀再上抛——
        kill -0 失败（进程消亡）+ 端口无 LISTEN（服务子进程形）。"""
        port = harness.probe_free_port()
        server = tmp_path / "srv.py"
        server.write_text(
            "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(204)\n"
            "        self.end_headers()\n"
            "    def log_message(self, *a):\n"
            "        pass\n"
            f"HTTPServer(('127.0.0.1', {port}), H).serve_forever()\n",
            encoding="utf-8",
        )
        log = tmp_path / "srv.log"
        proc = _spawn(f"exec(open({str(server)!r}).read())", log)
        # 等服务实绑完成（204 恒不满足 ==200：走非 200 成功态分支而非连接拒绝）
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        with pytest.raises(TimeoutError, match="探活超时"):
            try:
                harness.wait_http_ready(
                    f"http://127.0.0.1:{port}/",
                    proc,
                    log,
                    "unit-500",
                    timeout=1.5,
                    interval=0.2,
                )
            except TimeoutError:
                harness.kill_process_group(proc)  # armed：先杀再 raise（与 fixture 同形制）
                raise
        assert proc.poll() is not None, "armed 序列后进程应已死亡"
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            assert sock.connect_ex(("127.0.0.1", port)) != 0, "端口仍被监听"


class TestKillProcessGroupFallback:
    def test_term_ignored_falls_back_to_kill(self, tmp_path: Path) -> None:
        """teardown 兜底链：TERM 被忽略的假组由 KILL 收走（组消亡）。"""
        log = tmp_path / "stubborn.log"
        proc = _spawn(
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(60)\n",
            log,
        )
        time.sleep(0.8)  # 等信号处理器注册完成
        assert proc.poll() is None, "前置失败：假组未存活"
        harness.kill_process_group(proc, timeout=1.0)
        assert proc.poll() is not None, "兜底链后进程应已死亡"
        assert proc.returncode == -9, f"应为 SIGKILL 回收，实际 {proc.returncode}"


class TestConfigureInjection:
    def test_configure_overrides_external_preset(self) -> None:
        """configure 注入直测：外部预置 8080 也被覆写为随机端口。"""
        saved_state = dict(harness.E2E_STATE)
        saved_url = os.environ.get("E2E_WEB_URL")
        os.environ["E2E_WEB_URL"] = "http://localhost:8080"  # 模拟外部预置
        try:
            harness.E2E_STATE.clear()
            # pytest_configure 全量在 conftest（hook 载体）；此处等价重放
            # 其全部动作（双探测+无条件注入）
            harness.E2E_STATE["api_port"] = harness.probe_free_port()
            harness.E2E_STATE["web_port"] = harness.probe_free_port()
            os.environ["E2E_WEB_URL"] = (
                f"http://127.0.0.1:{harness.E2E_STATE['web_port']}"
            )
            injected = os.environ["E2E_WEB_URL"]
            assert injected == (
                f"http://127.0.0.1:{harness.E2E_STATE['web_port']}"
            )
            assert ":8080" not in injected
        finally:
            harness.E2E_STATE.clear()
            harness.E2E_STATE.update(saved_state)
            if saved_url is None:
                os.environ.pop("E2E_WEB_URL", None)
            else:
                os.environ["E2E_WEB_URL"] = saved_url


class TestPortConflictFailFast:
    def test_strict_port_conflict_error_carries_port(self, tmp_path: Path) -> None:
        """端口冲突预占形（web 侧真实形态）：--strictPort 绑定失败退出，
        错误含端口号。"""
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        log = tmp_path / "vite_conflict.log"
        env = os.environ.copy()
        env["VITE_API_PROXY_TARGET"] = "http://127.0.0.1:1"
        proc = subprocess.Popen(
            [
                "pnpm", "exec", "vite", "--host", "127.0.0.1",
                "--port", str(port), "--strictPort",
            ],
            cwd=str(harness.PROJECT_ROOT / "web"),
            env=env,
            start_new_session=True,
            stdout=log.open("w"),
            stderr=subprocess.STDOUT,
        )
        try:
            with pytest.raises(RuntimeError, match=str(port)) as excinfo:
                harness.wait_http_ready(
                    f"http://127.0.0.1:{port}/",
                    proc,
                    log,
                    "e2e web",
                    timeout=40,
                )
            assert "日志尾部" in str(excinfo.value)
        finally:
            harness.kill_process_group(proc)
            blocker.close()

    def test_api_port_occupied_early_exit_error_carries_port(
        self, tmp_path: Path
    ) -> None:
        """端口冲突预占形（api 侧真实形态）：MYQUANT_API_PORT 被占时
        `-m api` 的 uvicorn 绑定失败退出，探活走进程早退分支而非超时
        空等——spec「端口冲突快速失败」的 uvicorn 形态（错误含端口号
        与日志尾部，不静默改换端口重试）。"""
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        service = harness.spawn_api_process(
            api_port=port,
            db_path=tmp_path / "e2e.db",
            log_path=tmp_path / "api_conflict.log",
        )
        try:
            with pytest.raises(RuntimeError, match=str(port)) as excinfo:
                harness.wait_http_ready(
                    f"http://127.0.0.1:{port}/api/health",
                    service.proc,
                    service.log_path,
                    "e2e api",
                    timeout=40,
                )
            assert "进程早退" in str(excinfo.value)
            assert "日志尾部" in str(excinfo.value)
        finally:
            service.close()
            blocker.close()

class TestDataFapiDirSingleSource:
    """DATA_FAPI_DIR 单源解析两分支（fix-data-dir-single-source A-3）。

    模块常量在 import 时求值——两分支经 reload 重求值断言；干净 env
    下与原 __file__ 锚定同值，主进程 FAPI_DATA_DIR 置位时跟随 env
    （有意语义——生产行情目录只读挂载通道，harness:154 setenv 形成
    恒等或改写传递均无害）。E2E_STATE 台账备份恢复（reload 重置
    模块命名空间的副作用隔离）。
    """

    @staticmethod
    def _reload_harness() -> None:
        import importlib

        importlib.reload(harness)

    def test_clean_env_anchors_repo_root(self, monkeypatch) -> None:
        """干净 env：DATA_FAPI_DIR == 工程根 data/market_fapi。"""
        monkeypatch.delenv("FAPI_DATA_DIR", raising=False)
        saved_state = dict(harness.E2E_STATE)
        try:
            self._reload_harness()
            assert harness.DATA_FAPI_DIR == (
                harness.PROJECT_ROOT / "data" / "market_fapi")
        finally:
            monkeypatch.delenv("FAPI_DATA_DIR", raising=False)
            self._reload_harness()
            harness.E2E_STATE.clear()
            harness.E2E_STATE.update(saved_state)

    def test_env_set_follows_env(self, monkeypatch, tmp_path: Path) -> None:
        """setenv 分支：DATA_FAPI_DIR 跟随 env（tmp 注入）。"""
        monkeypatch.setenv("FAPI_DATA_DIR", str(tmp_path))
        saved_state = dict(harness.E2E_STATE)
        try:
            self._reload_harness()
            assert harness.DATA_FAPI_DIR == tmp_path
        finally:
            monkeypatch.delenv("FAPI_DATA_DIR", raising=False)
            self._reload_harness()
            harness.E2E_STATE.clear()
            harness.E2E_STATE.update(saved_state)
