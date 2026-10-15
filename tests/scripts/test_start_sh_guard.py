"""启动脚本假成功修复——脚本行为测试（fix-start-silent-failure B0-B11）。

工程首个 shell 行为测试。三件自起自清 fixture（B0）：
① 端口注入——bind 探测随机高位空闲端口（start.sh env 注入支持）；
② PID 文件守护——备份/恢复三 PID 文件，宿主有活服务（8000/8800 被
   占）时前置 skip（诚实 skip 优于污染）；
③ 占用进程工厂——health_hog（/api/health 返 200 的 http.server）/
   tcp_hog（纯 listener）/delayed_hog（延迟 listen，构造预检后竞态窗）。

B7 正常路径逐字节：git show main:scripts/start.sh（修复前版本）与本
分支版本在相同环境下输出对拍（PID/端口数字归一）。
"""

import os
import re
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
START = REPO / "scripts" / "start.sh"
STOP = REPO / "scripts" / "stop.sh"
RESTART = REPO / "scripts" / "restart.sh"
VENV_PY = REPO / "venv" / "bin" / "python"

PID_FILES = [REPO / ".myquant_api.pid", REPO / ".myquant_mcp.pid",
             REPO / ".myquant_web.pid"]


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture()
def ports():
    """bind 探测两个空闲高位端口（API/MCP）——env 注入下测试不触碰
    宿主固定端口服务。"""
    got = []
    holders = []
    for _ in range(2):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        got.append(s.getsockname()[1])
        holders.append(s)
    for s in holders:
        s.close()
    return {"api": got[0], "mcp": got[1]}


@pytest.fixture()
def pid_guard():
    """PID 文件接管：备份三文件（内容+存在性）后移出场——用例期间对
    PID 文件有完全控制权（宿主真服务的进程不受影响，文件 finally
    原样恢复）。测试内的清理一律自管理（直读 PID kill+rm），不经
    stop.sh——其 web 兜底段（ps 杀 vite）会误伤宿主 Web 服务。"""
    backup = {p: (p.exists(), p.read_bytes() if p.exists() else None)
              for p in PID_FILES}
    for p in PID_FILES:
        p.unlink(missing_ok=True)
    yield
    for p, (existed, content) in backup.items():
        if existed:
            p.write_bytes(content)
        else:
            p.unlink(missing_ok=True)


def _cleanup_started_services():
    """测试自管理清理：按 PID 文件杀本次拉起的进程并清文件（不经
    stop.sh——防其 web ps 兜底误伤宿主）。"""
    for p in PID_FILES:
        if p.exists():
            try:
                pid = int(p.read_text().strip())
                import signal
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    try:
                        os.kill(pid, 0)
                        time.sleep(0.1)
                    except ProcessLookupError:
                        break
            except (ValueError, ProcessLookupError, PermissionError):
                pass
            p.unlink(missing_ok=True)


def _env(ports, db_path=None):
    env = dict(os.environ)
    env["MYQUANT_API_PORT"] = str(ports["api"])
    env["MYQUANT_MCP_PORT"] = str(ports["mcp"])
    env["MYQUANT_WEB_PORT"] = str(ports["mcp"] + 1)
    if db_path is not None:
        env["MYQUANT_DB_PATH"] = str(db_path)
    return env


def _run_start(args, env, timeout=90):
    return subprocess.run(
        ["bash", str(START)] + args, env=env, capture_output=True,
        text=True, timeout=timeout)


# ── 占用进程工厂 ─────────────────────────────────────────────

_HEALTH_HOG = """
import http.server, threading

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass

http.server.HTTPServer(('127.0.0.1', {port}), H).serve_forever()
"""

_TCP_HOG = """
import socket, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', {port}))
s.listen(5)
while True:
    time.sleep(60)
"""

_DELAYED_HOG = """
import socket, time
time.sleep({delay})
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', {port}))
s.listen(5)
while True:
    time.sleep(60)
"""

_DELAYED_HEALTH_HOG = """
import http.server, time
time.sleep({delay})

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass

http.server.HTTPServer(('127.0.0.1', {port}), H).serve_forever()
"""


class _Hog:
    def __init__(self, script, port, delay=0):
        self.proc = subprocess.Popen(
            [str(VENV_PY), "-c", script.format(port=port, delay=delay)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.port = port

    def wait_listening(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _port_busy(self.port):
                return True
            time.sleep(0.1)
        return False

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture()
def hogs():
    started = []
    try:
        yield lambda script, port, delay=0: _launch(started, script, port,
                                                    delay)
    finally:
        for h in started:
            h.stop()


def _launch(started, script, port, delay):
    h = _Hog(script, port, delay)
    started.append(h)
    return h


# ── B1/B2：预检失败 ──────────────────────────────────────────

class TestPrecheck:
    def test_b1_api_precheck_fails_with_holder_clue(self, ports, pid_guard,
                                                    hogs, tmp_path):
        """B1（预检失败 API）：health_hog 占端口且无 PID 文件 → 退出码
        1、无「启动成功」、错误含占用者 PID 与命令行、PID 文件未写入。"""
        hog = hogs(_HEALTH_HOG, ports["api"])
        assert hog.wait_listening()
        r = _run_start(["-a"], _env(ports, tmp_path / "q.db"))
        assert r.returncode == 1
        assert "启动成功" not in r.stdout + r.stderr
        out = r.stdout + r.stderr
        assert "已被以下进程监听" in out
        assert str(hog.proc.pid) in out, "错误须含占用进程 PID"
        assert not (REPO / ".myquant_api.pid").exists(), "PID 文件未写入"

    def test_b2_mcp_precheck_fails(self, ports, pid_guard, hogs, tmp_path):
        """B2（预检失败 MCP）：任意监听进程占 MCP 端口 → 退出码 1。"""
        hog = hogs(_TCP_HOG, ports["mcp"])
        assert hog.wait_listening()
        r = _run_start(["-m"], _env(ports, tmp_path / "q.db"))
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "启动成功" not in out
        assert str(ports["mcp"]) in out
        assert str(hog.proc.pid) in out


# ── B3/B4：假成功路径（竞态窗构造） ──────────────────────────

class TestVerifyResponder:
    _BAD_DB = "/nonexistent-dir/q.db"  # 坏 DB 路径——子进程 lifespan 崩即秒死

    def test_b3_api_fake_success_intercepted(self, ports, pid_guard, hogs):
        """B3（change 级必过·假成功路径 API）：原始事故形态复刻——
        delayed health_hog 在预检后竞态窗内开始应答 /api/health，健康
        检查命中的不是本次进程 → verify_responder 拦截（形态一：本次
        已死+他人应答 / 形态二：本次活但非监听者——取决于子进程与 hog
        的 bind 竞速，两形态均属「假成功被拦」的验收面）：退出码 1、
        无「启动成功」、报应答者/监听者线索。"""
        hog = hogs(_DELAYED_HEALTH_HOG, ports["api"], delay=1.5)
        r = _run_start(["-a"], _env(ports, self._BAD_DB))
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "启动成功" not in out
        assert ("实际应答者是既有进程" in out
                or "监听者不是本次进程" in out), out
        assert str(hog.proc.pid) in out, "错误须含实际应答/监听者 PID"

    def test_b4_mcp_fake_success_intercepted(self, ports, pid_guard, hogs):
        """B4（假成功路径 MCP）：本次 MCP 秒死（坏档位 env——经实测
        startup 校验 ValueError 退出）+delayed tcp_hog 在预检后 listen
        ——tcp_probe 命中 hog 但本次进程死 → 形态一拦截。"""
        hog = hogs(_DELAYED_HOG, ports["mcp"], delay=1.5)
        env = _env(ports, self._BAD_DB)
        env["MYQUANT_MCP_PROFILE"] = "__bad_profile__"
        r = _run_start(["-m"], env)
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "启动成功" not in out
        assert "实际应答者是既有进程" in out or "监听者不是本次进程" in out
        assert not (REPO / ".myquant_mcp.pid").exists(), "死 PID 文件已清理"


# ── B5/B6：第三态与已死分支（文本断言层——行为面由预检/核验路径
#    联合覆盖，第三态需注入坏 config 构造、环境不可控按任务书降级） ──

class TestThirdState:
    def test_b5_exhausted_alive_exits_nonzero_textual(self):
        """B5（降级文本断言）：进程仍活分支 exit 1 在文且不含成功字样。"""
        src = START.read_text(encoding="utf-8")
        api_seg = src.split("start_api")[1].split("start_mcp")[0]
        mcp_seg = src.split("start_mcp")[1].split("start_web")[0]
        for seg, label in ((api_seg, "api"), (mcp_seg, "mcp")):
            alive = seg.split("kill -0")[1]  # 仍活分支
            assert "exit 1" in alive, f"{label} 仍活分支缺 exit 1"
            assert "启动成功" not in alive, f"{label} 仍活分支不得含成功字样"

    def test_b6_dead_branch_unchanged_textual(self):
        """B6（文本断言）：已死分支维持 error+rm PID+exit 1 既有形制。"""
        src = START.read_text(encoding="utf-8")
        for label, fn in (("API", "start_api"), ("MCP", "start_mcp")):
            seg = src.split(fn)[1].split("start_")[0]
            dead = seg.split("else")[1]
            assert "error" in dead and "exit 1" in dead
            assert "rm -f" in dead, f"{label} 已死分支 PID 文件清理在"


# ── B7：正常路径逐字节对拍（main 修复前版本 vs 本分支） ──────

class TestNormalPath:
    def test_b7_normal_path_output_identical_to_main(self, ports, pid_guard,
                                                     tmp_path):
        """B7（change 级必过）：端口空闲正常路径输出与修复前（main 版
        start.sh）逐字节一致（PID/端口数字归一）。真实启动两轮（分支
        版/main 版各一次），间以自管理清理。main 版放 scripts/ 同级
        （APP_DIR 推导依赖脚本位置——放 tmp 会指向错误的 venv）。"""
        main_sh = REPO / "scripts" / ".start_main_test.sh"
        main = subprocess.run(
            ["git", "show", "main:scripts/start.sh"],
            cwd=REPO, capture_output=True, text=True, check=True)
        main_sh.write_text(main.stdout)
        main_sh.chmod(0o755)
        try:
            self._b7_body(ports, tmp_path, main_sh)
        finally:
            main_sh.unlink(missing_ok=True)

    def _b7_body(self, ports, tmp_path, main_sh):

        def _normalize(text):
            text = re.sub(r"PID: \d+", "PID: N", text)
            text = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:P", text)
            text = re.sub(r"localhost:\d+", "localhost:P", text)
            return "\n".join(
                ln for ln in text.splitlines()
                if "启动" in ln or "地址" in ln or "文档" in ln or "端点" in ln
                or "====" in ln or "MyQuant" in ln)

        def _run_and_collect(script):
            env = _env(ports, tmp_path / "q.db")
            r = subprocess.run(
                ["bash", str(script), "-a"], env=env, capture_output=True,
                text=True, timeout=120)
            assert r.returncode == 0, r.stdout + r.stderr
            _cleanup_started_services()  # 测试自管理清理（不经 stop.sh）
            time.sleep(1)  # 端口释放窗口
            return _normalize(r.stdout)

        out_branch = _run_and_collect(START)
        out_main = _run_and_collect(main_sh)
        assert out_branch == out_main, (
            f"正常路径输出漂移:\n--- 分支 ---\n{out_branch}\n--- main ---\n{out_main}")
        assert "API 服务启动成功" in out_branch


# ── B9：PID 活跳过 ────────────────────────────────────────────

class TestPidAliveSkip:
    def test_b9_pid_alive_skips_precheck(self, ports, pid_guard, tmp_path):
        """B9：PID 文件在且进程活 → warn「已在运行」语义不变（跳过预检
        与拉起，退出码 0）——被本脚本管理的进程不算占用冲突。"""
        pid_file = REPO / ".myquant_api.pid"
        # 用一个存活的哑进程占用 PID 槽位（不监听任何端口）
        dummy = subprocess.Popen(
            ["sleep", "120"], stdout=subprocess.DEVNULL)
        pid_file.write_text(str(dummy.pid))
        try:
            r = _run_start(["-a"], _env(ports, tmp_path / "q.db"))
            assert r.returncode == 0
            out = r.stdout + r.stderr
            assert "已在运行" in out
            assert "已被以下进程监听" not in out, "管理内活进程不进预检"
        finally:
            dummy.terminate()
            dummy.wait()


# ── B10：stop/restart 回归 ────────────────────────────────────

class TestStopRestartRegression:
    def test_b10_restart_zero_change_and_propagation(self, ports, pid_guard):
        """B10（文本断言版）：restart.sh 与 main 零 diff（零改动承诺）+
        set -e 传导形制在文——start 非零退出即中止、「重启完成」不打印。
        行为面不实跑 restart 全链：stop.sh 的 web 兜底（ps 杀 vite）会
        误伤宿主 Web 服务——预检拦截行为已由 B1/B2 实证，传导语义由
        set -e + B7 真实启动闭环联合覆盖。"""
        diff = subprocess.run(
            ["git", "diff", "main", "--", "scripts/restart.sh",
             "scripts/stop.sh"], cwd=REPO, capture_output=True, text=True)
        assert diff.stdout == "", f"restart/stop 须零改动: {diff.stdout[:200]}"
        src = RESTART.read_text(encoding="utf-8")
        assert "set -e" in src, "set -e 传导前提在文"
        assert "start.sh" in src and "重启完成" in src


# ── B8/B11：编排与形制文本断言 ────────────────────────────────

class TestTextualAnchors:
    def test_b11_guard_forms_in_script(self):
        """B11（文本断言）：两函数与 -sTCP:LISTEN 形制在文；两处成功
        打印行文本未变；失败 exit 1 传导使汇总块仅在成功路径到达。"""
        src = START.read_text(encoding="utf-8")
        assert "precheck_port()" in src
        assert "verify_responder()" in src
        assert src.count("-sTCP:LISTEN") >= 4, (
            "预检/核验各两处 lsof + verify 内两处 = 至少 4 形制点")
        assert 'info "API 服务启动成功 (PID: $pid)"' in src
        assert 'info "MCP 服务启动成功 (PID: $pid)"' in src
        # verify 在打印成功之前（顺序铁律：健康→核验→打印）
        api_seg = src.split("start_api")[1].split("start_mcp")[0]
        assert api_seg.index("verify_responder") < api_seg.index("启动成功")
        mcp_seg = src.split("start_mcp")[1].split("start_web")[0]
        assert mcp_seg.index("verify_responder") < mcp_seg.index("启动成功")

    def test_b8_failure_stops_subsequent_services_textual(self):
        """B8（文本断言）：失败统一函数内 exit 1 终止脚本——main 的后续
        start_* 与汇总块仅在全成功路径到达（行为面由 B1/B10 联合覆盖：
        失败即中止、汇总不打印——B1 输出无「MyQuant 服务已启动」）。"""
        src = START.read_text(encoding="utf-8")
        assert "服务已启动" in src  # 汇总块在
        # main 内 start_api 先于 start_mcp——exit 1 使后者不执行（set -e
        # + 函数内直接 exit 的传导在 B10 行为证）
        assert src.index("start_api") < src.index("start_mcp")
